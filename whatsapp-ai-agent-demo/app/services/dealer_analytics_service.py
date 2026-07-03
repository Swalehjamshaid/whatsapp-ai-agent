"""
File: app/services/dealer_analytics_service.py
Version: 10.0 - ENTERPRISE DEALER DOMAIN AI EXPERT WITH FULL MENU
Purpose: Answer ANY dealer-related business question through a single entry point
         PostgreSQL is the ONLY source of truth.
         Full menu system with 18+ options, sub-menus, and AI-powered queries

NEW FEATURES:
- ✅ Complete Menu System (press 2 from main menu)
- ✅ 18+ Dealer Analytics Options with sub-menus
- ✅ Dealer Selection Prompts
- ✅ Comparison Flow (2 dealers)
- ✅ Ranking Display with Medals
- ✅ Quick Commands Support
- ✅ Context Memory
- ✅ Dynamic Menu Rendering
- ✅ WhatsApp-Optimized Formatting
- ✅ AI-Powered Natural Language Queries

Status: PRODUCTION READY
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
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

try:
    from geopy.distance import great_circle
except ImportError:
    great_circle = None

try:
    import openrouteservice
except ImportError:
    openrouteservice = None

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
TABLE: str = "delivery_reports"
SEPARATOR: str = "────────────────────"

# ============================================================
# BLOCK 3: CONSTANTS
# ============================================================

BUSINESS_COLUMNS: tuple[str, ...] = (
    "dn_no", "division", "customer_code", "dealer_code", "customer_name",
    "customer_model", "material_no", "sales_office", "sales_manager",
    "ship_to_city", "warehouse", "warehouse_code", "delivery_location",
    "dn_qty", "dn_amount", "dn_create_date", "good_issue_date", "pod_date",
    "delivery_status", "pgi_status", "pod_status", "pending_flag",
)

WAREHOUSE_COORDINATES: dict[str, tuple[float, float]] = {
    "rawalpindi": (33.5651, 73.0169),
    "lahore": (31.5204, 74.3587),
    "karachi": (24.8607, 67.0011),
    "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750),
    "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350),
    "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883),
    "bahawalpur": (29.3956, 71.6836),
    "dg khan": (30.0430, 70.6402),
    "sukkur": (27.7060, 68.8530),
    "rahim yar khan": (28.4200, 70.3030),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
    "islamabad": (33.6844, 73.0479),
}

DEALER_ALIASES: dict[str, str] = {
    "mian": "Mian Group Chakwal",
    "mgc": "Mian Group Chakwal",
    "taj": "Taj Electronics",
    "taj haripur": "Taj Electronics Haripur",
    "haroon": "Haroon Electronics",
    "arco": "Arco Electronics",
    "shah": "Shah Electronics",
    "national": "National Foods",
    "commercial": "Commercial Abbottabad",
    "city electronics": "City Electronics Knwl.X",
}

DEALER_SUFFIXES: tuple[str, ...] = (
    "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
    "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
    "centre", "center", "solutions", "services", "digital", "technologies",
    "systems", "networks", "communications", "logistics", "transport",
)

DEALER_NAMES: list[str] = [
    "City Electronics Knwl.X", "Mian Group Chakwal", "Taj Electronics",
    "Haroon Electronics", "Arco Electronics", "Shah Electronics",
    "National Foods", "Commercial Abbottabad",
]

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """Dealer question intent types"""
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    PRODUCTS = "products"
    PERFORMANCE = "performance"
    PENDING_DN = "pending_dn"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DELIVERY = "delivery"
    SEARCH = "search"
    COMPARISON = "comparison"
    RANKING = "ranking"
    TREND = "trend"
    FORECAST = "forecast"
    AI_SUMMARY = "ai_summary"
    DISTANCE = "distance"
    HISTORY = "history"
    CITIES = "cities"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu navigation states"""
    MAIN = "main"
    DEALER_SELECTION = "dealer_selection"
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
class DealerContext:
    """Session context for dealer queries"""
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    awaiting_dealer: bool = False
    awaiting_comparison: bool = False
    
    def set_dealer(self, dealer: str) -> None:
        self.current_dealer = dealer
    
    def get_dealer(self) -> Optional[str]:
        return self.current_dealer
    
    def clear(self) -> None:
        self.current_dealer = None
        self.current_dealer_code = None
        self.last_question = None
        self.last_intent = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_dealers = []
        self.awaiting_dealer = False
        self.awaiting_comparison = False

@dataclass
class QueryPlan:
    """Query execution plan"""
    intent: IntentType
    dealer: Optional[str] = None
    dealers: List[str] = field(default_factory=list)
    dealer_code: Optional[str] = None
    metrics: List[str] = field(default_factory=list)
    timeframe: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    format: str = "standard"
    confidence: float = 1.0
    requires_ai: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent.value,
            "dealer": self.dealer,
            "dealers": self.dealers,
            "dealer_code": self.dealer_code,
            "metrics": self.metrics,
            "timeframe": self.timeframe,
            "limit": self.limit,
            "format": self.format,
            "confidence": self.confidence,
        }

@dataclass
class DealerAnswer:
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

