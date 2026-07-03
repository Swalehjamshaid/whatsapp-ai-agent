"""
File: app/services/national_kpi_service.py
Version: 5.0 - ENTERPRISE NATIONAL LOGISTICS INTELLIGENCE ENGINE WITH FULL MENU
Purpose: National executive dashboard and logistics intelligence for Haier Pakistan
         PostgreSQL is the ONLY source of truth.
         Full menu system with 20+ options, sub-menus, and AI-powered queries

FEATURES:
- ✅ Complete Menu System
- ✅ 20+ National Analytics Options
- ✅ Warehouse Wise Analytics
- ✅ Delivery SLA & POD Policy Engine
- ✅ AI Recommendation Engine
- ✅ National Health Score
- ✅ Executive Summary
- ✅ Quick Commands Support
- ✅ Context Memory
- ✅ Dynamic Menu Rendering
- ✅ WhatsApp-Optimized Formatting
- ✅ PostgreSQL Integration

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

CACHE_TTL = max(60, int(os.getenv("NATIONAL_KPI_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"

# Delivery SLA Policy - Standard timelines based on distance
DELIVERY_SLA: Dict[str, Dict[str, Any]] = {
    "0-100": {"distance_max": 100, "target_days": 1, "category": "Same Day/Next Day"},
    "101-200": {"distance_max": 200, "target_days": 2, "category": "2 Days"},
    "201-300": {"distance_max": 300, "target_days": 3, "category": "3 Days"},
    "301-500": {"distance_max": 500, "target_days": 4, "category": "4 Days"},
    "501-700": {"distance_max": 700, "target_days": 5, "category": "5 Days"},
    "701+": {"distance_max": float('inf'), "target_days": 6, "category": "6 Days"},
}

# POD Policy - Standard timelines
POD_POLICY: Dict[str, int] = {
    "standard": 3,  # Standard POD submission in days
    "priority": 1,   # Priority POD submission in days
}

# Warehouse coordinates for distance calculation
WAREHOUSE_COORDINATES: Dict[str, Tuple[float, float]] = {
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

SEPARATOR: str = "────────────────────"

# ============================================================
# BLOCK 3: ENUMS
# ============================================================

class IntentType(Enum):
    """National KPI question intent types"""
    NATIONAL_DASHBOARD = "national_dashboard"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    WAREHOUSE_COMPARISON = "warehouse_comparison"
    REVENUE = "revenue"
    UNITS = "units"
    DELIVERY = "delivery"
    PENDING = "pending"
    POD = "pod"
    PGI = "pgi"
    DEALER_COVERAGE = "dealer_coverage"
    CITY_ANALYTICS = "city_analytics"
    PRODUCT_DISTRIBUTION = "product_distribution"
    SLA_COMPLIANCE = "sla_compliance"
    EXECUTIVE_SUMMARY = "executive_summary"
    AI_INSIGHTS = "ai_insights"
    RECOMMENDATIONS = "recommendations"
    HEALTH_SCORE = "health_score"
    TREND = "trend"
    FORECAST = "forecast"
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
    DASHBOARD = "dashboard"

# ============================================================
# BLOCK 4: DATACLASSES
# ============================================================

@dataclass
class NationalContext:
    """Session context for national queries"""
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

@dataclass
class NationalAnswer:
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
# BLOCK 5: UTILITY FUNCTIONS
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

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance between two points using Haversine formula"""
    R = 6371  # Earth's radius in kilometers
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_sla_target(distance: float) -> Dict[str, Any]:
    """Get SLA target based on distance"""
    for key, sla in DELIVERY_SLA.items():
        if distance <= sla["distance_max"]:
            return sla
    return DELIVERY_SLA["701+"]

# ============================================================
# BLOCK 6: MENU SYSTEM
# ============================================================

