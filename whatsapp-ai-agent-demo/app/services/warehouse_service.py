"""
File: app/services/warehouse_service.py
Version: 2.0 - ENTERPRISE WAREHOUSE DOMAIN AI EXPERT WITH FULL MENU
Purpose: Answer ANY warehouse-related business question through a single entry point
         PostgreSQL is the ONLY source of truth.
         Full menu system with 18+ options, sub-menus, and AI-powered queries

NEW FEATURES:
- ✅ Complete Menu System (press 4 from main menu)
- ✅ 18+ Warehouse Analytics Options with sub-menus
- ✅ Warehouse Selection Prompts
- ✅ Comparison Flow (2 warehouses)
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

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("WAREHOUSE_ANALYTICS_CACHE_TTL", "300")))
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

WAREHOUSE_NAMES: list[str] = [
    "lahore", "karachi", "rawalpindi", "multan", "peshawar",
    "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
    "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
    "abbottabad", "gwadar", "gilgit", "islamabad"
]

WAREHOUSE_ALIASES: dict[str, str] = {
    "lhr": "lahore",
    "khi": "karachi",
    "rwp": "rawalpindi",
    "isb": "islamabad",
    "fsd": "faisalabad",
    "hyd": "hyderabad",
    "skt": "sialkot",
    "guj": "gujranwala",
    "bwp": "bahawalpur",
    "skr": "sukkur",
}

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """Warehouse question intent types"""
    DASHBOARD = "dashboard"
    INVENTORY = "inventory"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING_DN = "pending_dn"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DELIVERY = "delivery"
    PERFORMANCE = "performance"
    TOP_PRODUCTS = "top_products"
    RANKING = "ranking"
    COMPARISON = "comparison"
    UTILIZATION = "utilization"
    AGING = "aging"
    DEALER_DISTRIBUTION = "dealer_distribution"
    CITY_DISTRIBUTION = "city_distribution"
    TRANSIT = "transit"
    SUMMARY = "summary"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu navigation states"""
    MAIN = "main"
    WAREHOUSE_SELECTION = "warehouse_selection"
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
class WarehouseContext:
    """Session context for warehouse queries"""
    current_warehouse: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_warehouses: List[str] = field(default_factory=list)
    awaiting_warehouse: bool = False
    awaiting_comparison: bool = False
    
    def set_warehouse(self, warehouse: str) -> None:
        self.current_warehouse = warehouse
    
    def get_warehouse(self) -> Optional[str]:
        return self.current_warehouse
    
    def clear(self) -> None:
        self.current_warehouse = None
        self.last_question = None
        self.last_intent = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_warehouses = []
        self.awaiting_warehouse = False
        self.awaiting_comparison = False

@dataclass
class QueryPlan:
    """Query execution plan"""
    intent: IntentType
    warehouse: Optional[str] = None
    warehouses: List[str] = field(default_factory=list)
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
            "warehouse": self.warehouse,
            "warehouses": self.warehouses,
            "metrics": self.metrics,
            "timeframe": self.timeframe,
            "limit": self.limit,
            "format": self.format,
            "confidence": self.confidence,
        }

@dataclass
class WarehouseAnswer:
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