class DealerMenuRenderer:
    """Render dealer analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main dealer menu"""
        return "\n".join([
            "🏪 *DEALER ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Dealer Dashboard",
            "2. Dealer Revenue",
            "3. Dealer Units",
            "4. Dealer Products",
            "5. Dealer Performance",
            "6. Dealer Pending DN",
            "7. Dealer Pending PGI",
            "8. Dealer Pending POD",
            "9. Dealer Delivery",
            "10. Dealer Ranking",
            "11. Dealer Comparison",
            "12. Dealer History",
            "13. Dealer Search",
            "14. Dealer Cities",
            "15. Dealer Distance",
            "16. Dealer Trends",
            "17. Dealer Forecast",
            "18. Dealer AI Summary",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• Compare [Dealer1] and [Dealer2]",
            "• Top dealers by revenue",
            "",
            "Reply with a number or dealer name:"
        ])
    
    @staticmethod
    def render_dealer_selection(prompt: str = "Enter dealer name:") -> str:
        """Render dealer selection prompt"""
        return "\n".join([
            "🔍 *Dealer Selection*",
            "",
            prompt,
            "",
            "💡 *Examples:*",
            "City Electronics Knwl.X",
            "Mian Group Chakwal",
            "Taj Electronics",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        """Render comparison dealer selection"""
        return "\n".join([
            "🔄 *Compare Dealers*",
            "",
            "Enter first dealer name:",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_dealer_dashboard(dealer_name: str, data: Dict[str, Any]) -> str:
        """Render dealer dashboard"""
        lines = [
            f"🏪 *Dealer Dashboard - {dealer_name}*",
            "",
            "📊 *Key Metrics*",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Units: {data.get('total_units', 0):,}",
            f"DN: {data.get('total_dn', 0):,}",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            f"City: {data.get('city', 'N/A')}",
            f"Warehouse: {data.get('warehouse', 'N/A')}",
            "",
            "🚚 *Delivery*",
            f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
            f"Average Days: {data.get('avg_delivery', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Business Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            f"National Rank: #{data.get('national_rank', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Try:* 'Revenue in [dealer]' or 'Pending in [dealer]'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render dealer rankings"""
        lines = [
            f"🏆 *Dealer Rankings by {metric.title()}*",
            "",
        ]
        
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
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(dealer1: str, dealer2: str, metrics: Dict[str, Any]) -> str:
        """Render comparison result"""
        lines = [
            f"🔄 *Comparison: {dealer1} vs {dealer2}*",
            "",
            "───────────────────",
            "",
        ]
        
        metrics1 = metrics.get(f"{dealer1}_metrics", {})
        metrics2 = metrics.get(f"{dealer2}_metrics", {})
        
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
    def render_pending_list(title: str, dealers: List[Dict[str, Any]]) -> str:
        """Render pending dealer list"""
        if not dealers:
            return f"📋 *{title}*\n\nNo pending items found."
        
        lines = [f"📋 *{title}*", ""]
        for i, item in enumerate(dealers[:10], 1):
            dealer = item.get('dealer_name', 'N/A')
            pending = item.get('pending_count', 0)
            lines.append(f"{i}. {dealer}: {pending} pending")
        
        if len(dealers) > 10:
            lines.append(f"... and {len(dealers) - 10} more")
        
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
    """AI-powered intent detection for dealer questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|get).*(?:dealer|dashboard)",
            r"dealer (?:dashboard|profile|details)",
            r"show me (?:dealer|dashboard)",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income).*(?:dealer)",
            r"dealer (?:revenue|sales)",
            r"how much (?:revenue|sales)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|volume).*(?:dealer)",
            r"dealer (?:units|quantity)",
            r"how many units",
        ],
        IntentType.PRODUCTS: [
            r"(?:product|products|model|material).*(?:dealer)",
            r"dealer (?:product|products|top product)",
            r"what (?:products|models)",
        ],
        IntentType.PERFORMANCE: [
            r"(?:performance|score|rating).*(?:dealer)",
            r"dealer (?:performance|score|health)",
            r"how is (?:dealer|performance)",
        ],
        IntentType.PENDING_DN: [
            r"(?:pending|outstanding|backlog).*(?:dn|delivery).*(?:dealer)",
            r"dealer pending (?:dn|orders)",
            r"pending deliveries",
        ],
        IntentType.PENDING_PGI: [
            r"(?:pending pgi|pgi pending).*(?:dealer)",
            r"dealer pending pgi",
        ],
        IntentType.PENDING_POD: [
            r"(?:pending pod|pod pending).*(?:dealer)",
            r"dealer pending pod",
        ],
        IntentType.DELIVERY: [
            r"(?:delivery|transit|shipping).*(?:dealer)",
            r"dealer (?:delivery|transit)",
            r"delivery performance",
        ],
        IntentType.SEARCH: [
            r"(?:search|find|lookup).*(?:dealer)",
            r"search (?:dealer|dealers)",
            r"find dealer",
        ],
        IntentType.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        IntentType.RANKING: [
            r"(?:top|best|highest).*(?:dealer|dealers)",
            r"dealer (?:ranking|rank|leaderboard)",
            r"top dealers",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|change).*(?:dealer)",
            r"dealer (?:trend|growth|change)",
        ],
        IntentType.FORECAST: [
            r"(?:forecast|predict|future).*(?:dealer)",
            r"dealer (?:forecast|projection)",
        ],
        IntentType.AI_SUMMARY: [
            r"(?:summary|overview|explain).*(?:dealer)",
            r"dealer (?:summary|overview|explain)",
            r"tell me about dealer",
        ],
        IntentType.DISTANCE: [
            r"(?:distance|travel|driving).*(?:dealer)",
            r"dealer (?:distance|warehouse distance)",
            r"how far",
        ],
        IntentType.HISTORY: [
            r"(?:history|timeline|past).*(?:dealer)",
            r"dealer (?:history|timeline)",
        ],
        IntentType.CITIES: [
            r"(?:city|cities|location).*(?:dealer)",
            r"dealer (?:city|cities|locations)",
            r"where is dealer",
        ],
        IntentType.MENU: [
            r"menu",
            r"dealer menu",
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
                    Route(name="dealer_dashboard", utterances=[
                        "dealer dashboard", "show dealer", "dealer details"
                    ]),
                    Route(name="dealer_revenue", utterances=[
                        "dealer revenue", "dealer sales", "revenue for dealer"
                    ]),
                    Route(name="dealer_units", utterances=[
                        "dealer units", "units sold", "dealer quantity"
                    ]),
                    Route(name="dealer_products", utterances=[
                        "dealer products", "top products", "dealer models"
                    ]),
                    Route(name="dealer_performance", utterances=[
                        "dealer performance", "dealer score", "dealer health"
                    ]),
                    Route(name="dealer_pending", utterances=[
                        "dealer pending", "pending orders", "dealer backlog"
                    ]),
                    Route(name="dealer_comparison", utterances=[
                        "compare dealers", "dealer vs dealer", "comparison"
                    ]),
                    Route(name="dealer_ranking", utterances=[
                        "top dealers", "dealer ranking", "best dealers"
                    ]),
                    Route(name="dealer_summary", utterances=[
                        "dealer summary", "dealer overview", "tell me about dealer"
                    ]),
                ]
                self._semantic_router = Router(routes=routes, encoder=HuggingFaceEncoder())
                logger.info("✅ Semantic router initialized")
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
        if question_lower in ["menu", "dealer menu", "options", "help", "show menu"]:
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
                    intent_name = result.name.replace("dealer_", "")
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
                elif keyword in ["summary", "overview", "explain"]:
                    best_intent = IntentType.AI_SUMMARY
                    best_score = 0.5
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 9: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for dealer questions"""
    
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
            "dealers": [],
            "dealer_codes": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_dealers": [],
            "requires_comparison": False,
        }
        
        # Extract dealer names
        dealers = self._extract_dealers(question_lower)
        if dealers:
            entities["dealers"] = dealers
        
        # Extract dealer codes
        dealer_codes = self._extract_dealer_codes(question_lower)
        if dealer_codes:
            entities["dealer_codes"] = dealer_codes
        
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
            if len(entities["dealers"]) >= 2:
                entities["comparison_dealers"] = entities["dealers"][:2]
        
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
    
    def _extract_dealers(self, text: str) -> List[str]:
        """Extract dealer names from text"""
        found = []
        
        # Direct matches
        for dealer in DEALER_NAMES:
            if dealer.lower() in text:
                found.append(dealer)
        
        # Check for dealer with suffix
        for suffix in DEALER_SUFFIXES:
            pattern = rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)'
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                if name and len(name) > 2 and name not in found:
                    found.append(name)
        
        # Check for quoted dealer names
        match = re.search(r'"([^"]+)"', text)
        if match:
            found.append(match.group(1))
        
        return found
    
    def _extract_dealer_codes(self, text: str) -> List[str]:
        """Extract dealer codes from text"""
        # Dealer codes are typically alphanumeric, 3-10 characters
        matches = re.findall(r'\b([A-Z0-9]{3,10})\b', text.upper())
        return matches
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income"],
            "units": ["units", "quantity", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "delivery": ["delivery", "transit", "shipping"],
            "pgi": ["pgi", "goods issue"],
            "pod": ["pod", "proof of delivery"],
            "performance": ["performance", "score", "rating"],
            "products": ["product", "products", "model", "material"],
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
            r"(\d+)\s+(?:dealers|items)",
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
# BLOCK 10: DISTANCE SERVICE
# ============================================================

class DistanceService:
    """Distance calculation for dealer-warehouse relationship"""
    
    def __init__(self):
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def calculate(self, warehouse: str, dealer_city: str) -> Dict[str, Any]:
        """Calculate distance between warehouse and dealer city"""
        key = f"{warehouse.lower()}|{dealer_city.lower()}"
        
        with self._lock:
            if key in self._cache:
                return self._cache[key].copy()
        
        warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse.lower())
        city_coord = WAREHOUSE_COORDINATES.get(dealer_city.lower())
        
        result = {
            "distance_km": None,
            "driving_time": "Unknown",
            "estimated_delivery": "Unknown",
            "source": "unavailable"
        }
        
        if warehouse_coord and city_coord:
            lat1, lon1 = warehouse_coord
            lat2, lon2 = city_coord
            
            R = 6371
            dlat = math.radians(lat2 - lat1)
            dlon = math.radians(lon2 - lon1)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
            c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
            distance = R * c
            
            result["distance_km"] = round(distance, 1)
            result["source"] = "haversine"
            
            hours = distance / 50
            if hours < 1:
                result["driving_time"] = f"{int(hours * 60)} Minutes"
            else:
                result["driving_time"] = f"{int(hours)} Hours {int((hours % 1) * 60)} Minutes"
            
            if distance <= 80:
                result["estimated_delivery"] = "Same Day"
            elif distance <= 200:
                result["estimated_delivery"] = "Next Day"
            elif distance <= 400:
                result["estimated_delivery"] = "1-2 Days"
            elif distance <= 700:
                result["estimated_delivery"] = "2-3 Days"
            else:
                result["estimated_delivery"] = "3-5 Days"
        
        with self._lock:
            self._cache[key] = result.copy()
        
        return result

# ============================================================
# BLOCK 11: DEALER DASHBOARD BUILDER
# ============================================================

class DealerDashboardBuilder:
    """Build dealer dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self.distance_service = DistanceService()
    
    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for dealer"""
        cache_key = dealer_identifier.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            # Try dealer name first, then dealer code, then customer code
            conditions = [
                func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                DeliveryReport.dealer_code == dealer_identifier,
                DeliveryReport.customer_code == dealer_identifier,
            ]
            
            query = self.session.query(
                func.max(DeliveryReport.customer_name).label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.division).label("division"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending_dn"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pod_pending_dn"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
                func.max(DeliveryReport.pod_date).label("latest_pod_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod_success"),
            ).filter(or_(*conditions)).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code
            ).first()
            
            if not query:
                return None
            
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            completed_dn = int(query.completed_dn or 0)
            pod_success = int(query.pod_success or 0)
            
            dashboard = {
                "dealer_name": _text(query.dealer_name),
                "dealer_code": _text(query.dealer_code),
                "customer_code": _text(query.customer_code),
                "city": _text(query.city),
                "delivery_location": _text(query.delivery_location),
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "division": _text(query.division),
                "total_dn": total_dn,
                "completed_dn": completed_dn,
                "pending_dn": pending_dn,
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "pgi_pending_dn": int(query.pgi_pending_dn or 0),
                "pod_pending_dn": int(query.pod_pending_dn or 0),
                "first_delivery_date": _date_text(query.first_delivery_date),
                "latest_delivery_date": _date_text(query.latest_delivery_date),
                "latest_pgi_date": _date_text(query.latest_pgi_date),
                "latest_pod_date": _date_text(query.latest_pod_date),
                "avg_delivery": _days(query.avg_delivery),
                "avg_pod": _days(query.avg_pod),
                "avg_cycle": _days(query.avg_cycle),
                "delivery_success_pct": _percent(completed_dn, total_dn) if total_dn > 0 else 0,
                "pending_pct": _percent(pending_dn, total_dn) if total_dn > 0 else 0,
                "pgi_success_pct": _percent(pgi_pending_dn or 0, total_dn) if total_dn > 0 else 0,
                "pod_success_pct": _percent(pod_success, total_dn) if total_dn > 0 else 0,
                "avg_units_per_dn": round(_number(query.total_units) / total_dn, 2) if total_dn > 0 else 0,
                "avg_revenue_per_dn": round(_number(query.total_revenue) / total_dn, 2) if total_dn > 0 else 0,
            }
            
            # Distance
            distance = self.distance_service.calculate(
                _text(query.warehouse),
                _text(query.city)
            )
            dashboard["distance"] = distance
            
            # Calculate business score
            score = (
                dashboard["delivery_success_pct"] * 0.25 +
                (100 - dashboard["pending_pct"]) * 0.25 +
                min(100, dashboard["avg_units_per_dn"] * 10) * 0.15 +
                min(100, dashboard["avg_revenue_per_dn"] / 1000) * 0.15 +
                50
            )
            dashboard["business_score"] = round(min(100, max(0, score)), 1)
            dashboard["risk_score"] = round(100 - dashboard["business_score"], 1)
            
            # Status
            if dashboard["business_score"] >= 85:
                dashboard["overall_status"] = "Excellent"
                dashboard["performance_grade"] = "A"
            elif dashboard["business_score"] >= 70:
                dashboard["overall_status"] = "Good"
                dashboard["performance_grade"] = "B"
            elif dashboard["business_score"] >= 50:
                dashboard["overall_status"] = "Watch"
                dashboard["performance_grade"] = "C"
            else:
                dashboard["overall_status"] = "Critical"
                dashboard["performance_grade"] = "D"
            
            # Monthly analytics
            monthly = self._get_monthly_analytics(dealer_identifier)
            if monthly:
                dashboard.update(monthly)
            
            # Product analytics
            products = self._get_product_analytics(dealer_identifier)
            if products:
                dashboard.update(products)
            
            # Pending analytics
            pending = self._get_pending_analytics(dealer_identifier)
            if pending:
                dashboard.update(pending)
            
            # Generate insights and recommendations
            dashboard["insights"] = self._generate_insights(dashboard)
            dashboard["recommendations"] = self._generate_recommendations(dashboard)
            dashboard["executive_summary"] = self._generate_executive_summary(dashboard)
            
            with self._lock:
                self._cache[cache_key] = dashboard.copy()
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for dealer {dealer_identifier}: {e}")
            return None
    
    def _get_monthly_analytics(self, dealer_identifier: str) -> Dict[str, Any]:
        """Get monthly analytics"""
        try:
            conditions = [
                func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                DeliveryReport.dealer_code == dealer_identifier,
                DeliveryReport.customer_code == dealer_identifier,
            ]
            
            monthly = self.session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(or_(*conditions), DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
            
            if not monthly:
                return {}
            
            current = date.today().strftime("%Y-%m")
            prev_date = date.today().replace(day=1) - timedelta(days=1)
            previous = prev_date.strftime("%Y-%m")
            
            current_row = next((r for r in monthly if r.month == current), None)
            previous_row = next((r for r in monthly if r.month == previous), None)
            
            current_revenue = _number(current_row.revenue) if current_row else 0.0
            previous_revenue = _number(previous_row.revenue) if previous_row else 0.0
            
            best = max(monthly, key=lambda r: _number(r.revenue))
            worst = min(monthly, key=lambda r: _number(r.revenue))
            
            return {
                "current_month_revenue": round(current_revenue, 2),
                "previous_month_revenue": round(previous_revenue, 2),
                "monthly_growth": _growth(current_revenue, previous_revenue),
                "current_month_units": int(current_row.units) if current_row else 0,
                "previous_month_units": int(previous_row.units) if previous_row else 0,
                "current_month_dn": int(current_row.dns) if current_row else 0,
                "previous_month_dn": int(previous_row.dns) if previous_row else 0,
                "best_month": _text(best.month),
                "worst_month": _text(worst.month),
                "busiest_month": _text(best.month),
                "revenue_growth_pct": _growth(current_revenue, previous_revenue),
            }
        except Exception:
            return {}
    
    def _get_product_analytics(self, dealer_identifier: str) -> Dict[str, Any]:
        """Get product analytics"""
        try:
            conditions = [
                func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                DeliveryReport.dealer_code == dealer_identifier,
                DeliveryReport.customer_code == dealer_identifier,
            ]
            
            top_model = self.session.query(
                DeliveryReport.customer_model.label("model"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(or_(*conditions), DeliveryReport.customer_model.isnot(None)).group_by(
                DeliveryReport.customer_model
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            top_material = self.session.query(
                DeliveryReport.material_no.label("material"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(or_(*conditions), DeliveryReport.material_no.isnot(None)).group_by(
                DeliveryReport.material_no
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            return {
                "top_product": _text(top_model.model) if top_model else "Unknown",
                "top_model": _text(top_model.model) if top_model else "Unknown",
                "top_material": _text(top_material.material) if top_material else "Unknown",
                "highest_revenue_product": _text(top_model.model) if top_model else "Unknown",
            }
        except Exception:
            return {}
    
    def _get_pending_analytics(self, dealer_identifier: str) -> Dict[str, Any]:
        """Get pending analytics"""
        try:
            conditions = [
                func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                DeliveryReport.dealer_code == dealer_identifier,
                DeliveryReport.customer_code == dealer_identifier,
            ]
            
            pending_rows = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            ).filter(
                or_(*conditions),
                or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
            ).group_by(DeliveryReport.dn_no, DeliveryReport.dn_create_date).all()
            
            if not pending_rows:
                return {}
            
            today = date.today()
            ages = []
            total_revenue = 0.0
            total_units = 0
            
            for row in pending_rows:
                if row.dn_create_date:
                    age = (today - row.dn_create_date).days
                    ages.append(age)
                total_revenue += _number(row.revenue)
                total_units += _number(row.units)
            
            oldest = min(pending_rows, key=lambda r: r.dn_create_date or date.max)
            avg_age = sum(ages) / len(ages) if ages else 0
            
            return {
                "pending_revenue": round(total_revenue, 2),
                "pending_units": int(total_units),
                "pending_average_days": round(avg_age, 2),
                "critical_pending": sum(1 for age in ages if age > 7),
                "overdue_pending": sum(1 for age in ages if age > 14),
                "oldest_pending_dn": _text(oldest.dn_no),
                "oldest_pending_days": max(ages) if ages else 0,
            }
        except Exception:
            return {}
    
    def _generate_insights(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate insights from dashboard"""
        insights = []
        
        revenue = dashboard.get('total_revenue', 0)
        growth = dashboard.get('monthly_growth', 0)
        pending = dashboard.get('pending_dn', 0)
        score = dashboard.get('business_score', 0)
        delivery = dashboard.get('delivery_success_pct', 0)
        
        if revenue > 0 and growth > 10:
            insights.append(f"Revenue is growing strongly at {growth:+.1f}%")
        elif revenue > 0 and growth < -10:
            insights.append(f"Revenue is declining at {growth:+.1f}%. Needs attention.")
        
        if pending == 0:
            insights.append("No pending orders - excellent operational efficiency")
        elif pending < 10:
            insights.append(f"Low pending orders: {pending}")
        else:
            insights.append(f"High pending orders: {pending}. Priority for resolution.")
        
        if score >= 85:
            insights.append(f"Excellent business score of {score:.1f}/100")
        elif score >= 70:
            insights.append(f"Good business score of {score:.1f}/100")
        elif score < 50:
            insights.append(f"Critical business score of {score:.1f}/100. Immediate action required.")
        
        if delivery >= 95:
            insights.append("Outstanding delivery performance")
        elif delivery >= 85:
            insights.append("Good delivery performance")
        elif delivery < 70:
            insights.append("Delivery performance needs improvement")
        
        if not insights:
            insights.append("Performance is stable. Continue monitoring.")
        
        return insights
    
    def _generate_recommendations(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate recommendations from dashboard"""
        recommendations = []
        
        pending = dashboard.get('pending_dn', 0)
        delivery = dashboard.get('delivery_success_pct', 0)
        score = dashboard.get('business_score', 0)
        pod = dashboard.get('pod_success_pct', 0)
        
        if pending > 20:
            recommendations.append(f"Escalate {pending} pending DNs for resolution")
        elif pending > 10:
            recommendations.append("Review pending orders for timely closure")
        
        if delivery < 80:
            recommendations.append("Improve delivery speed and reliability")
        
        if score < 70:
            recommendations.append("Develop action plan to improve business score")
        
        if pod < 85:
            recommendations.append("Focus on POD collection and completion")
        
        if not recommendations:
            recommendations.append("Maintain current performance levels")
            recommendations.append("Continue monitoring key metrics")
        
        return recommendations
    
    def _generate_executive_summary(self, dashboard: Dict[str, Any]) -> str:
        """Generate executive summary"""
        dealer = dashboard.get('dealer_name', 'Dealer')
        revenue = dashboard.get('total_revenue', 0)
        growth = dashboard.get('monthly_growth', 0)
        pending = dashboard.get('pending_dn', 0)
        score = dashboard.get('business_score', 0)
        status = dashboard.get('overall_status', 'Unknown')
        
        if growth >= 0:
            trend = "growing"
        else:
            trend = "declining"
        
        if score >= 70:
            action = "maintain current controls"
        else:
            action = "prioritize pending DN and POD closure"
        
        return (
            f"{dealer} is {trend} with a {score:.1f}/100 business score. "
            f"Revenue is PKR {revenue:,.2f} with {pending} pending DNs. "
            f"Delivery success is {dashboard.get('delivery_success_pct', 0):.1f}%. "
            f"Recommendation: {action}."
        )

# ============================================================
# BLOCK 12: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def __init__(self):
        self._menu_renderer = DealerMenuRenderer()
    
    def format(self, answer: DealerAnswer) -> str:
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
    
    def _format_metric(self, answer: DealerAnswer) -> str:
        """Single metric format"""
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 *{dealer}*"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        if answer.explanation:
            lines.append("")
            lines.append(answer.explanation)
        
        return "\n".join(lines)
    
    def _format_compact(self, answer: DealerAnswer) -> str:
        """Compact format"""
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 {dealer}"]
        lines.append("")
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_standard(self, answer: DealerAnswer) -> str:
        """Standard format"""
        return self._menu_renderer.render_dealer_dashboard(
            answer.plan.dealer or "Dealer",
            answer.dashboard or {}
        )
    
    def _format_executive(self, answer: DealerAnswer) -> str:
        """Executive summary format"""
        dealer = answer.plan.dealer or "Dealer"
        lines = [
            f"📋 *Executive Summary - {dealer}*",
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
    
    def _format_detailed(self, answer: DealerAnswer) -> str:
        """Detailed format"""
        dealer = answer.plan.dealer or "Dealer"
        lines = [
            f"📊 *Detailed Analysis - {dealer}*",
            "",
            "📍 *Location*",
            "─" * 40,
        ]
        
        if answer.dashboard:
            lines.append(f"City: {answer.dashboard.get('city', 'N/A')}")
            lines.append(f"Warehouse: {answer.dashboard.get('warehouse', 'N/A')}")
            lines.append(f"Sales Office: {answer.dashboard.get('sales_office', 'N/A')}")
        
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
    
    def _format_kpi_only(self, answer: DealerAnswer) -> str:
        """KPI-only format"""
        dealer = answer.plan.dealer or "Dealer"
        lines = [f"📊 *{dealer} KPIs*:"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"  {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_comparison(self, answer: DealerAnswer) -> str:
        """Comparison format"""
        return self._menu_renderer.render_comparison_result(
            answer.plan.dealers[0] if answer.plan.dealers else "",
            answer.plan.dealers[1] if len(answer.plan.dealers) > 1 else "",
            answer.metrics
        )
    
    def _format_ranking(self, answer: DealerAnswer) -> str:
        """Ranking format"""
        ranking_data = answer.metrics.get("ranking", [])
        return self._menu_renderer.render_ranking(ranking_data, answer.plan.sort_by or "revenue", answer.plan.limit)

# ============================================================
# BLOCK 13: MAIN DEALER ANALYTICS SERVICE WITH MENU
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Domain AI Expert with Full Menu System
    Single entry point for all dealer-related business questions
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "10.0.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = DealerMenuRenderer()
        self._formatter = ResponseFormatter()
        
        # Context memory
        self._contexts: Dict[str, DealerContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ DealerAnalyticsService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅")
        logger.info(f"   Source of Truth: PostgreSQL")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main dealer menu"""
        return self._menu_renderer.render_main_menu()
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response
        
        Returns:
            {
                "response": str,           # WhatsApp message
                "menu_type": str,          # "dealer_menu"
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
        elif context.menu_state == MenuState.DEALER_SELECTION:
            return self._handle_dealer_selection(context, user_input)
        elif context.menu_state == MenuState.COMPARISON_SELECTION:
            return self._handle_comparison_selection(context, user_input)
        
        # Default: treat as quick query
        return self._handle_quick_query(context, user_input)
    
    def _handle_main_menu_return(self, context: DealerContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_dealers = []
        context.awaiting_dealer = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "dealer_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True  # Exit to main AI Logistics menu
        }
    
    def _handle_main_menu_option(self, context: DealerContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("dashboard", "Enter dealer name for dashboard:"),
            "2": ("revenue", "Enter dealer name for revenue:"),
            "3": ("units", "Enter dealer name for units:"),
            "4": ("products", "Enter dealer name for products:"),
            "5": ("performance", "Enter dealer name for performance:"),
            "6": ("pending_dn", "Enter dealer name for pending DN:"),
            "7": ("pending_pgi", "Enter dealer name for pending PGI:"),
            "8": ("pending_pod", "Enter dealer name for pending POD:"),
            "9": ("delivery", "Enter dealer name for delivery:"),
            "10": ("ranking", None),  # Special handling
            "11": ("comparison", None),  # Special handling
            "12": ("history", "Enter dealer name for history:"),
            "13": ("search", None),  # Special handling
            "14": ("cities", "Enter dealer name for cities:"),
            "15": ("distance", "Enter dealer name for distance:"),
            "16": ("trends", "Enter dealer name for trends:"),
            "17": ("forecast", "Enter dealer name for forecast:"),
            "18": ("ai_summary", "Enter dealer name for AI summary:"),
        }
        
        if option == "10":
            return self._handle_ranking_request(context)
        elif option == "11":
            return self._handle_comparison_start(context)
        elif option == "13":
            return self._handle_search_start(context)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        # Check if we already have a selected dealer
        if context.current_dealer:
            result = self._execute_dealer_action(context, action, context.current_dealer)
            result["exit_menu"] = False
            return result
        
        # Ask for dealer
        context.menu_state = MenuState.DEALER_SELECTION
        context.selected_option = action
        context.awaiting_dealer = True
        
        return {
            "response": self._menu_renderer.render_dealer_selection(prompt),
            "menu_type": "dealer_menu",
            "action": "dealer_selection",
            "data": {"purpose": action},
            "exit_menu": False
        }
    
    def _handle_dealer_selection(self, context: DealerContext, dealer_input: str) -> Dict[str, Any]:
        """Handle dealer selection response"""
        dealer_name = self._resolve_dealer_name(dealer_input)
        if not dealer_name:
            return {
                "response": "\n".join([
                    "❌ Dealer not found.",
                    "",
                    "Please try again or enter a valid dealer name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dealer_menu",
                "action": "dealer_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_dealer = dealer_name
        context.menu_state = MenuState.MAIN
        context.awaiting_dealer = False
        
        action = context.selected_option or "dashboard"
        result = self._execute_dealer_action(context, action, dealer_name)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: DealerContext, dealer_input: str) -> Dict[str, Any]:
        """Handle comparison dealer selection"""
        dealer_name = self._resolve_dealer_name(dealer_input)
        if not dealer_name:
            return {
                "response": "\n".join([
                    "❌ Dealer not found.",
                    "",
                    "Please try again or enter a valid dealer name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dealer_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_dealers.append(dealer_name)
        
        if len(context.comparison_dealers) == 1:
            return {
                "response": "\n".join([
                    f"✅ First dealer selected: {dealer_name}",
                    "",
                    "Enter second dealer name:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dealer_menu",
                "action": "comparison_second",
                "data": {"first_dealer": dealer_name},
                "exit_menu": False
            }
        else:
            dealer1, dealer2 = context.comparison_dealers[0], context.comparison_dealers[1]
            context.menu_state = MenuState.MAIN
            context.comparison_dealers = []
            return self._perform_comparison(context, dealer1, dealer2)
    
    def _handle_ranking_request(self, context: DealerContext) -> Dict[str, Any]:
        """Handle ranking request"""
        result = self._get_dealer_ranking(context)
        result["exit_menu"] = False
        return result
    
    def _handle_search_start(self, context: DealerContext) -> Dict[str, Any]:
        """Start search"""
        context.menu_state = MenuState.DEALER_SELECTION
        context.selected_option = "search"
        context.awaiting_dealer = True
        
        return {
            "response": "\n".join([
                "🔍 *Search Dealers*",
                "",
                "Enter dealer name or code:",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dealer_menu",
            "action": "search_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_comparison_start(self, context: DealerContext) -> Dict[str, Any]:
        """Start comparison process"""
        context.menu_state = MenuState.COMPARISON_SELECTION
        context.comparison_dealers = []
        return {
            "response": self._menu_renderer.render_comparison_selection(),
            "menu_type": "dealer_menu",
            "action": "comparison_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_quick_query(self, context: DealerContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            import re
            dealers = re.findall(r'([\w\s]+?)(?:and|vs|versus)([\w\s]+)', query, re.IGNORECASE)
            if dealers:
                dealer1 = self._resolve_dealer_name(dealers[0][0].strip())
                dealer2 = self._resolve_dealer_name(dealers[0][1].strip())
                if dealer1 and dealer2:
                    return self._perform_comparison(context, dealer1, dealer2)
        
        # Check if it's a valid dealer name
        dealer_name = self._resolve_dealer_name(query)
        if dealer_name:
            context.current_dealer = dealer_name
            return self._get_dealer_dashboard(context, dealer_name)
        
        # Check if it's a ranking query
        if "top" in query.lower() and ("dealer" in query.lower() or "dealers" in query.lower()):
            return self._get_dealer_ranking(context)
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• 'City Electronics' - Show dashboard",
                "• 'Revenue in City Electronics'",
                "• 'Pending in Mian Group'",
                "• 'Compare City Electronics and Mian Group'",
                "• 'Top dealers by revenue'",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dealer_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _execute_dealer_action(self, context: DealerContext, action: str, dealer_name: str) -> Dict[str, Any]:
        """Execute dealer action based on selected option"""
        action_map = {
            "dashboard": self._get_dealer_dashboard,
            "revenue": self._get_dealer_metric,
            "units": self._get_dealer_metric,
            "products": self._get_dealer_products,
            "performance": self._get_dealer_performance,
            "pending_dn": self._get_dealer_pending_dn,
            "pending_pgi": self._get_dealer_pending_pgi,
            "pending_pod": self._get_dealer_pending_pod,
            "delivery": self._get_dealer_delivery,
            "history": self._get_dealer_history,
            "cities": self._get_dealer_cities,
            "distance": self._get_dealer_distance,
            "trends": self._get_dealer_trends,
            "forecast": self._get_dealer_forecast,
            "ai_summary": self._get_dealer_ai_summary,
        }
        
        handler = action_map.get(action, self._get_dealer_dashboard)
        
        if action in ["revenue", "units"]:
            return handler(context, dealer_name, action)
        else:
            return handler(context, dealer_name)
    
    def _resolve_dealer_name(self, input_text: str) -> Optional[str]:
        """Resolve dealer name from input"""
        input_lower = input_text.lower().strip()
        
        # Direct match
        for dealer in DEALER_NAMES:
            if dealer.lower() == input_lower:
                return dealer
        
        # Check aliases
        for alias, dealer in DEALER_ALIASES.items():
            if alias in input_lower:
                return dealer
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            matches = process.extract(input_lower, DEALER_NAMES, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= 85:
                return matches[0][0]
        
        # Partial match
        for dealer in DEALER_NAMES:
            if len(input_lower) >= 3:
                if input_lower[:3] in dealer.lower() or dealer.lower()[:3] in input_lower:
                    return dealer
        
        return None
    
    def _get_context(self, session_id: str) -> DealerContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext()
            return self._contexts[session_id]
    
    # ============================================================
    # DEALER OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_dealer_dashboard(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer dashboard"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\nPlease check the dealer name and try again.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "dashboard",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_dealer_dashboard(dealer_name, dashboard),
                    "menu_type": "dealer_menu",
                    "action": "dashboard",
                    "data": {"dealer": dealer_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Service error for {dealer_name}: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_metric(self, context: DealerContext, dealer_name: str, metric: str) -> Dict[str, Any]:
        """Get specific dealer metric"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "metric_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                metric_mapping = {
                    "revenue": ("Revenue", f"PKR {dashboard.get('total_revenue', 0):,.2f}"),
                    "units": ("Units", f"{dashboard.get('total_units', 0):,}"),
                }
                
                label, value = metric_mapping.get(metric, ("Metric", "N/A"))
                
                return {
                    "response": "\n".join([
                        f"📊 *{dealer_name} - {label}*",
                        "",
                        f"{value}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": f"metric_{metric}",
                    "data": {"dealer": dealer_name, "metric": metric, "value": value},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_products(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer products"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "products_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"🏷️ *Products - {dealer_name}*",
                        "",
                        f"Top Product: {dashboard.get('top_product', 'N/A')}",
                        f"Top Model: {dashboard.get('top_model', 'N/A')}",
                        f"Top Material: {dashboard.get('top_material', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "products",
                    "data": {"dealer": dealer_name, "products": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_performance(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer performance"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "performance_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📈 *Performance - {dealer_name}*",
                        "",
                        f"Score: {dashboard.get('business_score', 0):.1f}/100",
                        f"Status: {dashboard.get('overall_status', 'Unknown')}",
                        f"Grade: {dashboard.get('performance_grade', 'N/A')}",
                        f"Risk Score: {dashboard.get('risk_score', 0):.1f}/100",
                        "",
                        f"Delivery Success: {dashboard.get('delivery_success_pct', 0):.1f}%",
                        f"POD Success: {dashboard.get('pod_success_pct', 0):.1f}%",
                        f"Pending Rate: {dashboard.get('pending_pct', 0):.1f}%",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "performance",
                    "data": {"dealer": dealer_name, "performance": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_pending_dn(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer pending DN"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "pending_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                pending_data = {
                    "pending_dn": dashboard.get('pending_dn', 0),
                    "pending_revenue": dashboard.get('pending_revenue', 0),
                    "pending_units": dashboard.get('pending_units', 0),
                    "pgi_pending_dn": dashboard.get('pgi_pending_dn', 0),
                    "pod_pending_dn": dashboard.get('pod_pending_dn', 0),
                    "pending_average_days": dashboard.get('pending_average_days', 0),
                    "critical_pending": dashboard.get('critical_pending', 0),
                    "overdue_pending": dashboard.get('overdue_pending', 0),
                    "oldest_pending_dn": dashboard.get('oldest_pending_dn', 'N/A'),
                    "oldest_pending_days": dashboard.get('oldest_pending_days', 0),
                }
                
                return {
                    "response": "\n".join([
                        f"⏳ *Pending DN - {dealer_name}*",
                        "",
                        f"Pending DN: {pending_data['pending_dn']:,}",
                        f"Pending Revenue: PKR {pending_data['pending_revenue']:,.2f}",
                        f"Pending Units: {pending_data['pending_units']:,}",
                        f"PGI Pending: {pending_data['pgi_pending_dn']:,}",
                        f"POD Pending: {pending_data['pod_pending_dn']:,}",
                        "",
                        f"Avg Days: {pending_data['pending_average_days']:.1f}",
                        f"Critical (>7 days): {pending_data['critical_pending']:,}",
                        f"Overdue (>14 days): {pending_data['overdue_pending']:,}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "pending_dn",
                    "data": {"dealer": dealer_name, "pending": pending_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_pending_pgi(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer pending PGI"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "pgi_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending PGI - {dealer_name}*\n\nPending PGI: {dashboard.get('pgi_pending_dn', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "dealer_menu",
                    "action": "pending_pgi",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_pending_pod(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer pending POD"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "pod_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending POD - {dealer_name}*\n\nPending POD: {dashboard.get('pod_pending_dn', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "dealer_menu",
                    "action": "pending_pod",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_delivery(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer delivery"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "delivery_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                delivery_data = {
                    "delivery_success_pct": dashboard.get('delivery_success_pct', 0),
                    "avg_delivery": dashboard.get('avg_delivery', 0),
                    "pod_success_pct": dashboard.get('pod_success_pct', 0),
                    "avg_pod": dashboard.get('avg_pod', 0),
                    "avg_cycle": dashboard.get('avg_cycle', 0),
                }
                
                return {
                    "response": "\n".join([
                        f"🚚 *Delivery - {dealer_name}*",
                        "",
                        f"Success Rate: {delivery_data['delivery_success_pct']:.1f}%",
                        f"Average Days: {delivery_data['avg_delivery']:.1f}",
                        f"POD Success: {delivery_data['pod_success_pct']:.1f}%",
                        f"POD Average: {delivery_data['avg_pod']:.1f} Days",
                        f"Cycle Time: {delivery_data['avg_cycle']:.1f} Days",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "delivery",
                    "data": {"dealer": dealer_name, "delivery": delivery_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_history(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer history"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "history_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📋 *History - {dealer_name}*",
                        "",
                        f"First DN: {dashboard.get('first_delivery_date', 'N/A')}",
                        f"Latest DN: {dashboard.get('latest_delivery_date', 'N/A')}",
                        f"Latest PGI: {dashboard.get('latest_pgi_date', 'N/A')}",
                        f"Latest POD: {dashboard.get('latest_pod_date', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "history",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_cities(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer cities"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "cities_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📍 *Cities - {dealer_name}*",
                        "",
                        f"City: {dashboard.get('city', 'N/A')}",
                        f"Delivery Location: {dashboard.get('delivery_location', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "cities",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_distance(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer distance"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "distance_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                distance = dashboard.get('distance', {})
                
                return {
                    "response": "\n".join([
                        f"📍 *Distance - {dealer_name}*",
                        "",
                        f"Warehouse: {dashboard.get('warehouse', 'N/A')}",
                        f"Distance: {distance.get('distance_km', 'N/A')} KM",
                        f"Driving Time: {distance.get('driving_time', 'N/A')}",
                        f"Est. Delivery: {distance.get('estimated_delivery', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "distance",
                    "data": {"dealer": dealer_name, "distance": distance},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_trends(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer trends"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "trends_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📈 *Trends - {dealer_name}*",
                        "",
                        f"Monthly Growth: {dashboard.get('monthly_growth', 0):+.1f}%",
                        f"Revenue Growth: {dashboard.get('revenue_growth_pct', 0):+.1f}%",
                        "",
                        f"Current Month Revenue: PKR {dashboard.get('current_month_revenue', 0):,.2f}",
                        f"Previous Month Revenue: PKR {dashboard.get('previous_month_revenue', 0):,.2f}",
                        "",
                        f"Best Month: {dashboard.get('best_month', 'N/A')}",
                        f"Worst Month: {dashboard.get('worst_month', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "trends",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_forecast(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer forecast"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "forecast_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                # Simple forecast based on current month and growth
                revenue = dashboard.get('current_month_revenue', 0)
                growth = dashboard.get('monthly_growth', 0)
                forecast_revenue = revenue * (1 + growth / 100)
                
                return {
                    "response": "\n".join([
                        f"🔮 *Forecast - {dealer_name}*",
                        "",
                        f"Current Revenue: PKR {revenue:,.2f}",
                        f"Growth Rate: {growth:+.1f}%",
                        f"Next Month Forecast: PKR {forecast_revenue:,.2f}",
                        "",
                        "📌 *Based on current month data*",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "forecast",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_ai_summary(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer AI summary"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "summary_error",
                        "data": {"dealer": dealer_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📋 *AI Summary - {dealer_name}*",
                        "",
                        dashboard.get('executive_summary', 'Summary not available.'),
                        "",
                        "━━━━━━━━━━━━━━━━━━",
                        "",
                        f"Status: {dashboard.get('overall_status', 'Unknown')}",
                        f"Score: {dashboard.get('business_score', 0):.1f}/100",
                        f"Grade: {dashboard.get('performance_grade', 'N/A')}",
                        "",
                        f"Revenue: PKR {dashboard.get('total_revenue', 0):,.2f}",
                        f"Growth: {dashboard.get('monthly_growth', 0):+.1f}%",
                        f"Pending: {dashboard.get('pending_dn', 0):,} DN",
                        "",
                        "💡 *Key Insights*",
                        "\n".join(f"• {insight}" for insight in dashboard.get('insights', [])[:3]),
                        "",
                        "🎯 *Recommendations*",
                        "\n".join(f"• {rec}" for rec in dashboard.get('recommendations', [])[:3]),
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "dealer_menu",
                    "action": "summary",
                    "data": {"dealer": dealer_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dealer_ranking(self, context: DealerContext) -> Dict[str, Any]:
        """Get dealer rankings"""
        try:
            with self._session() as session:
                results = session.query(
                    func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue")
                ).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).group_by(
                    DeliveryReport.customer_name
                ).order_by(
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).desc()
                ).limit(10).all()
                
                ranking = []
                for row in results:
                    dealer = _text(row.dealer)
                    if dealer:
                        ranking.append({
                            "dealer": dealer,
                            "value": f"PKR {float(row.revenue or 0):,.2f}"
                        })
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "dealer_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Ranking error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_comparison(self, context: DealerContext, dealer1: str, dealer2: str) -> Dict[str, Any]:
        """Perform dealer comparison"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dash1 = builder.build(dealer1)
                dash2 = builder.build(dealer2)
                
                if not dash1 or not dash2:
                    return {
                        "response": "⚠️ One or both dealers not found.\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "comparison_error",
                        "data": {"error": "not_found"},
                        "exit_menu": False
                    }
                
                metrics = {}
                
                metrics[f"{dealer1}_metrics"] = {
                    "Revenue": f"PKR {dash1.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash1.get('total_units', 0):,}",
                    "DN": f"{dash1.get('total_dn', 0):,}",
                    "Pending": f"{dash1.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash1.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash1.get('business_score', 0):.1f}/100",
                }
                
                metrics[f"{dealer2}_metrics"] = {
                    "Revenue": f"PKR {dash2.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash2.get('total_units', 0):,}",
                    "DN": f"{dash2.get('total_dn', 0):,}",
                    "Pending": f"{dash2.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash2.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash2.get('business_score', 0):.1f}/100",
                }
                
                revenue1 = dash1.get('total_revenue', 0)
                revenue2 = dash2.get('total_revenue', 0)
                
                if revenue1 > revenue2:
                    explanation = f"{dealer1} has higher revenue than {dealer2}"
                elif revenue2 > revenue1:
                    explanation = f"{dealer2} has higher revenue than {dealer1}"
                else:
                    explanation = f"{dealer1} and {dealer2} have similar revenue"
                
                metrics["explanation"] = explanation
                
                return {
                    "response": self._menu_renderer.render_comparison_result(dealer1, dealer2, metrics),
                    "menu_type": "dealer_menu",
                    "action": "comparison",
                    "data": {"dealer1": dealer1, "dealer2": dealer2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Comparison error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _search_dealers(self, context: DealerContext, query: str) -> Dict[str, Any]:
        """Search dealers"""
        try:
            with self._session() as session:
                search_pattern = f"%{query}%"
                results = session.query(
                    func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
                    func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
                    func.max(DeliveryReport.ship_to_city).label("city"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                ).filter(
                    or_(
                        DeliveryReport.customer_name.ilike(search_pattern),
                        DeliveryReport.dealer_code.ilike(search_pattern),
                        DeliveryReport.customer_code.ilike(search_pattern),
                    )
                ).group_by(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code
                ).order_by(
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).desc()
                ).limit(20).all()
                
                if not results:
                    return {
                        "response": f"🔍 No results found for '{query}'\n\n0. Main Menu",
                        "menu_type": "dealer_menu",
                        "action": "search",
                        "data": {"query": query, "results": []},
                        "exit_menu": False
                    }
                
                lines = [f"🔍 *Search Results for '{query}'*", ""]
                for i, row in enumerate(results[:10], 1):
                    dealer = _text(row.dealer_name)
                    code = _text(row.dealer_code)
                    city = _text(row.city)
                    revenue = float(row.revenue or 0)
                    lines.append(f"{i}. {dealer} (Code: {code})")
                    lines.append(f"   City: {city} | Revenue: PKR {revenue:,.2f}")
                    lines.append("")
                
                if len(results) > 10:
                    lines.append(f"... and {len(results) - 10} more")
                
                lines.extend([
                    "",
                    "0. Main Menu",
                    "99. Back"
                ])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "dealer_menu",
                    "action": "search",
                    "data": {"query": query, "results": results},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Search error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    # ============================================================
    # LEGACY METHODS - BACKWARD COMPATIBILITY
    # ============================================================
    
    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not dealer_name:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide a dealer name.",
                "error": "DEALER_REQUIRED"
            }
        
        context = DealerContext()
        result = self._get_dealer_dashboard(context, dealer_name)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_top_dealers(self, limit: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = DealerContext()
        result = self._get_dealer_ranking(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("ranking", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def compare_dealers(self, dealers: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not dealers or len(dealers) < 2:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide at least two dealers.",
                "error": "TWO_DEALERS_REQUIRED"
            }
        
        context = DealerContext()
        result = self._perform_comparison(context, dealers[0], dealers[1])
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
                dealers = session.query(func.count(distinct(DeliveryReport.customer_name))).scalar() or 0
            
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
                "dealers": int(dealers),
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

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()


def get_dealer_analytics_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


def process_dealer_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process dealer menu input for WhatsApp integration"""
    service = get_dealer_analytics_service()
    return service.process_menu_input(session_id, user_input)


def get_dealer_main_menu() -> str:
    """Get the main dealer menu for WhatsApp"""
    service = get_dealer_analytics_service()
    return service.get_main_menu()


# ============================================================
# BLOCK 15: EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_dealer_analytics_service",
    "process_dealer_menu",
    "get_dealer_main_menu",
    "DealerMenuRenderer",
    "get_dealer_dashboard",
    "get_top_dealers",
    "compare_dealers",
    "health_check",
]