class NationalMenuRenderer:
    """Render national KPI menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main national menu"""
        return "\n".join([
            "🇵🇰 *NATIONAL LOGISTICS INTELLIGENCE MENU*",
            "",
            "0. Main Menu",
            "1. National Dashboard",
            "2. Warehouse Dashboard",
            "3. Warehouse Ranking",
            "4. Warehouse Comparison",
            "5. National Revenue",
            "6. National Units",
            "7. National Delivery",
            "8. Pending Dashboard",
            "9. POD Dashboard",
            "10. PGI Dashboard",
            "11. Dealer Coverage",
            "12. City Analytics",
            "13. Product Distribution",
            "14. SLA Compliance",
            "15. Executive Summary",
            "16. AI Insights",
            "17. Recommendations",
            "18. National Health Score",
            "19. Monthly Trend",
            "20. National Forecast",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• National Dashboard",
            "• Warehouse Lahore",
            "• Compare Lahore and Karachi",
            "• Top warehouses by revenue",
            "• National delivery performance",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_warehouse_selection(prompt: str = "Enter warehouse name:") -> str:
        """Render warehouse selection prompt"""
        return "\n".join([
            "🏭 *Warehouse Selection*",
            "",
            prompt,
            "",
            "💡 *Examples:*",
            "Lahore",
            "Karachi",
            "Rawalpindi",
            "Multan",
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
    def render_national_dashboard(data: Dict[str, Any]) -> str:
        """Render national dashboard"""
        lines = [
            "🇵🇰 *NATIONAL LOGISTICS DASHBOARD*",
            "",
            "📊 *Overview*",
            f"Warehouses: {data.get('total_warehouses', 0):,}",
            f"Dealers: {data.get('total_dealers', 0):,}",
            f"Cities: {data.get('total_cities', 0):,}",
            f"Products: {data.get('total_products', 0):,}",
            "",
            "💰 *Financials*",
            f"Total Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Avg Revenue/DN: PKR {data.get('avg_revenue_per_dn', 0):,.2f}",
            "",
            "📦 *Operations*",
            f"Total DN: {data.get('total_dn', 0):,}",
            f"Total Units: {data.get('total_units', 0):,}",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            f"Pending PGI: {data.get('pending_pgi', 0):,}",
            f"Pending POD: {data.get('pending_pod', 0):,}",
            "",
            "🚚 *Delivery*",
            f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
            f"PGI Success: {data.get('pgi_success_pct', 0):.1f}%",
            f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
            f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
            "",
            "🏆 *National Health*",
            f"Overall Score: {data.get('national_health_score', 0):.1f}/100",
            f"Performance Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back",
            "",
            "📌 *Try:* 'Warehouse Lahore' or 'Compare Lahore and Karachi'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_warehouse_dashboard(warehouse: str, data: Dict[str, Any]) -> str:
        """Render warehouse dashboard"""
        lines = [
            f"🏭 *Warehouse Dashboard - {warehouse}*",
            "",
            "📊 *Overview*",
            f"Dealers: {data.get('dealer_count', 0):,}",
            f"Cities: {data.get('city_count', 0):,}",
            f"Products: {data.get('product_count', 0):,}",
            "",
            "💰 *Financials*",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Avg Revenue/DN: PKR {data.get('avg_revenue_per_dn', 0):,.2f}",
            "",
            "📦 *Operations*",
            f"DN: {data.get('total_dn', 0):,}",
            f"Units: {data.get('total_units', 0):,}",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            f"Pending PGI: {data.get('pending_pgi', 0):,}",
            f"Pending POD: {data.get('pending_pod', 0):,}",
            "",
            "🚚 *Delivery*",
            f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
            f"PGI Success: {data.get('pgi_success_pct', 0):.1f}%",
            f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
            f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Warehouse Score: {data.get('warehouse_score', 0):.1f}/100",
            f"Performance Grade: {data.get('performance_grade', 'N/A')}",
            f"SLA Compliance: {data.get('sla_compliance_pct', 0):.1f}%",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
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
            
            lines.append(f"{medal} {warehouse}: {value}")
        
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
        """Render warehouse comparison result"""
        lines = [
            f"🔄 *Comparison: {warehouse1} vs {warehouse2}*",
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
    def render_executive_summary(data: Dict[str, Any]) -> str:
        """Render executive summary"""
        lines = [
            "🇵🇰 *PAKISTAN LOGISTICS SUMMARY*",
            "",
            f"📊 Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"📦 DN: {data.get('total_dn', 0):,}",
            f"📦 Units: {data.get('total_units', 0):,}",
            f"🏭 Warehouses: {data.get('total_warehouses', 0):,}",
            f"🏪 Dealers: {data.get('total_dealers', 0):,}",
            f"🏙️ Cities: {data.get('total_cities', 0):,}",
            "",
            f"🚚 Delivery: {data.get('delivery_success_pct', 0):.1f}%",
            f"📄 POD: {data.get('pod_success_pct', 0):.1f}%",
            f"📈 Growth: {data.get('growth_rate', 0):+.1f}%",
            f"⭐ Health Score: {data.get('national_health_score', 0):.1f}/100",
            "",
            "🎯 *Recommendations*",
        ]
        
        for rec in data.get('recommendations', [])[:3]:
            lines.append(f"• {rec}")
        
        if not data.get('recommendations'):
            lines.append("• Maintain current performance levels")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_health_score(data: Dict[str, Any]) -> str:
        """Render national health score"""
        lines = [
            "⭐ *NATIONAL HEALTH SCORE*",
            "",
            f"Overall: {data.get('national_health_score', 0):.1f}/100",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "📊 *Component Scores*",
            f"Delivery: {data.get('delivery_score', 0):.1f}/100",
            f"Warehouse: {data.get('warehouse_score', 0):.1f}/100",
            f"Dealer: {data.get('dealer_score', 0):.1f}/100",
            f"Revenue: {data.get('revenue_score', 0):.1f}/100",
            f"POD: {data.get('pod_score', 0):.1f}/100",
            f"PGI: {data.get('pgi_score', 0):.1f}/100",
            f"Pending: {data.get('pending_score', 0):.1f}/100",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        return "\n".join(lines)

# ============================================================
# BLOCK 7: INTENT ENGINE
# ============================================================

class IntentEngine:
    """AI-powered intent detection for national queries"""
    
    INTENT_PATTERNS = {
        IntentType.NATIONAL_DASHBOARD: [
            r"(?:national|overall|pakistan).*(?:dashboard|kpi|overview)",
            r"show (?:national|overall) (?:dashboard|kpi)",
            r"national logistics (?:dashboard|overview)",
        ],
        IntentType.WAREHOUSE_DASHBOARD: [
            r"warehouse (?:dashboard|performance|details)",
            r"show (?:warehouse|warehouse dashboard)",
            r"warehouse (?:analysis|report)",
        ],
        IntentType.WAREHOUSE_RANKING: [
            r"(?:top|best|highest).*warehouse",
            r"warehouse (?:ranking|rank|leaderboard)",
            r"best (?:warehouse|warehouses)",
            r"worst warehouse",
        ],
        IntentType.WAREHOUSE_COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"warehouse vs",
            r"comparison",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income).*(?:national|overall|warehouse)",
            r"national (?:revenue|sales)",
            r"total revenue",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|volume).*(?:national|overall)",
            r"national (?:units|quantity)",
            r"total units",
        ],
        IntentType.DELIVERY: [
            r"(?:delivery|deliveries).*(?:national|overall|performance)",
            r"national delivery (?:performance|status)",
            r"delivery success",
        ],
        IntentType.PENDING: [
            r"(?:pending|backlog|overdue).*(?:national|overall)",
            r"national pending",
            r"pending (?:dn|orders|deliveries)",
        ],
        IntentType.POD: [
            r"(?:pod|proof of delivery).*(?:national|overall)",
            r"national pod",
            r"pod performance",
        ],
        IntentType.PGI: [
            r"(?:pgi|goods issue).*(?:national|overall)",
            r"national pgi",
            r"pgi performance",
        ],
        IntentType.DEALER_COVERAGE: [
            r"(?:dealer|dealers).*(?:coverage|distribution|count)",
            r"dealer (?:coverage|network)",
            r"total dealers",
        ],
        IntentType.CITY_ANALYTICS: [
            r"(?:city|cities).*(?:analytics|distribution|performance)",
            r"city (?:analysis|distribution)",
            r"top cities",
        ],
        IntentType.PRODUCT_DISTRIBUTION: [
            r"(?:product|products).*(?:distribution|analytics|top)",
            r"product (?:distribution|analytics)",
            r"top products",
        ],
        IntentType.SLA_COMPLIANCE: [
            r"(?:sla|service level).*(?:compliance|agreement)",
            r"sla (?:compliance|performance)",
            r"delivery (?:sla|timeline)",
        ],
        IntentType.EXECUTIVE_SUMMARY: [
            r"(?:executive|management).*(?:summary|overview)",
            r"executive (?:summary|dashboard)",
            r"logistics summary",
        ],
        IntentType.AI_INSIGHTS: [
            r"(?:insight|insights|analytics).*(?:national|overall)",
            r"national (?:insights|analytics)",
            r"key (?:insights|findings)",
        ],
        IntentType.RECOMMENDATIONS: [
            r"(?:recommend|suggest|advice).*(?:national|overall)",
            r"national (?:recommendations|suggestions)",
            r"what (?:should|can) be done",
        ],
        IntentType.HEALTH_SCORE: [
            r"(?:health|score|rating).*(?:national|overall)",
            r"national (?:health|score)",
            r"overall (?:health|score)",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|change).*(?:national|overall)",
            r"national (?:trend|growth)",
            r"monthly trend",
        ],
        IntentType.FORECAST: [
            r"(?:forecast|predict|future).*(?:national|overall)",
            r"national (?:forecast|projection)",
        ],
        IntentType.MENU: [
            r"menu",
            r"national menu",
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
                    Route(name="national_dashboard", utterances=[
                        "national dashboard", "national kpi", "pakistan logistics"
                    ]),
                    Route(name="warehouse_dashboard", utterances=[
                        "warehouse dashboard", "warehouse performance", "warehouse details"
                    ]),
                    Route(name="warehouse_ranking", utterances=[
                        "top warehouses", "warehouse ranking", "best warehouse"
                    ]),
                    Route(name="national_revenue", utterances=[
                        "national revenue", "total revenue", "revenue summary"
                    ]),
                    Route(name="national_units", utterances=[
                        "national units", "total units", "units summary"
                    ]),
                    Route(name="national_delivery", utterances=[
                        "national delivery", "delivery performance", "delivery success"
                    ]),
                    Route(name="national_pending", utterances=[
                        "national pending", "pending orders", "backlog"
                    ]),
                    Route(name="executive_summary", utterances=[
                        "executive summary", "management summary", "logistics summary"
                    ]),
                    Route(name="national_health", utterances=[
                        "national health", "health score", "overall score"
                    ]),
                ]
                self._semantic_router = Router(routes=routes, encoder=HuggingFaceEncoder())
                logger.info("✅ Semantic router initialized for national KPI")
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
        if question_lower in ["menu", "national menu", "options", "help", "show menu"]:
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
                    intent_name = result.name.replace("national_", "")
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
                if keyword in ["dashboard", "kpi", "overview"]:
                    best_intent = IntentType.NATIONAL_DASHBOARD
                    best_score = 0.5
                    break
                elif keyword in ["revenue", "sales"]:
                    best_intent = IntentType.REVENUE
                    best_score = 0.5
                    break
                elif keyword in ["warehouse", "warehouses"]:
                    best_intent = IntentType.WAREHOUSE_DASHBOARD
                    best_score = 0.5
                    break
                elif keyword in ["delivery", "deliveries"]:
                    best_intent = IntentType.DELIVERY
                    best_score = 0.5
                    break
                elif keyword in ["pending", "backlog"]:
                    best_intent = IntentType.PENDING
                    best_score = 0.5
                    break
                elif keyword in ["pod"]:
                    best_intent = IntentType.POD
                    best_score = 0.5
                    break
                elif keyword in ["pgi"]:
                    best_intent = IntentType.PGI
                    best_score = 0.5
                    break
                elif keyword in ["top", "best", "ranking"]:
                    best_intent = IntentType.WAREHOUSE_RANKING
                    best_score = 0.5
                    break
                elif keyword in ["compare", "vs"]:
                    best_intent = IntentType.WAREHOUSE_COMPARISON
                    best_score = 0.5
                    break
                elif keyword in ["health", "score"]:
                    best_intent = IntentType.HEALTH_SCORE
                    best_score = 0.5
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 8: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for national queries"""
    
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
        for metric in ["revenue", "units", "dn", "delivery", "pending", "score"]:
            if metric in question_lower:
                entities["sort_by"] = metric
                break
        
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_warehouses(self, text: str) -> List[str]:
        """Extract warehouse names from text"""
        found = []
        
        # Known warehouses
        warehouses = ["Lahore", "Karachi", "Rawalpindi", "Multan", "Peshawar", 
                     "Hyderabad", "Quetta", "Faisalabad", "Sialkot", "Gujranwala"]
        
        for warehouse in warehouses:
            if warehouse.lower() in text:
                found.append(warehouse)
        
        return found
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income"],
            "units": ["units", "quantity", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "delivery": ["delivery", "deliveries"],
            "pod": ["pod"],
            "pgi": ["pgi"],
            "score": ["score", "health", "rating"],
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
# BLOCK 9: NATIONAL REPOSITORY
# ============================================================

class NationalRepository:
    """National data access layer - PostgreSQL only"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def get_national_dashboard(self) -> Dict[str, Any]:
        """Get national dashboard data"""
        cache_key = "national_dashboard"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                func.count(distinct(DeliveryReport.warehouse)).label('warehouses'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealers'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('cities'),
                func.count(distinct(DeliveryReport.customer_model)).label('products'),
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pending_pgi'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_pod'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pod_completed'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pgi_completed'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
                func.avg(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)),
                     DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                )).label('avg_pod_days'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue_per_dn'),
                func.avg(DeliveryReport.dn_qty).label('avg_units_per_dn'),
            ).first()
            
            if not query:
                return {}
            
            data = {
                'total_warehouses': int(query.warehouses or 0),
                'total_dealers': int(query.dealers or 0),
                'total_cities': int(query.cities or 0),
                'total_products': int(query.products or 0),
                'total_dn': int(query.total_dn or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pending_pgi': int(query.pending_pgi or 0),
                'pending_pod': int(query.pending_pod or 0),
                'pod_completed': int(query.pod_completed or 0),
                'pgi_completed': int(query.pgi_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
                'avg_revenue_per_dn': float(query.avg_revenue_per_dn or 0.0),
                'avg_units_per_dn': float(query.avg_units_per_dn or 0.0),
            }
            
            # Calculate percentages
            data['delivery_success_pct'] = _percent(
                data.get('pgi_completed', 0),
                data.get('total_dn', 0)
            )
            data['pod_success_pct'] = _percent(
                data.get('pod_completed', 0),
                data.get('total_dn', 0)
            )
            data['pgi_success_pct'] = _percent(
                data.get('pgi_completed', 0),
                data.get('total_dn', 0)
            )
            
            # Get monthly growth
            monthly_data = self._get_monthly_data()
            if monthly_data:
                data.update(monthly_data)
            
            # Calculate national health score
            data['national_health_score'] = self._calculate_national_health_score(data)
            data['performance_grade'] = self._get_performance_grade(data['national_health_score'])
            
            # Generate insights and recommendations
            data['insights'] = self._generate_insights(data)
            data['recommendations'] = self._generate_recommendations(data)
            data['executive_summary'] = self._generate_executive_summary(data)
            
            with self._lock:
                self._cache[cache_key] = data.copy()
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to get national dashboard: {e}")
            return {}
    
    def _get_monthly_data(self) -> Dict[str, Any]:
        """Get monthly trend data"""
        try:
            monthly = self.session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label('month'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn'),
            ).filter(
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
            }
        except Exception:
            return {}
    
    def _calculate_national_health_score(self, data: Dict[str, Any]) -> float:
        """Calculate national health score"""
        scores = {
            'delivery': min(100, data.get('delivery_success_pct', 0) * 1.1),
            'pod': min(100, data.get('pod_success_pct', 0) * 1.1),
            'pgi': min(100, data.get('pgi_success_pct', 0) * 1.1),
            'pending': max(0, 100 - (data.get('pending_dn', 0) / max(1, data.get('total_dn', 1)) * 100)),
            'revenue': min(100, data.get('total_revenue', 0) / 1000000 * 10),  # Scale by millions
        }
        
        # Weighted average
        weights = {
            'delivery': 0.25,
            'pod': 0.20,
            'pgi': 0.15,
            'pending': 0.25,
            'revenue': 0.15,
        }
        
        score = sum(scores[k] * weights[k] for k in scores.keys())
        return round(min(100, max(0, score)), 1)
    
    def _get_performance_grade(self, score: float) -> str:
        """Get performance grade based on score"""
        if score >= 85:
            return "A"
        elif score >= 70:
            return "B"
        elif score >= 50:
            return "C"
        else:
            return "D"
    
    def _generate_insights(self, data: Dict[str, Any]) -> List[str]:
        """Generate insights from data"""
        insights = []
        
        revenue = data.get('total_revenue', 0)
        growth = data.get('growth_rate', 0)
        delivery = data.get('delivery_success_pct', 0)
        pod = data.get('pod_success_pct', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('national_health_score', 0)
        
        if revenue > 0 and growth > 10:
            insights.append(f"Strong national growth at {growth:+.1f}%")
        elif revenue > 0 and growth > 0:
            insights.append(f"Steady national growth at {growth:+.1f}%")
        elif revenue > 0 and growth < -5:
            insights.append(f"National revenue decline of {growth:+.1f}% needs attention")
        
        if delivery >= 95:
            insights.append("Excellent national delivery performance")
        elif delivery >= 85:
            insights.append("Good national delivery performance")
        elif delivery < 75:
            insights.append("National delivery performance needs improvement")
        
        if pod >= 95:
            insights.append("Excellent national POD compliance")
        elif pod >= 85:
            insights.append("Good national POD compliance")
        elif pod < 75:
            insights.append("National POD compliance needs improvement")
        
        if pending > 0:
            insights.append(f"National pending orders: {pending:,} - priority for resolution")
        
        if score >= 85:
            insights.append(f"Excellent national health score of {score:.1f}/100")
        elif score >= 70:
            insights.append(f"Good national health score of {score:.1f}/100")
        elif score < 50:
            insights.append(f"Critical national health score of {score:.1f}/100")
        
        if not insights:
            insights.append("National performance is stable. Continue monitoring.")
        
        return insights
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate recommendations from data"""
        recommendations = []
        
        pending = data.get('pending_dn', 0)
        delivery = data.get('delivery_success_pct', 0)
        pod = data.get('pod_success_pct', 0)
        score = data.get('national_health_score', 0)
        
        if pending > 100:
            recommendations.append(f"Escalate {pending:,} pending DNs for resolution")
        elif pending > 50:
            recommendations.append("Review pending orders for timely closure")
        
        if delivery < 80:
            recommendations.append("Improve national delivery speed and reliability")
        
        if pod < 80:
            recommendations.append("Focus on national POD collection and completion")
        
        if score < 70:
            recommendations.append("Develop action plan to improve national health score")
        
        if not recommendations:
            recommendations.append("Maintain current national performance levels")
            recommendations.append("Continue monitoring key metrics")
        
        return recommendations
    
    def _generate_executive_summary(self, data: Dict[str, Any]) -> str:
        """Generate executive summary"""
        revenue = data.get('total_revenue', 0)
        dn = data.get('total_dn', 0)
        delivery = data.get('delivery_success_pct', 0)
        score = data.get('national_health_score', 0)
        growth = data.get('growth_rate', 0)
        
        if growth >= 0:
            trend = "growing"
        else:
            trend = "declining"
        
        return (
            f"Pakistan logistics is {trend} with a {score:.1f}/100 health score. "
            f"Revenue is PKR {revenue:,.2f} with {dn:,} DNs. "
            f"Delivery success is {delivery:.1f}%. "
            f"Recommendation: Maintain focus on pending DN closure."
        )
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Optional[Dict[str, Any]]:
        """Get warehouse dashboard"""
        cache_key = f"warehouse_{warehouse_name.lower()}"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                func.count(distinct(DeliveryReport.customer_name)).label('dealers'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('cities'),
                func.count(distinct(DeliveryReport.customer_model)).label('products'),
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pending_pgi'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_pod'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pod_completed'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pgi_completed'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
                func.avg(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)),
                     DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                )).label('avg_pod_days'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue_per_dn'),
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse_name.lower()
            ).first()
            
            if not query:
                return None
            
            data = {
                'warehouse': warehouse_name,
                'dealer_count': int(query.dealers or 0),
                'city_count': int(query.cities or 0),
                'product_count': int(query.products or 0),
                'total_dn': int(query.total_dn or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pending_pgi': int(query.pending_pgi or 0),
                'pending_pod': int(query.pending_pod or 0),
                'pod_completed': int(query.pod_completed or 0),
                'pgi_completed': int(query.pgi_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
                'avg_revenue_per_dn': float(query.avg_revenue_per_dn or 0.0),
            }
            
            # Calculate percentages
            data['delivery_success_pct'] = _percent(
                data.get('pgi_completed', 0),
                data.get('total_dn', 0)
            )
            data['pod_success_pct'] = _percent(
                data.get('pod_completed', 0),
                data.get('total_dn', 0)
            )
            data['pgi_success_pct'] = _percent(
                data.get('pgi_completed', 0),
                data.get('total_dn', 0)
            )
            
            # Calculate warehouse score
            data['warehouse_score'] = self._calculate_warehouse_score(data)
            data['performance_grade'] = self._get_performance_grade(data['warehouse_score'])
            
            # SLA Compliance
            data['sla_compliance_pct'] = self._calculate_sla_compliance(warehouse_name)
            
            with self._lock:
                self._cache[cache_key] = data.copy()
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to get warehouse dashboard for {warehouse_name}: {e}")
            return None
    
    def _calculate_warehouse_score(self, data: Dict[str, Any]) -> float:
        """Calculate warehouse performance score"""
        scores = {
            'delivery': min(100, data.get('delivery_success_pct', 0) * 1.1),
            'pod': min(100, data.get('pod_success_pct', 0) * 1.1),
            'pending': max(0, 100 - (data.get('pending_dn', 0) / max(1, data.get('total_dn', 1)) * 100)),
            'revenue': min(100, data.get('total_revenue', 0) / 100000 * 10),  # Scale by 100K
        }
        
        weights = {
            'delivery': 0.30,
            'pod': 0.25,
            'pending': 0.25,
            'revenue': 0.20,
        }
        
        score = sum(scores[k] * weights[k] for k in scores.keys())
        return round(min(100, max(0, score)), 1)
    
    def _calculate_sla_compliance(self, warehouse_name: str) -> float:
        """Calculate SLA compliance for warehouse"""
        try:
            # Get warehouse coordinates
            warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse_name.lower())
            if not warehouse_coord:
                return 100.0  # Default if no coordinates
            
            # Get deliveries from this warehouse
            deliveries = self.session.query(
                DeliveryReport.ship_to_city,
                DeliveryReport.good_issue_date,
                DeliveryReport.dn_create_date,
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse_name.lower(),
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.dn_create_date.isnot(None),
            ).limit(1000).all()
            
            if not deliveries:
                return 100.0
            
            compliant = 0
            total = 0
            
            for delivery in deliveries:
                total += 1
                
                # Get city coordinates
                city_coord = WAREHOUSE_COORDINATES.get(delivery.ship_to_city.lower() if delivery.ship_to_city else "")
                if not city_coord:
                    compliant += 1
                    continue
                
                # Calculate distance
                distance = _calculate_distance(
                    warehouse_coord[0], warehouse_coord[1],
                    city_coord[0], city_coord[1]
                )
                
                # Get SLA target
                sla = _get_sla_target(distance)
                target_days = sla["target_days"]
                
                # Calculate actual days
                actual_days = (delivery.good_issue_date - delivery.dn_create_date).days
                
                if actual_days <= target_days:
                    compliant += 1
            
            return _percent(compliant, total) if total > 0 else 100.0
            
        except Exception as e:
            logger.error(f"Failed to calculate SLA compliance: {e}")
            return 100.0
    
    def get_warehouse_ranking(self, metric: str = "revenue", limit: int = 10) -> List[Dict[str, Any]]:
        """Get warehouse ranking by metric"""
        try:
            metric_map = {
                "revenue": (func.sum(DeliveryReport.dn_amount), "PKR {:,}"),
                "units": (func.sum(DeliveryReport.dn_qty), "{:,} units"),
                "dn": (func.count(distinct(DeliveryReport.dn_no)), "{:,} DN"),
                "delivery": (func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )), "{:.1f} days"),
                "pending": (func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))), "{:,} pending"),
            }
            
            if metric not in metric_map:
                metric = "revenue"
            
            agg_func, format_str = metric_map[metric]
            
            results = self.session.query(
                DeliveryReport.warehouse,
                agg_func.label('value')
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).order_by(
                desc('value')
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.warehouse:
                    value = float(row.value or 0) if metric in ["revenue", "units", "dn"] else float(row.value or 0)
                    if metric == "delivery":
                        value = float(row.value or 0)
                        display_value = format_str.format(value)
                    elif metric == "pending":
                        display_value = format_str.format(int(value))
                    else:
                        display_value = format_str.format(value)
                    
                    ranking.append({
                        'warehouse': _text(row.warehouse),
                        'value': display_value,
                        'raw_value': value,
                    })
            
            return ranking
            
        except Exception as e:
            logger.error(f"Failed to get warehouse ranking: {e}")
            return []
    
    def compare_warehouses(self, warehouse1: str, warehouse2: str) -> Dict[str, Any]:
        """Compare two warehouses"""
        dash1 = self.get_warehouse_dashboard(warehouse1)
        dash2 = self.get_warehouse_dashboard(warehouse2)
        
        if not dash1 or not dash2:
            return {}
        
        metrics = {}
        
        metrics[f"{warehouse1}_metrics"] = {
            "Revenue": f"PKR {dash1.get('total_revenue', 0):,.2f}",
            "Units": f"{dash1.get('total_units', 0):,}",
            "DN": f"{dash1.get('total_dn', 0):,}",
            "Dealers": f"{dash1.get('dealer_count', 0):,}",
            "Cities": f"{dash1.get('city_count', 0):,}",
            "Pending": f"{dash1.get('pending_dn', 0):,}",
            "Delivery": f"{dash1.get('delivery_success_pct', 0):.1f}%",
            "POD": f"{dash1.get('pod_success_pct', 0):.1f}%",
            "Score": f"{dash1.get('warehouse_score', 0):.1f}/100",
        }
        
        metrics[f"{warehouse2}_metrics"] = {
            "Revenue": f"PKR {dash2.get('total_revenue', 0):,.2f}",
            "Units": f"{dash2.get('total_units', 0):,}",
            "DN": f"{dash2.get('total_dn', 0):,}",
            "Dealers": f"{dash2.get('dealer_count', 0):,}",
            "Cities": f"{dash2.get('city_count', 0):,}",
            "Pending": f"{dash2.get('pending_dn', 0):,}",
            "Delivery": f"{dash2.get('delivery_success_pct', 0):.1f}%",
            "POD": f"{dash2.get('pod_success_pct', 0):.1f}%",
            "Score": f"{dash2.get('warehouse_score', 0):.1f}/100",
        }
        
        rev1 = dash1.get('total_revenue', 0)
        rev2 = dash2.get('total_revenue', 0)
        
        if rev1 > rev2:
            explanation = f"{warehouse1} has higher revenue than {warehouse2}"
        elif rev2 > rev1:
            explanation = f"{warehouse2} has higher revenue than {warehouse1}"
        else:
            explanation = f"{warehouse1} and {warehouse2} have similar revenue"
        
        metrics["explanation"] = explanation
        
        return metrics

# ============================================================
# BLOCK 10: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def __init__(self):
        self._menu_renderer = NationalMenuRenderer()
    
    def format(self, answer: NationalAnswer) -> str:
        """Format answer based on plan format"""
        if answer.plan.format == ResponseFormat.KPI_ONLY:
            return self._format_kpi_only(answer)
        elif answer.plan.format == ResponseFormat.COMPACT:
            return self._format_compact(answer)
        elif answer.plan.format == ResponseFormat.EXECUTIVE:
            return self._format_executive(answer)
        elif answer.plan.format == ResponseFormat.DETAILED:
            return self._format_detailed(answer)
        elif answer.plan.format == ResponseFormat.COMPARISON:
            return self._format_comparison(answer)
        elif answer.plan.format == ResponseFormat.RANKING:
            return self._format_ranking(answer)
        else:
            return self._format_standard(answer)
    
    def _format_kpi_only(self, answer: NationalAnswer) -> str:
        """KPI-only format"""
        lines = ["📊 *National KPIs*:"]
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"  {metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_compact(self, answer: NationalAnswer) -> str:
        """Compact format"""
        lines = ["🇵🇰 *National Logistics*"]
        lines.append("")
        
        for metric_name, value in answer.metrics.items():
            lines.append(f"{metric_name}: {value}")
        
        return "\n".join(lines)
    
    def _format_standard(self, answer: NationalAnswer) -> str:
        """Standard format"""
        return self._menu_renderer.render_national_dashboard(answer.dashboard or {})
    
    def _format_executive(self, answer: NationalAnswer) -> str:
        """Executive summary format"""
        return self._menu_renderer.render_executive_summary(answer.dashboard or {})
    
    def _format_detailed(self, answer: NationalAnswer) -> str:
        """Detailed format"""
        data = answer.dashboard or {}
        lines = [
            "📊 *Detailed National Analysis*",
            "",
            "📋 *Overview*",
            "─" * 40,
            f"Warehouses: {data.get('total_warehouses', 0):,}",
            f"Dealers: {data.get('total_dealers', 0):,}",
            f"Cities: {data.get('total_cities', 0):,}",
            f"Products: {data.get('total_products', 0):,}",
            "",
            "💰 *Financials*",
            "─" * 40,
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Avg Revenue/DN: PKR {data.get('avg_revenue_per_dn', 0):,.2f}",
            "",
            "📦 *Operations*",
            "─" * 40,
            f"DN: {data.get('total_dn', 0):,}",
            f"Units: {data.get('total_units', 0):,}",
            f"Pending: {data.get('pending_dn', 0):,}",
            "",
            "🚚 *Delivery*",
            "─" * 40,
            f"Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD: {data.get('pod_success_pct', 0):.1f}%",
            f"PGI: {data.get('pgi_success_pct', 0):.1f}%",
            "",
            "⭐ *Health*",
            "─" * 40,
            f"Score: {data.get('national_health_score', 0):.1f}/100",
            f"Grade: {data.get('performance_grade', 'N/A')}",
        ]
        
        if answer.insights:
            lines.append("")
            lines.append("💡 *Insights*")
            lines.append("─" * 40)
            for insight in answer.insights[:3]:
                lines.append(f"• {insight}")
        
        if answer.recommendations:
            lines.append("")
            lines.append("🎯 *Recommendations*")
            lines.append("─" * 40)
            for rec in answer.recommendations[:3]:
                lines.append(f"• {rec}")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    def _format_comparison(self, answer: NationalAnswer) -> str:
        """Comparison format"""
        return self._menu_renderer.render_comparison_result(
            answer.plan.warehouses[0] if answer.plan.warehouses else "",
            answer.plan.warehouses[1] if len(answer.plan.warehouses) > 1 else "",
            answer.metrics
        )
    
    def _format_ranking(self, answer: NationalAnswer) -> str:
        """Ranking format"""
        ranking_data = answer.metrics.get("ranking", [])
        return self._menu_renderer.render_ranking(ranking_data, answer.plan.sort_by or "revenue", answer.plan.limit)

# ============================================================
# BLOCK 11: MAIN NATIONAL KPI SERVICE WITH MENU
# ============================================================

class NationalKPIService:
    """
    National Logistics Intelligence Engine with Full Menu System
    Single entry point for all national logistics queries
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "national_kpi"
        self._version = "5.0.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = NationalMenuRenderer()
        self._formatter = ResponseFormatter()
        
        # Context memory
        self._contexts: Dict[str, NationalContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ NationalKPIService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅")
        logger.info(f"   Source of Truth: PostgreSQL")
        logger.info(f"   SLA Policy Engine: ✅")
        logger.info(f"   POD Policy Engine: ✅")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main national menu"""
        return self._menu_renderer.render_main_menu()
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response
        
        Returns:
            {
                "response": str,
                "menu_type": str,
                "action": str,
                "data": dict,
                "exit_menu": bool
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
    
    def _handle_main_menu_return(self, context: NationalContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_warehouses = []
        context.awaiting_warehouse = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "national_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True
        }
    
    def _handle_main_menu_option(self, context: NationalContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("national_dashboard", None),
            "2": ("warehouse_dashboard", "Enter warehouse name:"),
            "3": ("warehouse_ranking", None),
            "4": ("warehouse_comparison", None),
            "5": ("revenue", None),
            "6": ("units", None),
            "7": ("delivery", None),
            "8": ("pending", None),
            "9": ("pod", None),
            "10": ("pgi", None),
            "11": ("dealer_coverage", None),
            "12": ("city_analytics", "Enter city name:"),
            "13": ("product_distribution", "Enter product name:"),
            "14": ("sla_compliance", None),
            "15": ("executive_summary", None),
            "16": ("ai_insights", None),
            "17": ("recommendations", None),
            "18": ("health_score", None),
            "19": ("monthly_trend", None),
            "20": ("national_forecast", None),
        }
        
        if option == "2":
            context.menu_state = MenuState.WAREHOUSE_SELECTION
            context.selected_option = "warehouse_dashboard"
            context.awaiting_warehouse = True
            return {
                "response": self._menu_renderer.render_warehouse_selection(),
                "menu_type": "national_menu",
                "action": "warehouse_selection",
                "data": {},
                "exit_menu": False
            }
        elif option == "4":
            return self._handle_comparison_start(context)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        # Check if action requires warehouse selection
        if action in ["warehouse_dashboard", "city_analytics", "product_distribution"]:
            context.menu_state = MenuState.WAREHOUSE_SELECTION
            context.selected_option = action
            context.awaiting_warehouse = True
            return {
                "response": self._menu_renderer.render_warehouse_selection(prompt),
                "menu_type": "national_menu",
                "action": "warehouse_selection",
                "data": {"purpose": action},
                "exit_menu": False
            }
        
        # Execute action
        result = self._execute_national_action(context, action)
        result["exit_menu"] = False
        return result
    
    def _handle_warehouse_selection(self, context: NationalContext, warehouse_input: str) -> Dict[str, Any]:
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
                "menu_type": "national_menu",
                "action": "warehouse_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_warehouse = warehouse_name
        context.menu_state = MenuState.MAIN
        context.awaiting_warehouse = False
        
        action = context.selected_option or "warehouse_dashboard"
        result = self._execute_national_action_with_warehouse(context, action, warehouse_name)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: NationalContext, warehouse_input: str) -> Dict[str, Any]:
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
                "menu_type": "national_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_warehouses.append(warehouse_name)
        
        if len(context.comparison_warehouses) == 1:
            return {
                "response": "\n".join([
                    f"✅ First warehouse selected: {warehouse_name}",
                    "",
                    "Enter second warehouse name:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "national_menu",
                "action": "comparison_second",
                "data": {"first_warehouse": warehouse_name},
                "exit_menu": False
            }
        else:
            warehouse1, warehouse2 = context.comparison_warehouses[0], context.comparison_warehouses[1]
            context.menu_state = MenuState.MAIN
            context.comparison_warehouses = []
            return self._perform_warehouse_comparison(context, warehouse1, warehouse2)
    
    def _handle_comparison_start(self, context: NationalContext) -> Dict[str, Any]:
        """Start comparison process"""
        context.menu_state = MenuState.COMPARISON_SELECTION
        context.comparison_warehouses = []
        return {
            "response": self._menu_renderer.render_comparison_selection(),
            "menu_type": "national_menu",
            "action": "comparison_start",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_quick_query(self, context: NationalContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            import re
            warehouses = re.findall(r'([\w\s]+?)(?:and|vs|versus)([\w\s]+)', query, re.IGNORECASE)
            if warehouses:
                warehouse1 = self._resolve_warehouse_name(warehouses[0][0].strip())
                warehouse2 = self._resolve_warehouse_name(warehouses[0][1].strip())
                if warehouse1 and warehouse2:
                    return self._perform_warehouse_comparison(context, warehouse1, warehouse2)
        
        # Check if it's a warehouse query
        warehouse_name = self._resolve_warehouse_name(query)
        if warehouse_name:
            context.current_warehouse = warehouse_name
            return self._get_warehouse_dashboard(context, warehouse_name)
        
        # Check for ranking query
        if "top" in query.lower() and ("warehouse" in query.lower() or "warehouses" in query.lower()):
            return self._get_warehouse_ranking(context)
        
        # Check for specific metrics
        if "revenue" in query.lower() or "pending" in query.lower() or "delivery" in query.lower():
            return self._execute_national_action(context, "national_dashboard")
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• National Dashboard",
                "• Warehouse Lahore",
                "• Compare Lahore and Karachi",
                "• Top warehouses by revenue",
                "• National delivery performance",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "national_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _resolve_warehouse_name(self, input_text: str) -> Optional[str]:
        """Resolve warehouse name from input"""
        input_lower = input_text.lower().strip()
        
        warehouses = ["Lahore", "Karachi", "Rawalpindi", "Multan", "Peshawar", 
                     "Hyderabad", "Quetta", "Faisalabad", "Sialkot", "Gujranwala"]
        
        # Direct match
        for warehouse in warehouses:
            if warehouse.lower() == input_lower:
                return warehouse
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            matches = process.extract(input_lower, warehouses, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= 85:
                return matches[0][0]
        
        # Partial match
        for warehouse in warehouses:
            if len(input_lower) >= 3:
                if input_lower[:3] in warehouse.lower() or warehouse.lower()[:3] in input_lower:
                    return warehouse
        
        return None
    
    def _get_context(self, session_id: str) -> NationalContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = NationalContext()
            return self._contexts[session_id]
    
    def _execute_national_action(self, context: NationalContext, action: str) -> Dict[str, Any]:
        """Execute national action"""
        action_map = {
            "national_dashboard": self._get_national_dashboard,
            "warehouse_ranking": self._get_warehouse_ranking,
            "revenue": self._get_national_dashboard,
            "units": self._get_national_dashboard,
            "delivery": self._get_national_dashboard,
            "pending": self._get_national_dashboard,
            "pod": self._get_national_dashboard,
            "pgi": self._get_national_dashboard,
            "dealer_coverage": self._get_national_dashboard,
            "sla_compliance": self._get_national_dashboard,
            "executive_summary": self._get_executive_summary,
            "ai_insights": self._get_ai_insights,
            "recommendations": self._get_recommendations,
            "health_score": self._get_health_score,
            "monthly_trend": self._get_monthly_trend,
            "national_forecast": self._get_national_forecast,
        }
        
        handler = action_map.get(action, self._get_national_dashboard)
        return handler(context)
    
    def _execute_national_action_with_warehouse(self, context: NationalContext, action: str, warehouse_name: str) -> Dict[str, Any]:
        """Execute national action with warehouse"""
        if action == "warehouse_dashboard":
            return self._get_warehouse_dashboard(context, warehouse_name)
        elif action == "city_analytics":
            return self._get_city_analytics(context, warehouse_name)
        elif action == "product_distribution":
            return self._get_product_distribution(context, warehouse_name)
        else:
            return self._get_warehouse_dashboard(context, warehouse_name)
    
    # ============================================================
    # NATIONAL OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_national_dashboard(self, context: NationalContext) -> Dict[str, Any]:
        """Get national dashboard"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ National dashboard data not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "national_dashboard",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_national_dashboard(dashboard),
                    "menu_type": "national_menu",
                    "action": "national_dashboard",
                    "data": {"dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"National dashboard error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_dashboard(self, context: NationalContext, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dashboard"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_warehouse_dashboard(warehouse_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ Warehouse '{warehouse_name}' not found.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "warehouse_dashboard",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_warehouse_dashboard(warehouse_name, dashboard),
                    "menu_type": "national_menu",
                    "action": "warehouse_dashboard",
                    "data": {"warehouse": warehouse_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Warehouse dashboard error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_warehouse_ranking(self, context: NationalContext) -> Dict[str, Any]:
        """Get warehouse ranking"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                ranking = repository.get_warehouse_ranking("revenue", 10)
                
                if not ranking:
                    return {
                        "response": "🏆 *Warehouse Rankings*\n\nNo warehouses found.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "warehouse_ranking",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "national_menu",
                    "action": "warehouse_ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Warehouse ranking error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_warehouse_comparison(self, context: NationalContext, warehouse1: str, warehouse2: str) -> Dict[str, Any]:
        """Perform warehouse comparison"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                metrics = repository.compare_warehouses(warehouse1, warehouse2)
                
                if not metrics:
                    return {
                        "response": "⚠️ One or both warehouses not found.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "comparison_error",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_comparison_result(warehouse1, warehouse2, metrics),
                    "menu_type": "national_menu",
                    "action": "warehouse_comparison",
                    "data": {"warehouse1": warehouse1, "warehouse2": warehouse2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Warehouse comparison error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_executive_summary(self, context: NationalContext) -> Dict[str, Any]:
        """Get executive summary"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Executive summary not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "executive_summary",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_executive_summary(dashboard),
                    "menu_type": "national_menu",
                    "action": "executive_summary",
                    "data": {"dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Executive summary error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_ai_insights(self, context: NationalContext) -> Dict[str, Any]:
        """Get AI insights"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Insights not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "ai_insights",
                        "data": {},
                        "exit_menu": False
                    }
                
                insights = dashboard.get('insights', [])
                if not insights:
                    insights = ["No insights available at this time."]
                
                return {
                    "response": "\n".join([
                        "💡 *National AI Insights*",
                        "",
                        "\n".join(f"• {insight}" for insight in insights[:5]),
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "national_menu",
                    "action": "ai_insights",
                    "data": {"insights": insights},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"AI insights error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_recommendations(self, context: NationalContext) -> Dict[str, Any]:
        """Get recommendations"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Recommendations not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "recommendations",
                        "data": {},
                        "exit_menu": False
                    }
                
                recommendations = dashboard.get('recommendations', [])
                if not recommendations:
                    recommendations = ["No recommendations available at this time."]
                
                return {
                    "response": "\n".join([
                        "🎯 *National Recommendations*",
                        "",
                        "\n".join(f"• {rec}" for rec in recommendations[:5]),
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "national_menu",
                    "action": "recommendations",
                    "data": {"recommendations": recommendations},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Recommendations error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_health_score(self, context: NationalContext) -> Dict[str, Any]:
        """Get national health score"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Health score not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "health_score",
                        "data": {},
                        "exit_menu": False
                    }
                
                # Calculate component scores
                health_data = {
                    'national_health_score': dashboard.get('national_health_score', 0),
                    'performance_grade': dashboard.get('performance_grade', 'N/A'),
                    'delivery_score': min(100, dashboard.get('delivery_success_pct', 0) * 1.1),
                    'warehouse_score': 85,  # Placeholder
                    'dealer_score': 80,     # Placeholder
                    'revenue_score': min(100, dashboard.get('total_revenue', 0) / 1000000 * 10),
                    'pod_score': min(100, dashboard.get('pod_success_pct', 0) * 1.1),
                    'pgi_score': min(100, dashboard.get('pgi_success_pct', 0) * 1.1),
                    'pending_score': max(0, 100 - (dashboard.get('pending_dn', 0) / max(1, dashboard.get('total_dn', 1)) * 100)),
                }
                
                return {
                    "response": self._menu_renderer.render_health_score(health_data),
                    "menu_type": "national_menu",
                    "action": "health_score",
                    "data": {"health": health_data},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Health score error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_monthly_trend(self, context: NationalContext) -> Dict[str, Any]:
        """Get monthly trend"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Trend data not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "monthly_trend",
                        "data": {},
                        "exit_menu": False
                    }
                
                return {
                    "response": "\n".join([
                        "📈 *National Monthly Trend*",
                        "",
                        f"Current Month Revenue: PKR {dashboard.get('current_month_revenue', 0):,.2f}",
                        f"Previous Month Revenue: PKR {dashboard.get('previous_month_revenue', 0):,.2f}",
                        f"Growth Rate: {dashboard.get('growth_rate', 0):+.1f}%",
                        "",
                        f"Current Month Units: {dashboard.get('total_units', 0):,}",
                        f"Current Month DN: {dashboard.get('total_dn', 0):,}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "national_menu",
                    "action": "monthly_trend",
                    "data": {"trend": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Monthly trend error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_national_forecast(self, context: NationalContext) -> Dict[str, Any]:
        """Get national forecast"""
        try:
            with self._session() as session:
                repository = NationalRepository(session)
                dashboard = repository.get_national_dashboard()
                
                if not dashboard:
                    return {
                        "response": "⚠️ Forecast data not available.\n\n0. Main Menu",
                        "menu_type": "national_menu",
                        "action": "national_forecast",
                        "data": {},
                        "exit_menu": False
                    }
                
                revenue = dashboard.get('current_month_revenue', 0)
                growth = dashboard.get('growth_rate', 0)
                forecast_revenue = revenue * (1 + growth / 100)
                
                return {
                    "response": "\n".join([
                        "🔮 *National Forecast*",
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
                    "menu_type": "national_menu",
                    "action": "national_forecast",
                    "data": {"forecast": forecast_revenue},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"National forecast error: {e}")
            return {
                "response": f"⚠️ Service error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "national_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_analytics(self, context: NationalContext, city_name: str) -> Dict[str, Any]:
        """Get city analytics"""
        # Placeholder - would need additional queries
        return {
            "response": f"🏙️ *City Analytics - {city_name}*\n\nComing soon!\n\n0. Main Menu\n99. Back",
            "menu_type": "national_menu",
            "action": "city_analytics",
            "data": {"city": city_name},
            "exit_menu": False
        }
    
    def _get_product_distribution(self, context: NationalContext, product_name: str) -> Dict[str, Any]:
        """Get product distribution"""
        # Placeholder - would need additional queries
        return {
            "response": f"📦 *Product Distribution - {product_name}*\n\nComing soon!\n\n0. Main Menu\n99. Back",
            "menu_type": "national_menu",
            "action": "product_distribution",
            "data": {"product": product_name},
            "exit_menu": False
        }
    
    # ============================================================
    # LEGACY METHODS - BACKWARD COMPATIBILITY
    # ============================================================
    
    def get_national_kpi_dashboard(self, **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = NationalContext()
        result = self._get_national_dashboard(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
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
                "sla_policy": True,
                "pod_policy": True,
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
# BLOCK 12: SERVICE SINGLETON
# ============================================================

_service: Optional[NationalKPIService] = None
_service_lock = threading.Lock()

def get_national_kpi_service() -> NationalKPIService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = NationalKPIService()
    return _service

def process_national_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process national menu input for WhatsApp integration"""
    service = get_national_kpi_service()
    return service.process_menu_input(session_id, user_input)

def get_national_main_menu() -> str:
    """Get the main national menu for WhatsApp"""
    service = get_national_kpi_service()
    return service.get_main_menu()

# ============================================================
# BLOCK 13: EXPORTS
# ============================================================

__all__ = [
    "NationalKPIService",
    "NationalContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_national_kpi_service",
    "process_national_menu",
    "get_national_main_menu",
    "NationalMenuRenderer",
    "get_national_kpi_dashboard",
    "health_check",
]