class WarehouseMenuRenderer:
    """Render warehouse analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main warehouse menu"""
        return "\n".join([
            "🏭 *WAREHOUSE ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Warehouse Dashboard",
            "2. Warehouse Inventory",
            "3. Warehouse Revenue",
            "4. Warehouse Units",
            "5. Pending DN",
            "6. Pending PGI",
            "7. Pending POD",
            "8. Delivery Performance",
            "9. Warehouse Ranking",
            "10. Warehouse Comparison",
            "11. Top Products",
            "12. Dealer Distribution",
            "13. City Distribution",
            "14. Storage Utilization",
            "15. Transit Analysis",
            "16. Delivery Aging",
            "17. Warehouse KPIs",
            "18. Warehouse AI Summary",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type warehouse name for dashboard",
            "• Compare Lahore and Karachi",
            "• Top warehouses by revenue",
            "",
            "Reply with a number or warehouse name:"
        ])
    
    @staticmethod
    def render_warehouse_selection(prompt: str = "Enter warehouse name:") -> str:
        """Render warehouse selection prompt"""
        return "\n".join([
            "🔍 *Warehouse Selection*",
            "",
            prompt,
            "",
            "💡 *Available Warehouses:*",
            "Lahore, Karachi, Rawalpindi, Islamabad, Multan",
            "Peshawar, Quetta, Faisalabad, Hyderabad, Sialkot",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        """Render comparison warehouse selection"""
        return "\n".join([
            "🔄 *Compare Warehouses*",
            "",
            "Enter first warehouse name:",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_warehouse_dashboard(warehouse_name: str, data: Dict[str, Any]) -> str:
        """Render warehouse dashboard"""
        lines = [
            f"🏭 *Warehouse Dashboard - {warehouse_name.title()}*",
            "",
            "📊 *Key Metrics*",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Units: {data.get('total_units', 0):,}",
            f"DN: {data.get('total_dn', 0):,}",
            f"Dealers: {data.get('total_dealers', 0):,}",
            f"Cities: {data.get('total_cities', 0):,}",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            "",
            "🚚 *Delivery*",
            f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
            f"Average Days: {data.get('avg_delivery', 0):.1f}",
            f"Transit Days: {data.get('transit_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Business Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Try:* 'Revenue in [warehouse]' or 'Pending in [warehouse]'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render warehouse rankings"""
        lines = [
            f"🏆 *Warehouse Rankings by {metric.title()}*",
            "",
        ]
        
        for i, item in enumerate(ranking[:limit], 1):
            warehouse = item.get('warehouse', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {warehouse.title()}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(warehouse1: str, warehouse2: str, metrics: Dict[str, Any]) -> str:
        """Render comparison result"""
        lines = [
            f"🔄 *Comparison: {warehouse1.title()} vs {warehouse2.title()}*",
            "",
            "───────────────────",
            "",
        ]
        
        metrics1 = metrics.get(f"{warehouse1}_metrics", {})
        metrics2 = metrics.get(f"{warehouse2}_metrics", {})
        
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
    def render_pending_list(title: str, items: List[Dict[str, Any]]) -> str:
        """Render pending list"""
        if not items:
            return f"📋 *{title}*\n\nNo pending items found."
        
        lines = [f"📋 *{title}*", ""]
        for i, item in enumerate(items[:10], 1):
            name = item.get('name', 'N/A')
            count = item.get('count', 0)
            lines.append(f"{i}. {name}: {count} pending")
        
        if len(items) > 10:
            lines.append(f"... and {len(items) - 10} more")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_distribution(title: str, items: List[Dict[str, Any]], limit: int = 10) -> str:
        """Render distribution"""
        if not items:
            return f"📊 *{title}*\n\nNo data available."
        
        lines = [f"📊 *{title}*", ""]
        for i, item in enumerate(items[:limit], 1):
            name = item.get('name', 'N/A')
            value = item.get('value', 0)
            pct = item.get('percentage', 0)
            lines.append(f"{i}. {name}: {value:,} ({pct:.1f}%)")
        
        if len(items) > limit:
            lines.append(f"... and {len(items) - limit} more")
        
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
    """AI-powered intent detection for warehouse questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|get).*(?:warehouse|dashboard)",
            r"warehouse (?:dashboard|profile|details)",
            r"show me (?:warehouse|dashboard)",
        ],
        IntentType.INVENTORY: [
            r"(?:inventory|stock|supply).*(?:warehouse)",
            r"warehouse (?:inventory|stock)",
            r"how much inventory",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income).*(?:warehouse)",
            r"warehouse (?:revenue|sales)",
            r"how much (?:revenue|sales)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|volume).*(?:warehouse)",
            r"warehouse (?:units|quantity)",
            r"how many units",
        ],
        IntentType.PENDING_DN: [
            r"(?:pending|outstanding|backlog).*(?:dn|delivery).*(?:warehouse)",
            r"warehouse pending (?:dn|orders)",
            r"pending deliveries",
        ],
        IntentType.PENDING_PGI: [
            r"(?:pending pgi|pgi pending).*(?:warehouse)",
            r"warehouse pending pgi",
        ],
        IntentType.PENDING_POD: [
            r"(?:pending pod|pod pending).*(?:warehouse)",
            r"warehouse pending pod",
        ],
        IntentType.DELIVERY: [
            r"(?:delivery|transit|shipping).*(?:warehouse)",
            r"warehouse (?:delivery|transit)",
            r"delivery performance",
        ],
        IntentType.PERFORMANCE: [
            r"(?:performance|score|efficiency).*(?:warehouse)",
            r"warehouse (?:performance|efficiency)",
            r"how is warehouse",
        ],
        IntentType.TOP_PRODUCTS: [
            r"(?:top products|product mix).*(?:warehouse)",
            r"warehouse (?:top products|products)",
            r"what products",
        ],
        IntentType.RANKING: [
            r"(?:top|best|highest).*(?:warehouse|warehouses)",
            r"warehouse (?:ranking|rank|leaderboard)",
            r"top warehouses",
        ],
        IntentType.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        IntentType.UTILIZATION: [
            r"(?:utilization|capacity|storage).*(?:warehouse)",
            r"warehouse (?:utilization|capacity)",
            r"storage utilization",
        ],
        IntentType.AGING: [
            r"(?:aging|delay|waiting).*(?:warehouse)",
            r"warehouse (?:aging|delivery aging)",
            r"delivery delay",
        ],
        IntentType.DEALER_DISTRIBUTION: [
            r"(?:dealer distribution|dealers served).*(?:warehouse)",
            r"warehouse dealers",
            r"dealer distribution",
        ],
        IntentType.CITY_DISTRIBUTION: [
            r"(?:city distribution|cities served).*(?:warehouse)",
            r"warehouse cities",
            r"city distribution",
        ],
        IntentType.TRANSIT: [
            r"(?:transit|travel|journey).*(?:warehouse)",
            r"warehouse (?:transit|travel)",
            r"transit time",
        ],
        IntentType.SUMMARY: [
            r"(?:summary|overview|explain).*(?:warehouse)",
            r"warehouse (?:summary|overview)",
            r"tell me about warehouse",
        ],
        IntentType.MENU: [
            r"menu",
            r"warehouse menu",
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
                    Route(name="warehouse_dashboard", utterances=[
                        "warehouse dashboard", "show warehouse", "warehouse details"
                    ]),
                    Route(name="warehouse_revenue", utterances=[
                        "warehouse revenue", "warehouse sales", "revenue for warehouse"
                    ]),
                    Route(name="warehouse_inventory", utterances=[
                        "warehouse inventory", "warehouse stock", "inventory level"
                    ]),
                    Route(name="warehouse_pending", utterances=[
                        "warehouse pending", "pending orders", "warehouse backlog"
                    ]),
                    Route(name="warehouse_comparison", utterances=[
                        "compare warehouses", "warehouse vs warehouse", "comparison"
                    ]),
                    Route(name="warehouse_ranking", utterances=[
                        "top warehouses", "warehouse ranking", "best warehouses"
                    ]),
                    Route(name="warehouse_summary", utterances=[
                        "warehouse summary", "warehouse overview", "tell me about warehouse"
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
        if question_lower in ["menu", "warehouse menu", "options", "help", "show menu"]:
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
                    intent_name = result.name.replace("warehouse_", "")
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
                elif keyword in ["inventory", "stock"]:
                    best_intent = IntentType.INVENTORY
                    best_score = 0.5
                    break
                elif keyword in ["summary", "overview", "explain"]:
                    best_intent = IntentType.SUMMARY
                    best_score = 0.5
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 9: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for warehouse questions"""
    
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
            "warehouses": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_warehouses": [],
            "requires_comparison": False,
        }
        
        # Extract warehouse names
        warehouses = self._extract_warehouses(question_lower)
        if warehouses:
            entities["warehouses"] = warehouses
        
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
            if len(entities["warehouses"]) >= 2:
                entities["comparison_warehouses"] = entities["warehouses"][:2]
        
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
    
    def _extract_warehouses(self, text: str) -> List[str]:
        """Extract warehouse names from text"""
        found = []
        
        # Direct matches
        for warehouse in WAREHOUSE_NAMES:
            if warehouse in text:
                found.append(warehouse)
        
        # Alias matches
        for alias, warehouse in WAREHOUSE_ALIASES.items():
            if alias in text and warehouse not in found:
                found.append(warehouse)
        
        # Fuzzy match for partials
        if not found and RAPIDFUZZ_AVAILABLE:
            for warehouse in WAREHOUSE_NAMES:
                if len(warehouse) >= 3:
                    if warehouse[:3] in text or warehouse[:4] in text:
                        found.append(warehouse)
        
        return found
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income"],
            "units": ["units", "quantity", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "delivery": ["delivery", "transit", "shipping"],
            "inventory": ["inventory", "stock", "supply"],
            "performance": ["performance", "score", "efficiency"],
            "products": ["products", "items", "goods"],
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
            r"(\d+)\s+(?:warehouses|items)",
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
# BLOCK 10: WAREHOUSE REPOSITORY
# ============================================================

class WarehouseRepository:
    """
    Warehouse repository - PostgreSQL is the ONLY source of truth.
    """
    
    @classmethod
    def warehouse_filter(cls, warehouse_name: str) -> Any:
        token = warehouse_name.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.warehouse)) == token.lower(),
            func.lower(func.trim(DeliveryReport.warehouse_code)) == token.lower(),
        )
    
    @classmethod
    def _aggregate_sql(cls, where: str = "TRUE", order_by: str = "total_revenue DESC") -> str:
        return f"""
            SELECT 
                warehouse,
                warehouse_code,
                sales_office,
                sales_manager,
                COUNT(DISTINCT dn_no) AS total_dn,
                COALESCE(SUM(dn_qty), 0) AS total_units,
                COALESCE(SUM(dn_amount), 0) AS total_revenue,
                COUNT(DISTINCT customer_name) AS total_dealers,
                COUNT(DISTINCT ship_to_city) AS total_cities,
                COUNT(DISTINCT material_no) AS total_products,
                COUNT(DISTINCT dn_no) FILTER (WHERE pod_date IS NULL OR pending_flag = true) AS pending_dn,
                COUNT(DISTINCT dn_no) FILTER (WHERE good_issue_date IS NULL) AS pending_pgi,
                COUNT(DISTINCT dn_no) FILTER (WHERE good_issue_date IS NOT NULL AND pod_date IS NULL) AS pending_pod,
                MIN(dn_create_date) AS first_delivery_date,
                MAX(dn_create_date) AS latest_delivery_date,
                MAX(good_issue_date) AS latest_pgi_date,
                MAX(pod_date) AS latest_pod_date,
                AVG(EXTRACT(EPOCH FROM (good_issue_date - dn_create_date)) / 86400) AS avg_delivery,
                AVG(EXTRACT(EPOCH FROM (pod_date - good_issue_date)) / 86400) AS avg_pod,
                AVG(EXTRACT(EPOCH FROM (pod_date - dn_create_date)) / 86400) AS avg_cycle,
                COUNT(DISTINCT dn_no) FILTER (WHERE pod_date IS NOT NULL) AS completed_dn
            FROM {TABLE}
            WHERE {where}
            GROUP BY warehouse, warehouse_code, sales_office, sales_manager
            ORDER BY {order_by}
        """

# ============================================================
# BLOCK 11: WAREHOUSE DASHBOARD BUILDER
# ============================================================

class WarehouseDashboardBuilder:
    """Build warehouse dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def build(self, warehouse_name: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for warehouse"""
        cache_key = warehouse_name.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            condition = WarehouseRepository.warehouse_filter(warehouse_name)
            
            query = self.session.query(
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("total_cities"),
                func.count(distinct(DeliveryReport.material_no)).label("total_products"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pending_pgi"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_pod"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
                func.max(DeliveryReport.pod_date).label("latest_pod_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            ).filter(condition).group_by(
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).first()
            
            if not query:
                return None
            
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            completed_dn = int(query.completed_dn or 0)
            
            dashboard = {
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "total_dn": total_dn,
                "completed_dn": completed_dn,
                "pending_dn": pending_dn,
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "total_dealers": int(query.total_dealers or 0),
                "total_cities": int(query.total_cities or 0),
                "total_products": int(query.total_products or 0),
                "pending_pgi": int(query.pending_pgi or 0),
                "pending_pod": int(query.pending_pod or 0),
                "first_delivery_date": _date_text(query.first_delivery_date),
                "latest_delivery_date": _date_text(query.latest_delivery_date),
                "latest_pgi_date": _date_text(query.latest_pgi_date),
                "latest_pod_date": _date_text(query.latest_pod_date),
                "avg_delivery": _days(query.avg_delivery),
                "avg_pod": _days(query.avg_pod),
                "avg_cycle": _days(query.avg_cycle),
                "delivery_success_pct": _percent(completed_dn, total_dn) if total_dn > 0 else 0,
                "pending_pct": _percent(pending_dn, total_dn) if total_dn > 0 else 0,
                "transit_days": _days(query.avg_delivery),
                "avg_units_per_dn": round(_number(query.total_units) / total_dn, 2) if total_dn > 0 else 0,
                "avg_revenue_per_dn": round(_number(query.total_revenue) / total_dn, 2) if total_dn > 0 else 0,
            }
            
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
            monthly = self._get_monthly_analytics(warehouse_name)
            if monthly:
                dashboard.update(monthly)
            
            # Top products
            products = self._get_top_products(warehouse_name)
            if products:
                dashboard["top_products"] = products
            
            # Generate insights and recommendations
            dashboard["insights"] = self._generate_insights(dashboard)
            dashboard["recommendations"] = self._generate_recommendations(dashboard)
            dashboard["executive_summary"] = self._generate_executive_summary(dashboard)
            
            with self._lock:
                self._cache[cache_key] = dashboard.copy()
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for warehouse {warehouse_name}: {e}")
            return None
    
    def _get_monthly_analytics(self, warehouse_name: str) -> Dict[str, Any]:
        """Get monthly analytics"""
        try:
            condition = WarehouseRepository.warehouse_filter(warehouse_name)
            
            monthly = self.session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
            
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
                "revenue_growth_pct": _growth(current_revenue, previous_revenue),
            }
        except Exception:
            return {}
    
    def _get_top_products(self, warehouse_name: str) -> List[Dict[str, Any]]:
        """Get top products for warehouse"""
        try:
            condition = WarehouseRepository.warehouse_filter(warehouse_name)
            
            results = self.session.query(
                DeliveryReport.customer_model.label("product"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(condition, DeliveryReport.customer_model.isnot(None)).group_by(
                DeliveryReport.customer_model
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).limit(10).all()
            
            products = []
            for row in results:
                products.append({
                    "name": _text(row.product),
                    "revenue": float(row.revenue or 0),
                    "units": int(row.units or 0),
                    "dns": int(row.dns or 0),
                })
            
            return products
        except Exception:
            return []
    
    def _generate_insights(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate insights from dashboard"""
        insights = []
        
        revenue = dashboard.get('total_revenue', 0)
        growth = dashboard.get('monthly_growth', 0)
        pending = dashboard.get('pending_dn', 0)
        score = dashboard.get('business_score', 0)
        delivery = dashboard.get('delivery_success_pct', 0)
        dealers = dashboard.get('total_dealers', 0)
        cities = dashboard.get('total_cities', 0)
        
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
        
        if dealers > 20:
            insights.append(f"Large dealer network: {dealers} dealers")
        
        if cities > 10:
            insights.append(f"Serves {cities} cities")
        
        if not insights:
            insights.append("Performance is stable. Continue monitoring.")
        
        return insights
    
    def _generate_recommendations(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate recommendations from dashboard"""
        recommendations = []
        
        pending = dashboard.get('pending_dn', 0)
        delivery = dashboard.get('delivery_success_pct', 0)
        score = dashboard.get('business_score', 0)
        pod = dashboard.get('avg_pod', 0)
        dealers = dashboard.get('total_dealers', 0)
        cities = dashboard.get('total_cities', 0)
        
        if pending > 20:
            recommendations.append(f"Escalate {pending} pending DNs for resolution")
        elif pending > 10:
            recommendations.append("Review pending orders for timely closure")
        
        if delivery < 80:
            recommendations.append("Improve delivery speed and reliability")
        
        if score < 70:
            recommendations.append("Develop action plan to improve business score")
        
        if pod > 5:
            recommendations.append("Focus on POD collection and completion")
        
        if dealers < 10:
            recommendations.append("Consider expanding dealer network")
        
        if cities < 5:
            recommendations.append("Consider expanding to more cities")
        
        if not recommendations:
            recommendations.append("Maintain current performance levels")
            recommendations.append("Continue monitoring key metrics")
        
        return recommendations
    
    def _generate_executive_summary(self, dashboard: Dict[str, Any]) -> str:
        """Generate executive summary"""
        warehouse = dashboard.get('warehouse', 'Warehouse')
        revenue = dashboard.get('total_revenue', 0)
        growth = dashboard.get('monthly_growth', 0)
        pending = dashboard.get('pending_dn', 0)
        score = dashboard.get('business_score', 0)
        status = dashboard.get('overall_status', 'Unknown')
        dealers = dashboard.get('total_dealers', 0)
        cities = dashboard.get('total_cities', 0)
        
        if growth >= 0:
            trend = "growing"
        else:
            trend = "declining"
        
        if score >= 70:
            action = "maintain current controls"
        else:
            action = "prioritize pending DN and POD closure"
        
        return (
            f"{warehouse.title()} is {trend} with a {score:.1f}/100 business score. "
            f"Revenue is PKR {revenue:,.2f} with {pending} pending DNs. "
            f"Delivery success is {dashboard.get('delivery_success_pct', 0):.1f}%. "
            f"The warehouse serves {dealers} dealers in {cities} cities. "
            f"Recommendation: {action}."
        )

# ============================================================
# BLOCK 12: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def __init__(self):
        self._menu_renderer = WarehouseMenuRenderer()
    
    def format(self, answer: WarehouseAnswer) -> str:
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
    
    def _format_metric(self, answer: WarehouseAnswer) -> str:
        """Single metric format"""
        warehouse = answer.plan.warehouse or "Warehouse"
        lines = [f"📊 *{warehouse.title()}*"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        if answer.explanation:
            lines.append("")
            lines.append(answer.explanation)
        
        return "\n".join(lines)
    
    def _format_compact(self, answer: WarehouseAnswer) -> str:
        """Compact format"""
        warehouse = answer.plan.warehouse or "Warehouse"
        lines = [f"📊 {warehouse.title()}"]
        lines.append("")
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_standard(self, answer: WarehouseAnswer) -> str:
        """Standard format"""
        return self._menu_renderer.render_warehouse_dashboard(
            answer.plan.warehouse or "Warehouse",
            answer.dashboard or {}
        )
    
    def _format_executive(self, answer: WarehouseAnswer) -> str:
        """Executive summary format"""
        warehouse = answer.plan.warehouse or "Warehouse"
        lines = [
            f"📋 *Executive Summary - {warehouse.title()}*",
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
    
    def _format_detailed(self, answer: WarehouseAnswer) -> str:
        """Detailed format"""
        warehouse = answer.plan.warehouse or "Warehouse"
        lines = [
            f"📊 *Detailed Analysis - {warehouse.title()}*",
            "",
            "📍 *Location*",
            "─" * 40,
        ]
        
        if answer.dashboard:
            lines.append(f"Warehouse Code: {answer.dashboard.get('warehouse_code', 'N/A')}")
            lines.append(f"Sales Office: {answer.dashboard.get('sales_office', 'N/A')}")
            lines.append(f"Sales Manager: {answer.dashboard.get('sales_manager', 'N/A')}")
        
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
    
    def _format_kpi_only(self, answer: WarehouseAnswer) -> str:
        """KPI-only format"""
        warehouse = answer.plan.warehouse or "Warehouse"
        lines = [f"📊 *{warehouse.title()} KPIs*:"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"  {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_comparison(self, answer: WarehouseAnswer) -> str:
        """Comparison format"""
        return self._menu_renderer.render_comparison_result(
            answer.plan.warehouses[0] if answer.plan.warehouses else "",
            answer.plan.warehouses[1] if len(answer.plan.warehouses) > 1 else "",
            answer.metrics
        )
    
    def _format_ranking(self, answer: WarehouseAnswer) -> str:
        """Ranking format"""
        ranking_data = answer.metrics.get("ranking", [])
        return self._menu_renderer.render_ranking(ranking_data, answer.plan.sort_by or "revenue", answer.plan.limit)

# ============================================================
# BLOCK 13: MAIN WAREHOUSE ANALYTICS SERVICE WITH MENU
# ============================================================

class WarehouseAnalyticsService:
    """
    Warehouse Domain AI Expert with Full Menu System
    Single entry point for all warehouse-related business questions
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "warehouse_analytics"
        self._version = "2.0.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = WarehouseMenuRenderer()
        self._formatter = ResponseFormatter()
        
        # Context memory
        self._contexts: Dict[str, WarehouseContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ WarehouseAnalyticsService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅")
        logger.info(f"   Source of Truth: PostgreSQL")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main warehouse menu"""
        return self._menu_renderer.render_main_menu()
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response
        
        Returns:
            {
                "response": str,           # WhatsApp message
                "menu_type": str,          # "warehouse_menu"
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
        elif context.menu_state == MenuState.WAREHOUSE_SELECTION:
            return self._handle_warehouse_selection(context, user_input)
        elif context.menu_state == MenuState.COMPARISON_SELECTION:
            return self._handle_comparison_selection(context, user_input)
        
        # Default: treat as quick query
        return self._handle_quick_query(context, user_input)
    
    def _handle_main_menu_return(self, context: WarehouseContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_warehouses = []
        context.awaiting_warehouse = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "warehouse_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True
        }
    
    def _handle_main_menu_option(self, context: WarehouseContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("dashboard", "Enter warehouse name for dashboard:"),
            "2": ("inventory", "Enter warehouse name for inventory:"),
            "3": ("revenue", "Enter warehouse name for revenue:"),
            "4": ("units", "Enter warehouse name for units:"),
            "5": ("pending_dn", "Enter warehouse name for pending DN:"),
            "6": ("pending_pgi", "Enter warehouse name for pending PGI:"),
            "7": ("pending_pod", "Enter warehouse name for pending POD:"),
            "8": ("delivery", "Enter warehouse name for delivery:"),
            "9": ("ranking", None),
            "10": ("comparison", None),
            "11": ("top_products", "Enter warehouse name for top products:"),
            "12": ("dealer_distribution", "Enter warehouse name for dealer distribution:"),
            "13": ("city_distribution", "Enter warehouse name for city distribution:"),
            "14": ("utilization", "Enter warehouse name for utilization:"),
            "15": ("transit", "Enter warehouse name for transit:"),
            "16": ("aging", "Enter warehouse name for aging:"),
            "17": ("kpis", "Enter warehouse name for KPIs:"),
            "18": ("summary", "Enter warehouse name for AI summary:"),
        }
        
        if option == "9":
            return self._handle_ranking_request(context)
        elif option == "10":
            return self._handle_comparison_start(context)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        if context.current_warehouse and action not in ["ranking", "comparison"]:
            result = self._execute_warehouse_action(context, action, context.current_warehouse)
            result["exit_menu"] = False
            return result
        
        context.menu_state = MenuState.WAREHOUSE_SELECTION
        context.selected_option = action
        context.awaiting_warehouse = True
        
        return {
            "response": self._menu_renderer.render_warehouse_selection(prompt),
            "menu_type": "warehouse_menu",
            "action": "warehouse_selection",
            "data": {"purpose": action},
            "exit_menu": False
        }
    
    def _handle_warehouse_selection(self, context: WarehouseContext, warehouse_input: str) -> Dict[str, Any]:
        """Handle warehouse selection response"""
        warehouse_name = self._resolve_warehouse_name(warehouse_input)
        if not warehouse_name:
            return {
                "response": "\n".join([
                    "❌ Warehouse not found.",
                    "",
                    "Please try again or enter a valid warehouse name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "warehouse_menu",
                "action": "warehouse_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_warehouse = warehouse_name
        context.menu_state = MenuState.MAIN
        context.awaiting_warehouse = False
        
        action = context.selected_option or "dashboard"
        result = self._execute_warehouse_action(context, action, warehouse_name)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: WarehouseContext, warehouse_input: str) -> Dict[str, Any]:
        """Handle comparison warehouse selection"""
        warehouse_name = self._resolve_warehouse_name(warehouse_input)
        if not warehouse_name:
            return {
                "response": "\n".join([
                    "❌ Warehouse not found.",
                    "",
                    "Please try again or enter a valid warehouse name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "warehouse_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_warehouses.append(warehouse_name)
        
        if len(context.comparison_warehouses) == 1:
            return {
                "response": "\n".join([
                    f"✅ First warehouse selected: {warehouse_name.title()}",
                    "",
                    "Enter second warehouse name:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "warehouse_menu",
                "action": "comparison_second",
                "data": {"first_warehouse": warehouse_name},
                "exit_menu": False
            }
        else:
            wh1, wh2 = context.comparison_warehouses[0], context.comparison_warehouses[1]
            context.menu_state = MenuState.MAIN
            context.comparison_warehouses = []
            return self._perform_comparison(context, wh1, wh2)
    
    def _handle_ranking_request(self, context: WarehouseContext) -> Dict[str, Any]:
        """Handle ranking request"""
        result = self._get_warehouse_ranking(context)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_start(self, context: WarehouseContext) -> Dict[str, Any]:
        """Start comparison process"""
        context.menu_state = MenuState.COMPARISON_SELECTION
        context.comparison_warehouses = []
        return {
            "response": self._menu_renderer.render_comparison_selection(),
            "menu_type": "warehouse_menu",
            "action": "comparison_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_quick_query(self, context: WarehouseContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            import re
            warehouses = re.findall(r'([\w\s]+?)(?:and|vs|versus)([\w\s]+)', query, re.IGNORECASE)
            if warehouses:
                wh1 = self._resolve_warehouse_name(warehouses[0][0].strip())
                wh2 = self._resolve_warehouse_name(warehouses[0][1].strip())
                if wh1 and wh2:
                    return self._perform_comparison(context, wh1, wh2)
        
        # Check if it's a valid warehouse name
        warehouse_name = self._resolve_warehouse_name(query)
        if warehouse_name:
            context.current_warehouse = warehouse_name
            return self._get_warehouse_dashboard(context, warehouse_name)
        
        # Check if it's a ranking query
        if "top" in query.lower() and ("warehouse" in query.lower() or "warehouses" in query.lower()):
            return self._get_warehouse_ranking(context)
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• 'Lahore' - Show dashboard",
                "• 'Revenue in Karachi'",
                "• 'Pending in Multan'",
                "• 'Compare Lahore and Karachi'",
                "• 'Top warehouses by revenue'",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "warehouse_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _execute_warehouse_action(self, context: WarehouseContext, action: str, warehouse_name: str) -> Dict[str, Any]:
        """Execute warehouse action based on selected option"""
        action_map = {
            "dashboard": self._get_warehouse_dashboard,
            "inventory": self._get_warehouse_inventory,
            "revenue": self._get_warehouse_metric,
            "units": self._get_warehouse_metric,
            "pending_dn": self._get_warehouse_pending_dn,
            "pending_pgi": self._get_warehouse_pending_pgi,
            "pending_pod": self._get_warehouse_pending_pod,
            "delivery": self._get_warehouse_delivery,
            "top_products": self._get_warehouse_top_products,
            "dealer_distribution": self._get_warehouse_dealer_distribution,
            "city_distribution": self._get_warehouse_city_distribution,
            "utilization": self._get_warehouse_utilization,
            "transit": self._get_warehouse_transit,
            "aging": self._get_warehouse_aging,
            "kpis": self._get_warehouse_kpis,
            "summary": self._get_warehouse_summary,
        }
        
        handler = action_map.get(action, self._get_warehouse_dashboard)
        
        if action in ["revenue", "units"]:
            return handler(context, warehouse_name, action)
        else:
            return handler(context, warehouse_name)
    
    def _resolve_warehouse_name(self, input_text: str) -> Optional[str]:
        """Resolve warehouse name from input"""
        input_lower = input_text.lower().strip()
        
        # Direct match
        if input_lower in WAREHOUSE_NAMES:
            return input_lower
        
        # Check aliases
        if input_lower in WAREHOUSE_ALIASES:
            return WAREHOUSE_ALIASES[input_lower]
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            matches = process.extract(input_lower, WAREHOUSE_NAMES, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= 85:
                return matches[0][0]
        
        # Partial match
        for warehouse in WAREHOUSE_NAMES:
            if len(input_lower) >= 3:
                if input_lower[:3] in warehouse or warehouse[:3] in input_lower:
                    return warehouse
        
        return None
    
    def _get_context(self, session_id: str) -> WarehouseContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = WarehouseContext()
            return self._contexts[session_id]
    
    # ============================================================
    # WAREHOUSE OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_warehouse_dashboard(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dashboard"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\nPlease check the warehouse name and try again.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "dashboard",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_warehouse_dashboard(warehouse_name, dashboard),
                    "menu_type": "warehouse_menu",
                    "action": "dashboard",
                    "data": {"warehouse": warehouse_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Service error for {warehouse_name}: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_inventory(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse inventory"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "inventory_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📦 *Inventory - {warehouse_name.title()}*",
                        "",
                        f"Total Units: {dashboard.get('total_units', 0):,}",
                        f"Total Products: {dashboard.get('total_products', 0):,}",
                        f"Total DN: {dashboard.get('total_dn', 0):,}",
                        f"Pending DN: {dashboard.get('pending_dn', 0):,}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "inventory",
                    "data": {"warehouse": warehouse_name, "inventory": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_metric(self, context: WarehouseContext, warehouse_name: str, metric: str) -> Dict[str, Any]:
        """Get specific warehouse metric"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "metric_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                metric_mapping = {
                    "revenue": ("Revenue", f"PKR {dashboard.get('total_revenue', 0):,.2f}"),
                    "units": ("Units", f"{dashboard.get('total_units', 0):,}"),
                }
                
                label, value = metric_mapping.get(metric, ("Metric", "N/A"))
                
                return {
                    "response": "\n".join([
                        f"📊 *{warehouse_name.title()} - {label}*",
                        "",
                        f"{value}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": f"metric_{metric}",
                    "data": {"warehouse": warehouse_name, "metric": metric, "value": value},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_pending_dn(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse pending DN"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "pending_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending DN - {warehouse_name.title()}*\n\nPending DN: {dashboard.get('pending_dn', 0):,}\nPending PGI: {dashboard.get('pending_pgi', 0):,}\nPending POD: {dashboard.get('pending_pod', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "warehouse_menu",
                    "action": "pending_dn",
                    "data": {"warehouse": warehouse_name, "pending": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_pending_pgi(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse pending PGI"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "pgi_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending PGI - {warehouse_name.title()}*\n\nPending PGI: {dashboard.get('pending_pgi', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "warehouse_menu",
                    "action": "pending_pgi",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_pending_pod(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse pending POD"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "pod_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": f"⏳ *Pending POD - {warehouse_name.title()}*\n\nPending POD: {dashboard.get('pending_pod', 0):,}\n\n0. Main Menu\n99. Back",
                    "menu_type": "warehouse_menu",
                    "action": "pending_pod",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_delivery(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse delivery"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "delivery_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"🚚 *Delivery - {warehouse_name.title()}*",
                        "",
                        f"Success Rate: {dashboard.get('delivery_success_pct', 0):.1f}%",
                        f"Average Days: {dashboard.get('avg_delivery', 0):.1f}",
                        f"Transit Days: {dashboard.get('transit_days', 0):.1f}",
                        f"POD Average: {dashboard.get('avg_pod', 0):.1f} Days",
                        f"Cycle Time: {dashboard.get('avg_cycle', 0):.1f} Days",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "delivery",
                    "data": {"warehouse": warehouse_name, "delivery": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_top_products(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse top products"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "top_products_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                top_products = dashboard.get('top_products', [])
                if not top_products:
                    return {
                        "response": f"🏷️ *Top Products - {warehouse_name.title()}*\n\nNo products found.\n\n0. Main Menu\n99. Back",
                        "menu_type": "warehouse_menu",
                        "action": "top_products",
                        "data": {"warehouse": warehouse_name},
                        "exit_menu": False
                    }
                
                lines = [f"🏷️ *Top Products - {warehouse_name.title()}*", ""]
                for i, product in enumerate(top_products[:5], 1):
                    lines.append(f"{i}. {product.get('name', 'N/A')}")
                    lines.append(f"   Revenue: PKR {product.get('revenue', 0):,.2f}")
                    lines.append(f"   Units: {product.get('units', 0):,}")
                    lines.append("")
                
                lines.extend([
                    "0. Main Menu",
                    "99. Back"
                ])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "warehouse_menu",
                    "action": "top_products",
                    "data": {"warehouse": warehouse_name, "products": top_products},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_dealer_distribution(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dealer distribution"""
        try:
            with self._session() as session:
                condition = WarehouseRepository.warehouse_filter(warehouse_name)
                
                results = session.query(
                    DeliveryReport.customer_name.label("dealer"),
                    func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                ).filter(condition, DeliveryReport.customer_name.isnot(None)).group_by(
                    DeliveryReport.customer_name
                ).order_by(func.count(distinct(DeliveryReport.dn_no)).desc()).limit(20).all()
                
                items = []
                for row in results:
                    items.append({
                        "name": _text(row.dealer),
                        "value": int(row.dns or 0),
                        "revenue": float(row.revenue or 0),
                    })
                
                return {
                    "response": self._menu_renderer.render_distribution("Dealer Distribution", items, 10),
                    "menu_type": "warehouse_menu",
                    "action": "dealer_distribution",
                    "data": {"warehouse": warehouse_name, "items": items},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_city_distribution(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse city distribution"""
        try:
            with self._session() as session:
                condition = WarehouseRepository.warehouse_filter(warehouse_name)
                
                results = session.query(
                    DeliveryReport.ship_to_city.label("city"),
                    func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                ).filter(condition, DeliveryReport.ship_to_city.isnot(None)).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(func.count(distinct(DeliveryReport.dn_no)).desc()).limit(20).all()
                
                items = []
                total = sum(int(row.dns or 0) for row in results)
                for row in results:
                    items.append({
                        "name": _text(row.city),
                        "value": int(row.dns or 0),
                        "percentage": _percent(row.dns, total) if total > 0 else 0,
                    })
                
                return {
                    "response": self._menu_renderer.render_distribution("City Distribution", items, 10),
                    "menu_type": "warehouse_menu",
                    "action": "city_distribution",
                    "data": {"warehouse": warehouse_name, "items": items},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_utilization(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse utilization"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "utilization_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                total_units = dashboard.get('total_units', 0)
                total_dn = dashboard.get('total_dn', 0)
                
                utilization = _percent(total_units, total_dn * 10) if total_dn > 0 else 0
                
                return {
                    "response": "\n".join([
                        f"📊 *Storage Utilization - {warehouse_name.title()}*",
                        "",
                        f"Total Units: {total_units:,}",
                        f"Total DN: {total_dn:,}",
                        f"Utilization Rate: {min(100, utilization):.1f}%",
                        f"Products: {dashboard.get('total_products', 0):,}",
                        f"Dealers: {dashboard.get('total_dealers', 0):,}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "utilization",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_transit(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse transit"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "transit_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"🚚 *Transit Analysis - {warehouse_name.title()}*",
                        "",
                        f"Transit Days: {dashboard.get('transit_days', 0):.1f}",
                        f"Delivery Days: {dashboard.get('avg_delivery', 0):.1f}",
                        f"POD Days: {dashboard.get('avg_pod', 0):.1f}",
                        f"Cycle Time: {dashboard.get('avg_cycle', 0):.1f}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "transit",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_aging(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse aging"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "aging_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📈 *Delivery Aging - {warehouse_name.title()}*",
                        "",
                        f"Average Delivery: {dashboard.get('avg_delivery', 0):.1f} Days",
                        f"Average POD: {dashboard.get('avg_pod', 0):.1f} Days",
                        f"Transit Days: {dashboard.get('transit_days', 0):.1f} Days",
                        f"Cycle Time: {dashboard.get('avg_cycle', 0):.1f} Days",
                        "",
                        f"First DN: {dashboard.get('first_delivery_date', 'N/A')}",
                        f"Latest DN: {dashboard.get('latest_delivery_date', 'N/A')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "aging",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_kpis(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse KPIs"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "kpis_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📊 *KPIs - {warehouse_name.title()}*",
                        "",
                        f"Revenue: PKR {dashboard.get('total_revenue', 0):,.2f}",
                        f"Units: {dashboard.get('total_units', 0):,}",
                        f"DN: {dashboard.get('total_dn', 0):,}",
                        f"Dealers: {dashboard.get('total_dealers', 0):,}",
                        f"Cities: {dashboard.get('total_cities', 0):,}",
                        f"Products: {dashboard.get('total_products', 0):,}",
                        f"Pending DN: {dashboard.get('pending_dn', 0):,}",
                        f"Delivery Success: {dashboard.get('delivery_success_pct', 0):.1f}%",
                        f"Business Score: {dashboard.get('business_score', 0):.1f}/100",
                        f"Status: {dashboard.get('overall_status', 'Unknown')}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "warehouse_menu",
                    "action": "kpis",
                    "data": {"warehouse": warehouse_name, "kpis": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_summary(self, context: WarehouseContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse AI summary"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dashboard = builder.build(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "summary_error",
                        "data": {"warehouse": warehouse_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        f"📋 *AI Summary - {warehouse_name.title()}*",
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
                        f"Dealers: {dashboard.get('total_dealers', 0):,}",
                        f"Cities: {dashboard.get('total_cities', 0):,}",
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
                    "menu_type": "warehouse_menu",
                    "action": "summary",
                    "data": {"warehouse": warehouse_name},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_ranking(self, context: WarehouseContext) -> Dict[str, Any]:
        """Get warehouse rankings"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.warehouse.label("warehouse"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue")
                ).filter(
                    DeliveryReport.warehouse.isnot(None)
                ).group_by(
                    DeliveryReport.warehouse
                ).order_by(
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).desc()
                ).limit(10).all()
                
                ranking = []
                for row in results:
                    warehouse = _text(row.warehouse)
                    if warehouse:
                        ranking.append({
                            "warehouse": warehouse,
                            "value": f"PKR {float(row.revenue or 0):,.2f}"
                        })
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "warehouse_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Ranking error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_comparison(self, context: WarehouseContext, wh1: str, wh2: str) -> Dict[str, Any]:
        """Perform warehouse comparison"""
        try:
            with self._session() as session:
                builder = WarehouseDashboardBuilder(session)
                dash1 = builder.build(wh1)
                dash2 = builder.build(wh2)
                
                if not dash1 or not dash2:
                    return {
                        "response": "⚠️ One or both warehouses not found.\n\n0. Main Menu",
                        "menu_type": "warehouse_menu",
                        "action": "comparison_error",
                        "data": {"error": "not_found"},
                        "exit_menu": False
                    }
                
                metrics = {}
                
                metrics[f"{wh1}_metrics"] = {
                    "Revenue": f"PKR {dash1.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash1.get('total_units', 0):,}",
                    "DN": f"{dash1.get('total_dn', 0):,}",
                    "Pending": f"{dash1.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash1.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash1.get('business_score', 0):.1f}/100",
                    "Dealers": f"{dash1.get('total_dealers', 0):,}",
                    "Cities": f"{dash1.get('total_cities', 0):,}",
                }
                
                metrics[f"{wh2}_metrics"] = {
                    "Revenue": f"PKR {dash2.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash2.get('total_units', 0):,}",
                    "DN": f"{dash2.get('total_dn', 0):,}",
                    "Pending": f"{dash2.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash2.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash2.get('business_score', 0):.1f}/100",
                    "Dealers": f"{dash2.get('total_dealers', 0):,}",
                    "Cities": f"{dash2.get('total_cities', 0):,}",
                }
                
                revenue1 = dash1.get('total_revenue', 0)
                revenue2 = dash2.get('total_revenue', 0)
                
                if revenue1 > revenue2:
                    explanation = f"{wh1.title()} has higher revenue than {wh2.title()}"
                elif revenue2 > revenue1:
                    explanation = f"{wh2.title()} has higher revenue than {wh1.title()}"
                else:
                    explanation = f"{wh1.title()} and {wh2.title()} have similar revenue"
                
                metrics["explanation"] = explanation
                
                return {
                    "response": self._menu_renderer.render_comparison_result(wh1, wh2, metrics),
                    "menu_type": "warehouse_menu",
                    "action": "comparison",
                    "data": {"wh1": wh1, "wh2": wh2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Comparison error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "warehouse_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    # ============================================================
    # LEGACY METHODS - BACKWARD COMPATIBILITY
    # ============================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not warehouse_name:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide a warehouse name.",
                "error": "WAREHOUSE_REQUIRED"
            }
        
        context = WarehouseContext()
        result = self._get_warehouse_dashboard(context, warehouse_name)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_top_warehouses(self, limit: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = WarehouseContext()
        result = self._get_warehouse_ranking(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("ranking", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def compare_warehouses(self, warehouses: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not warehouses or len(warehouses) < 2:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide at least two warehouses.",
                "error": "TWO_WAREHOUSES_REQUIRED"
            }
        
        context = WarehouseContext()
        result = self._perform_comparison(context, warehouses[0], warehouses[1])
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
                warehouses = session.query(func.count(distinct(DeliveryReport.warehouse))).scalar() or 0
            
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
                "warehouses": int(warehouses),
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
        
        if message.strip() in ["menu", "help", "options"]:
            return self.get_main_menu()
        
        result = self.process_menu_input(sender, message.strip())
        response = result.get("response", self.get_main_menu())
        
        if result.get("exit_menu", False):
            return response
        
        return response


# ============================================================
# BLOCK 14: SERVICE SINGLETON
# ============================================================

_service: Optional[WarehouseAnalyticsService] = None
_service_lock = threading.Lock()


def get_warehouse_analytics_service() -> WarehouseAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = WarehouseAnalyticsService()
    return _service


def process_warehouse_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process warehouse menu input for WhatsApp integration"""
    service = get_warehouse_analytics_service()
    return service.process_menu_input(session_id, user_input)


def get_warehouse_main_menu() -> str:
    """Get the main warehouse menu for WhatsApp"""
    service = get_warehouse_analytics_service()
    return service.get_main_menu()


# ============================================================
# BLOCK 15: EXPORTS
# ============================================================

__all__ = [
    "WarehouseAnalyticsService",
    "WarehouseContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_warehouse_analytics_service",
    "process_warehouse_menu",
    "get_warehouse_main_menu",
    "WarehouseMenuRenderer",
    "get_warehouse_dashboard",
    "get_top_warehouses",
    "compare_warehouses",
    "health_check",
]
