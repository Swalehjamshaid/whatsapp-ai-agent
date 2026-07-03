"""
File: app/services/city_service.py
Version: 5.3 - ENTERPRISE CITY DOMAIN AI EXPERT WITH FULL MENU
Purpose: Answer ANY city-related business question through a single entry point
         PostgreSQL is the ONLY source of truth.
         Full menu system with 15+ options, sub-menus, and AI-powered queries

FIXES:
- ✅ Added process_city_menu_input method for sub-menu navigation
- ✅ Fixed CityAnalyticsService attribute error
- ✅ Enhanced menu state management
- ✅ Improved error handling

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

CACHE_TTL = max(60, int(os.getenv("CITY_ANALYTICS_CACHE_TTL", "300")))
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

CITY_ALIASES: dict[str, str] = {
    "rwp": "rawalpindi",
    "isb": "islamabad",
    "lhr": "lahore",
    "khi": "karachi",
    "fsd": "faisalabad",
    "hyd": "hyderabad",
    "ryk": "rahim yar khan",
}

CITY_NAMES: list[str] = [
    "abbottabad", "lahore", "karachi", "rawalpindi", "quetta",
    "multan", "peshawar", "gilgit", "hyderabad", "islamabad",
    "sialkot", "gujranwala", "faisalabad", "bahawalpur", "sukkur",
    "dg khan", "rahim yar khan", "gwadar"
]

CITY_EMOJIS: Dict[str, str] = {
    "lahore": "🏛️", "karachi": "🌊", "rawalpindi": "🏔️", "islamabad": "🏛️",
    "multan": "🌅", "peshawar": "🏔️", "quetta": "🏜️", "faisalabad": "🏭",
    "hyderabad": "🌊", "sialkot": "⚽", "gujranwala": "🏭", "bahawalpur": "🌴",
    "sukkur": "🌊", "dg khan": "🏔️", "rahim yar khan": "🌾", "abbottabad": "🏔️",
    "gwadar": "🌊", "gilgit": "🏔️"
}

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """City question intent types"""
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    DELIVERY = "delivery"
    POD = "pod"
    PGI = "pgi"
    TOP_PRODUCT = "top_product"
    TOP_MODEL = "top_model"
    TOP_DEALER = "top_dealer"
    GROWTH = "growth"
    COMPARISON = "comparison"
    RANK = "rank"
    DISTANCE = "distance"
    FORECAST = "forecast"
    SUMMARY = "summary"
    BUSINESS_SCORE = "business_score"
    RISK_SCORE = "risk_score"
    DEALERS = "dealers"
    AVERAGE = "average"
    RANKING = "ranking"
    RECOMMENDATIONS = "recommendations"
    INSIGHTS = "insights"
    TREND = "trend"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu navigation states"""
    MAIN = "main"
    CITY_SELECTION = "city_selection"
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
class CityContext:
    """Session context for city queries"""
    current_city: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    last_metrics: List[str] = field(default_factory=list)
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_cities: List[str] = field(default_factory=list)
    awaiting_city: bool = False
    awaiting_comparison: bool = False
    
    def set_city(self, city: str) -> None:
        self.current_city = city
    
    def get_city(self) -> Optional[str]:
        return self.current_city
    
    def clear(self) -> None:
        self.current_city = None
        self.last_question = None
        self.last_intent = None
        self.last_metrics = []
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_cities = []
        self.awaiting_city = False
        self.awaiting_comparison = False

@dataclass
class QueryPlan:
    """Query execution plan"""
    intent: IntentType
    city: Optional[str] = None
    cities: List[str] = field(default_factory=list)
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
            "city": self.city,
            "cities": self.cities,
            "metrics": self.metrics,
            "timeframe": self.timeframe,
            "limit": self.limit,
            "format": self.format,
            "confidence": self.confidence,
        }

@dataclass
class CityAnswer:
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

def get_city_emoji(city_name: str) -> str:
    """Get emoji for city"""
    return CITY_EMOJIS.get(city_name.lower(), "📍")

# ============================================================
# BLOCK 7: MENU SYSTEM
# ============================================================

class CityMenuRenderer:
    """Render city analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main city menu"""
        return "\n".join([
            "🏙️ *CITY ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. City Dashboard",
            "2. City Revenue",
            "3. City Units",
            "4. City Pending",
            "5. City Delivery",
            "6. Compare Cities",
            "7. City Rankings",
            "8. Top Products",
            "9. Business Score",
            "10. Distance Info",
            "11. Growth Analytics",
            "12. City Summary",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type city name for dashboard",
            "• Compare Lahore Karachi",
            "• Top cities by revenue",
            "",
            "Reply with a number or city name:"
        ])
    
    @staticmethod
    def render_city_selection(prompt: str = "Enter city name:") -> str:
        """Render city selection prompt"""
        return "\n".join([
            "🏙️ *City Selection*",
            "",
            prompt,
            "",
            "💡 *Available Cities:*",
            "Lahore, Karachi, Rawalpindi, Islamabad, Multan",
            "Peshawar, Quetta, Faisalabad, Hyderabad, Sialkot",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        """Render comparison city selection"""
        return "\n".join([
            "🔄 *Compare Cities*",
            "",
            "Enter first city name:",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render city rankings"""
        lines = [
            f"🏆 *City Rankings by {metric.title()}*",
            "",
        ]
        
        for i, item in enumerate(ranking[:limit], 1):
            city = item.get('city', 'Unknown')
            value = item.get('value', 'N/A')
            emoji = get_city_emoji(city)
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {emoji} {city.title()}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(city1: str, city2: str, metrics: Dict[str, Any]) -> str:
        """Render comparison result"""
        emoji1 = get_city_emoji(city1)
        emoji2 = get_city_emoji(city2)
        
        lines = [
            f"🔄 *Comparison: {emoji1} {city1.title()} vs {emoji2} {city2.title()}*",
            "",
            "───────────────────",
            "",
        ]
        
        # Get metrics for both cities
        metrics1 = metrics.get(f"{city1}_metrics", {})
        metrics2 = metrics.get(f"{city2}_metrics", {})
        
        all_keys = set(metrics1.keys()) | set(metrics2.keys())
        
        for key in sorted(all_keys):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            
            # Determine winner
            if isinstance(v1, str) and isinstance(v2, str):
                # Try to extract numeric values for comparison
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
        
        # Add summary
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
    def render_city_dashboard(city_name: str, dashboard: Dict[str, Any]) -> str:
        """Render city dashboard"""
        emoji = get_city_emoji(city_name)
        
        lines = [
            f"{emoji} *{city_name.title()} Dashboard*",
            "",
            "📊 *Key Metrics*",
            f"Revenue: PKR {dashboard.get('total_revenue', 0):,.2f}",
            f"Units: {dashboard.get('total_units', 0):,}",
            f"DN: {dashboard.get('total_dn', 0):,}",
            f"Dealers: {dashboard.get('total_dealers', 0):,}",
            f"Pending DN: {dashboard.get('pending_dn', 0):,}",
            "",
            "🚚 *Delivery*",
            f"Success Rate: {dashboard.get('delivery_success_pct', 0):.1f}%",
            f"Average Days: {dashboard.get('avg_delivery', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Business Score: {dashboard.get('business_score', 0):.1f}/100",
            f"Status: {dashboard.get('overall_status', 'Unknown')}",
            f"Grade: {dashboard.get('performance_grade', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Try:* 'Revenue in [city]' or 'Pending in [city]'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_summary(city_name: str, data: Dict[str, Any]) -> str:
        """Render pending summary"""
        emoji = get_city_emoji(city_name)
        
        return "\n".join([
            f"⏳ *Pending Summary - {emoji} {city_name.title()}*",
            "",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            f"Pending Revenue: PKR {data.get('pending_revenue', 0):,.2f}",
            f"Pending Units: {data.get('pending_units', 0):,}",
            f"PGI Pending: {data.get('pgi_pending_dn', 0):,}",
            f"POD Pending: {data.get('pod_pending_dn', 0):,}",
            "",
            f"Avg Pending Days: {data.get('pending_average_days', 0):.1f}",
            f"Critical (>7 days): {data.get('critical_pending', 0):,}",
            f"Overdue (>14 days): {data.get('overdue_pending', 0):,}",
            f"Oldest Pending DN: {data.get('oldest_pending_dn', 'N/A')}",
            f"Oldest Pending Days: {data.get('oldest_pending_days', 0):,}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_delivery_summary(city_name: str, data: Dict[str, Any]) -> str:
        """Render delivery summary"""
        emoji = get_city_emoji(city_name)
        
        return "\n".join([
            f"🚚 *Delivery Summary - {emoji} {city_name.title()}*",
            "",
            f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
            f"Average Days: {data.get('avg_delivery', 0):.1f}",
            f"Fastest: {data.get('fastest_delivery', 0):.1f} Days",
            f"Slowest: {data.get('slowest_delivery', 0):.1f} Days",
            f"Same Day: {data.get('same_day_deliveries', 0):,}",
            f"Next Day: {data.get('next_day_deliveries', 0):,}",
            "",
            f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
            f"POD Average: {data.get('avg_pod', 0):.1f} Days",
            f"Cycle Time: {data.get('avg_cycle', 0):.1f} Days",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_distance_info(city_name: str, distance_data: Dict[str, Any]) -> str:
        """Render distance information"""
        emoji = get_city_emoji(city_name)
        warehouse = distance_data.get('warehouse', 'Unknown')
        
        return "\n".join([
            f"📍 *Distance Info - {emoji} {city_name.title()}*",
            "",
            f"Warehouse: {warehouse}",
            f"Distance: {distance_data.get('distance_km', 'N/A')} KM",
            f"Driving Time: {distance_data.get('driving_time', 'N/A')}",
            f"Est. Delivery: {distance_data.get('estimated_delivery', 'N/A')}",
            f"Source: {distance_data.get('source', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_growth_summary(city_name: str, data: Dict[str, Any]) -> str:
        """Render growth summary"""
        emoji = get_city_emoji(city_name)
        
        return "\n".join([
            f"📈 *Growth Analytics - {emoji} {city_name.title()}*",
            "",
            f"Monthly Growth: {data.get('monthly_growth', 0):+.1f}%",
            f"Revenue Growth: {data.get('revenue_growth_pct', 0):+.1f}%",
            "",
            f"Current Month Revenue: PKR {data.get('current_month_revenue', 0):,.2f}",
            f"Previous Month Revenue: PKR {data.get('previous_month_revenue', 0):,.2f}",
            "",
            f"Best Month: {data.get('best_month', 'N/A')}",
            f"Worst Month: {data.get('worst_month', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_business_score(city_name: str, data: Dict[str, Any]) -> str:
        """Render business score"""
        emoji = get_city_emoji(city_name)
        
        return "\n".join([
            f"📈 *Business Score - {emoji} {city_name.title()}*",
            "",
            f"Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            f"Risk Score: {data.get('risk_score', 0):.1f}/100",
            "",
            f"Strengths: {len(data.get('strengths', []))} identified",
            f"Weaknesses: {len(data.get('weaknesses', []))} identified",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_summary(city_name: str, data: Dict[str, Any]) -> str:
        """Render executive summary"""
        emoji = get_city_emoji(city_name)
        
        lines = [
            f"📋 *Executive Summary - {emoji} {city_name.title()}*",
            "",
            data.get('executive_summary', 'Summary not available.'),
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Score: {data.get('business_score', 0):.1f}/100",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Growth: {data.get('monthly_growth', 0):+.1f}%",
            f"Pending: {data.get('pending_dn', 0):,} DN",
            f"Dealers: {data.get('total_dealers', 0):,}",
        ]
        
        insights = data.get('insights', [])
        if insights:
            lines.append("")
            lines.append("💡 *Key Insights*")
            for insight in insights[:3]:
                lines.append(f"• {insight}")
        
        recommendations = data.get('recommendations', [])
        if recommendations:
            lines.append("")
            lines.append("🎯 *Recommendations*")
            for rec in recommendations[:3]:
                lines.append(f"• {rec}")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_top_products(city_name: str, data: Dict[str, Any]) -> str:
        """Render top products"""
        emoji = get_city_emoji(city_name)
        
        return "\n".join([
            f"🏷️ *Top Products - {emoji} {city_name.title()}*",
            "",
            f"Top Product: {data.get('top_product', 'N/A')}",
            f"Top Model: {data.get('top_model', 'N/A')}",
            f"Top Division: {data.get('top_division', 'N/A')}",
            f"Top Material: {data.get('top_material', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# BLOCK 8: INTENT ENGINE
# ============================================================

class IntentEngine:
    """AI-powered intent detection for city questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|tell|get).*(?:city|dashboard|profile)",
            r"(?:how is|what about).*city",
            r"city (?:dashboard|profile|analytics|performance|status)",
            r"tell me about (?:city|dashboard)",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income|turnover|collection|earnings)",
            r"(?:how much|what is).*(?:revenue|sale|income)",
            r"revenue (?:by|in|for|from)",
            r"total (?:revenue|sales)",
            r"(?:highest|lowest|top|bottom).*(?:revenue|sales)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|qty|volume|pieces|items)",
            r"(?:how many|number of).*(?:units|quantity|pieces)",
            r"units (?:sold|delivered|shipped)",
        ],
        IntentType.PENDING: [
            r"(?:pending|outstanding|backlog|overdue|delayed)",
            r"(?:delayed|unfulfilled).*(?:dn|order)",
            r"pending (?:dn|order|delivery)",
            r"(?:critical|urgent).*(?:pending|overdue)",
        ],
        IntentType.DELIVERY: [
            r"(?:delivery|dispatch|shipping|transit)",
            r"(?:delivery|dispatch) (?:time|duration|days|performance)",
            r"average delivery",
            r"(?:fastest|slowest|same day|next day).*(?:delivery)",
        ],
        IntentType.POD: [
            r"pod",
            r"(?:proof of delivery|delivery confirmation)",
            r"(?:pod|delivery proof) (?:rate|status|completion)",
        ],
        IntentType.PGI: [
            r"pgi",
            r"(?:goods issue|dispatch issue)",
            r"pgi (?:rate|status|pending)",
        ],
        IntentType.TOP_PRODUCT: [
            r"top (?:product|material|model|item|product)",
            r"(?:best|leading|highest).*(?:product|material|model)",
            r"what is the top (?:product|model)",
        ],
        IntentType.GROWTH: [
            r"(?:growth|trend|increase|decrease|change)",
            r"(?:monthly|quarterly|yearly) (?:growth|trend)",
            r"growth (?:rate|percentage|pct)",
        ],
        IntentType.COMPARISON: [
            r"compare|vs|versus|between",
            r"(?:comparison|compare) (?:between|of)",
            r"vs\s+(\w+)\s+and\s+(\w+)",
        ],
        IntentType.RANK: [
            r"(?:rank|ranking|position|standing|order)",
            r"(?:top|best|highest|lowest|worst)",
            r"ranked|ranking by",
            r"(?:top|bottom)\s+(\d+)\s+(?:cities|city)",
        ],
        IntentType.DISTANCE: [
            r"(?:distance|travel|driving|route)",
            r"(?:how far|distance from|between)",
        ],
        IntentType.BUSINESS_SCORE: [
            r"(?:business|health|performance).*(?:score|rating)",
            r"business (?:health|score)",
            r"overall (?:performance|health)",
        ],
        IntentType.RISK_SCORE: [
            r"(?:risk|vulnerability|exposure).*(?:score|rating)",
            r"risk (?:score|level|assessment)",
        ],
        IntentType.DEALERS: [
            r"(?:dealer|dealers|dealership|customer)",
            r"(?:number of|total) (?:dealer|customer)",
            r"dealer (?:network|base|count)",
        ],
        IntentType.AVERAGE: [
            r"(?:average|avg|mean|typical)",
            r"(?:per|each) (?:dealer|dn|unit|order)",
            r"average (?:revenue|units|delivery|order)",
        ],
        IntentType.SUMMARY: [
            r"(?:summary|overview|brief|condense)",
            r"executive (?:summary|overview)",
        ],
        IntentType.RECOMMENDATIONS: [
            r"(?:recommendation|suggest|advice|improve)",
            r"what should (?:i|we) (?:do|improve)",
            r"(?:how to|way to) improve",
        ],
        IntentType.INSIGHTS: [
            r"(?:insight|key insight|analysis|observation)",
            r"what (?:does|is) (?:the|this) (?:mean|tell)",
            r"(?:why|explain)",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|seasonal|period)",
            r"(?:over time|historical|trajectory)",
        ],
        IntentType.MENU: [
            r"menu",
            r"city menu",
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
                    Route(name="city_dashboard", utterances=[
                        "show city", "city dashboard", "how is city", "city performance"
                    ]),
                    Route(name="city_revenue", utterances=[
                        "city revenue", "sales in city", "how much revenue", "city income"
                    ]),
                    Route(name="city_pending", utterances=[
                        "pending in city", "overdue orders", "backlog", "pending delivery"
                    ]),
                    Route(name="city_comparison", utterances=[
                        "compare cities", "city vs city", "comparison", "versus"
                    ]),
                    Route(name="city_units", utterances=[
                        "units sold", "quantity", "pieces", "volume"
                    ]),
                    Route(name="city_delivery", utterances=[
                        "delivery time", "delivery days", "transit", "shipping"
                    ]),
                    Route(name="city_growth", utterances=[
                        "growth", "trend", "increase", "decrease", "change"
                    ]),
                    Route(name="city_summary", utterances=[
                        "summary", "overview", "executive summary", "brief"
                    ]),
                    Route(name="city_menu", utterances=[
                        "menu", "city menu", "options", "help", "show menu"
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
        if question_lower in ["menu", "city menu", "options", "help", "show menu"]:
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
                    intent_name = result.name.replace("city_", "")
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
                    best_intent = IntentType.PENDING
                    best_score = 0.5
                    break
                elif keyword in ["delivery", "delivered", "transit"]:
                    best_intent = IntentType.DELIVERY
                    best_score = 0.5
                    break
                elif keyword in ["compare", "vs", "versus"]:
                    best_intent = IntentType.COMPARISON
                    best_score = 0.6
                    break
                elif keyword in ["units", "quantity", "pieces"]:
                    best_intent = IntentType.UNITS
                    best_score = 0.5
                    break
                elif keyword in ["menu", "help", "options"]:
                    best_intent = IntentType.MENU
                    best_score = 0.8
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 9: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for city questions"""
    
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
            "cities": [],
            "metrics": [],
            "timeframe": None,
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_cities": [],
            "requires_comparison": False,
        }
        
        # Extract cities
        cities = self._extract_cities(question_lower)
        if cities:
            entities["cities"] = cities
        
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
            if len(entities["cities"]) >= 2:
                entities["comparison_cities"] = entities["cities"][:2]
        
        # Extract sort order
        if "highest" in question_lower or "top" in question_lower:
            entities["order"] = "desc"
        elif "lowest" in question_lower or "bottom" in question_lower:
            entities["order"] = "asc"
        
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_cities(self, text: str) -> List[str]:
        """Extract city names from text"""
        found = []
        
        # Direct matches
        for city in CITY_NAMES:
            if city in text:
                found.append(city)
        
        # Alias matches
        for alias, city in CITY_ALIASES.items():
            if alias in text and city not in found:
                found.append(city)
        
        # Fuzzy match for partials
        if not found and RAPIDFUZZ_AVAILABLE:
            for city in CITY_NAMES:
                if len(city) >= 3:
                    if city[:3] in text or city[:4] in text:
                        found.append(city)
        
        return list(dict.fromkeys(found))
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income", "turnover"],
            "units": ["units", "quantity", "qty", "volume", "pieces"],
            "pending": ["pending", "backlog", "overdue"],
            "delivery": ["delivery", "transit", "shipping"],
            "pod": ["pod", "proof of delivery"],
            "pgi": ["pgi", "goods issue"],
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
            r"(\d+)\s+(?:cities|dealers|items)",
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
# BLOCK 10: CITY DASHBOARD BUILDER
# ============================================================

class CityDashboardBuilder:
    """Build city dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def build(self, city_name: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for city"""
        cache_key = city_name.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                func.max(DeliveryReport.ship_to_city).label("city_name"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.division).label("division"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
                func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending_dn"),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("completed_dn"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)))).label("pgi_pending_dn"),
                func.count(distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pod_pending_dn"),
                func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
                func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
                func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
                func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city_name.lower()
            ).group_by(
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            completed_dn = int(query.completed_dn or 0)
            
            dashboard = {
                "city_name": _text(query.city_name),
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "division": _text(query.division),
                "total_dealers": int(query.total_dealers or 0),
                "total_dn": total_dn,
                "completed_dn": completed_dn,
                "pending_dn": pending_dn,
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "pgi_pending_dn": int(query.pgi_pending_dn or 0),
                "pod_pending_dn": int(query.pod_pending_dn or 0),
                "first_delivery_date": _date_text(query.first_delivery_date),
                "latest_delivery_date": _date_text(query.latest_delivery_date),
                "avg_delivery": _days(query.avg_delivery),
                "avg_pod": _days(query.avg_pod),
                "avg_cycle": _days(query.avg_cycle),
                "delivery_success_pct": _percent(completed_dn, total_dn),
                "pending_pct": _percent(pending_dn, total_dn),
                "pgi_success_pct": _percent(query.pgi_pending_dn or 0, total_dn) if total_dn > 0 else 0,
                "pod_success_pct": _percent(query.pod_pending_dn or 0, total_dn) if total_dn > 0 else 0,
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
            
            # Distance
            warehouse = _text(query.warehouse)
            distance = self._calculate_distance(warehouse, city_name)
            dashboard["distance"] = distance
            
            # Monthly analytics
            monthly = self._get_monthly_analytics(city_name)
            if monthly:
                dashboard.update(monthly)
            
            # Product analytics
            products = self._get_product_analytics(city_name)
            if products:
                dashboard.update(products)
            
            # Pending analytics
            pending = self._get_pending_analytics(city_name)
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
            logger.error(f"Failed to build dashboard for {city_name}: {e}")
            return None
    
    def _calculate_distance(self, warehouse: str, city: str) -> Dict[str, Any]:
        """Calculate distance between warehouse and city"""
        result = {"distance_km": None, "driving_time": "Unknown", "source": "unavailable", "warehouse": warehouse}
        
        warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse.lower())
        city_coord = WAREHOUSE_COORDINATES.get(city.lower())
        
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
            
            # Estimate driving time
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
        
        return result
    
    def _get_monthly_analytics(self, city_name: str) -> Dict[str, Any]:
        """Get monthly analytics"""
        try:
            condition = func.lower(DeliveryReport.ship_to_city) == city_name.lower()
            
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
                "current_month_dn": int(current_row.dns) if current_row else 0,
                "previous_month_dn": int(previous_row.dns) if previous_row else 0,
                "best_month": _text(best.month),
                "worst_month": _text(worst.month),
                "revenue_growth_pct": _growth(current_revenue, previous_revenue),
            }
        except Exception:
            return {}
    
    def _get_product_analytics(self, city_name: str) -> Dict[str, Any]:
        """Get product analytics"""
        try:
            condition = func.lower(DeliveryReport.ship_to_city) == city_name.lower()
            
            top_model = self.session.query(
                DeliveryReport.customer_model.label("model"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.customer_model.isnot(None)).group_by(
                DeliveryReport.customer_model
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            top_material = self.session.query(
                DeliveryReport.material_no.label("material"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.material_no.isnot(None)).group_by(
                DeliveryReport.material_no
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            top_division = self.session.query(
                DeliveryReport.division.label("division"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, DeliveryReport.division.isnot(None)).group_by(
                DeliveryReport.division
            ).order_by(func.sum(DeliveryReport.dn_amount).desc()).first()
            
            return {
                "top_product": _text(top_model.model) if top_model else "Unknown",
                "top_model": _text(top_model.model) if top_model else "Unknown",
                "top_material": _text(top_material.material) if top_material else "Unknown",
                "top_division": _text(top_division.division) if top_division else "Unknown",
            }
        except Exception:
            return {}
    
    def _get_pending_analytics(self, city_name: str) -> Dict[str, Any]:
        """Get pending analytics"""
        try:
            condition = func.lower(DeliveryReport.ship_to_city) == city_name.lower()
            
            pending_rows = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            ).filter(
                condition,
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
        dealers = dashboard.get('total_dealers', 0)
        
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
        
        if dealers < 10:
            recommendations.append("Consider expanding dealer network")
        
        if not recommendations:
            recommendations.append("Maintain current performance levels")
            recommendations.append("Continue monitoring key metrics")
        
        return recommendations
    
    def _generate_executive_summary(self, dashboard: Dict[str, Any]) -> str:
        """Generate executive summary"""
        city = dashboard.get('city_name', 'City')
        revenue = dashboard.get('total_revenue', 0)
        growth = dashboard.get('monthly_growth', 0)
        pending = dashboard.get('pending_dn', 0)
        score = dashboard.get('business_score', 0)
        status = dashboard.get('overall_status', 'Unknown')
        dealers = dashboard.get('total_dealers', 0)
        
        if growth >= 0:
            trend = "growing"
        else:
            trend = "declining"
        
        if score >= 70:
            action = "maintain current controls"
        else:
            action = "prioritize pending DN and POD closure"
        
        return (
            f"{city} is {trend} with a {score:.1f}/100 business score. "
            f"Revenue is PKR {revenue:,.2f} with {pending} pending DNs. "
            f"Delivery success is {dashboard.get('delivery_success_pct', 0):.1f}%. "
            f"The city has {dealers} dealers. "
            f"Recommendation: {action}."
        )

# ============================================================
# BLOCK 11: MAIN CITY ANALYTICS SERVICE WITH MENU
# ============================================================

class CityAnalyticsService:
    """
    City Domain AI Expert with Full Menu System
    Single entry point for all city-related business questions
    """
    
    def __init__(self) -> None:
        self._service_name = "city_analytics"
        self._version = "5.3.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = CityMenuRenderer()
        
        # Context memory
        self._contexts: Dict[str, CityContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ CityAnalyticsService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅")
        logger.info(f"   Source of Truth: PostgreSQL")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main city menu"""
        return self._menu_renderer.render_main_menu()
    
    def get_city_menu(self) -> str:
        """Get the city menu (alias for get_main_menu)"""
        return self.get_main_menu()
    
    def process_city_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process city menu input and return response.
        This is the method that ai_provider_service.py calls for menu navigation.
        """
        # Process the menu input using the existing process_menu_input method
        return self.process_menu_input(session_id, user_input)
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response
        
        Returns:
            {
                "response": str,           # WhatsApp message
                "menu_type": str,          # "city_menu"
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
        elif context.menu_state == MenuState.CITY_SELECTION:
            return self._handle_city_selection(context, user_input)
        elif context.menu_state == MenuState.COMPARISON_SELECTION:
            return self._handle_comparison_selection(context, user_input)
        
        # Default: treat as quick query
        return self._handle_quick_query(context, user_input)
    
    def _handle_main_menu_return(self, context: CityContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_cities = []
        context.awaiting_city = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "city_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True  # Exit to main AI Logistics menu
        }
    
    def _handle_main_menu_option(self, context: CityContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("dashboard", "Enter city name for dashboard:"),
            "2": ("revenue", "Enter city name for revenue:"),
            "3": ("units", "Enter city name for units:"),
            "4": ("pending", "Enter city name for pending:"),
            "5": ("delivery", "Enter city name for delivery:"),
            "6": ("comparison", None),  # Special handling
            "7": ("ranking", None),  # Special handling
            "8": ("top_products", "Enter city name for top products:"),
            "9": ("business_score", "Enter city name for business score:"),
            "10": ("distance", "Enter city name for distance:"),
            "11": ("growth", "Enter city name for growth:"),
            "12": ("summary", "Enter city name for summary:"),
        }
        
        if option == "6":
            # Start comparison flow
            context.menu_state = MenuState.COMPARISON_SELECTION
            context.comparison_cities = []
            return {
                "response": self._menu_renderer.render_comparison_selection(),
                "menu_type": "city_menu",
                "action": "comparison_start",
                "data": {},
                "exit_menu": False
            }
        
        if option == "7":
            # Show rankings directly
            return self._handle_ranking_request(context)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        # Check if we already have a selected city
        if context.current_city:
            # Use existing city
            result = self._execute_city_action(context, action, context.current_city)
            result["exit_menu"] = False
            return result
        
        # Ask for city
        context.menu_state = MenuState.CITY_SELECTION
        context.selected_option = action
        context.awaiting_city = True
        
        return {
            "response": self._menu_renderer.render_city_selection(prompt),
            "menu_type": "city_menu",
            "action": "city_selection",
            "data": {"purpose": action},
            "exit_menu": False
        }
    
    def _handle_city_selection(self, context: CityContext, city_input: str) -> Dict[str, Any]:
        """Handle city selection response"""
        city_name = self._resolve_city_name(city_input)
        if not city_name:
            return {
                "response": "\n".join([
                    "❌ City not found.",
                    "",
                    "Please try again or enter a valid city name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "city_menu",
                "action": "city_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_city = city_name
        context.menu_state = MenuState.MAIN
        context.awaiting_city = False
        
        action = context.selected_option or "dashboard"
        result = self._execute_city_action(context, action, city_name)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: CityContext, city_input: str) -> Dict[str, Any]:
        """Handle comparison city selection"""
        city_name = self._resolve_city_name(city_input)
        if not city_name:
            return {
                "response": "\n".join([
                    "❌ City not found.",
                    "",
                    "Please try again or enter a valid city name.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "city_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_cities.append(city_name)
        
        if len(context.comparison_cities) == 1:
            return {
                "response": "\n".join([
                    f"✅ First city selected: {city_name.title()}",
                    "",
                    "Enter second city name:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "city_menu",
                "action": "comparison_second",
                "data": {"first_city": city_name},
                "exit_menu": False
            }
        else:
            # Both cities selected, perform comparison
            city1, city2 = context.comparison_cities[0], context.comparison_cities[1]
            context.menu_state = MenuState.MAIN
            context.comparison_cities = []
            return self._perform_comparison(context, city1, city2)
    
    def _handle_ranking_request(self, context: CityContext) -> Dict[str, Any]:
        """Handle ranking request"""
        result = self._get_city_ranking(context)
        result["exit_menu"] = False
        return result
    
    def _handle_quick_query(self, context: CityContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            import re
            cities = re.findall(r'[a-zA-Z\s]+', query)
            city_names = [c.strip() for c in cities if c.strip()]
            
            if len(city_names) >= 2:
                city1 = self._resolve_city_name(city_names[0])
                city2 = self._resolve_city_name(city_names[1])
                if city1 and city2:
                    return self._perform_comparison(context, city1, city2)
        
        # Check if it's a ranking query
        if "top" in query.lower() and ("city" in query.lower() or "cities" in query.lower()):
            return self._get_city_ranking(context)
        
        # Try as single city query
        city_name = self._resolve_city_name(query)
        if city_name:
            context.current_city = city_name
            result = self._get_city_dashboard(context, city_name)
            result["exit_menu"] = False
            return result
        
        # Check if it's asking for menu
        if query.lower() in ["menu", "help", "options"]:
            return {
                "response": self._menu_renderer.render_main_menu(),
                "menu_type": "city_menu",
                "action": "menu",
                "data": {},
                "exit_menu": False
            }
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• 'Lahore' - Show dashboard",
                "• 'Revenue in Karachi'",
                "• 'Pending in Multan'",
                "• 'Compare Lahore Karachi'",
                "• 'Top cities by revenue'",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "city_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _execute_city_action(self, context: CityContext, action: str, city_name: str) -> Dict[str, Any]:
        """Execute city action based on selected option"""
        action_map = {
            "dashboard": self._get_city_dashboard,
            "revenue": self._get_city_metric,
            "units": self._get_city_metric,
            "pending": self._get_city_pending,
            "delivery": self._get_city_delivery,
            "top_products": self._get_city_top_products,
            "business_score": self._get_city_business_score,
            "distance": self._get_city_distance,
            "growth": self._get_city_growth,
            "summary": self._get_city_summary,
        }
        
        handler = action_map.get(action, self._get_city_dashboard)
        
        if action in ["revenue", "units"]:
            return handler(context, city_name, action)
        else:
            return handler(context, city_name)
    
    def _resolve_city_name(self, input_text: str) -> Optional[str]:
        """Resolve city name from input"""
        input_lower = input_text.lower().strip()
        
        # Direct match
        if input_lower in CITY_NAMES:
            return input_lower
        
        # Check aliases
        if input_lower in CITY_ALIASES:
            return CITY_ALIASES[input_lower]
        
        # Fuzzy match
        for city in CITY_NAMES:
            if len(city) >= 3:
                if city[:3] in input_lower or input_lower in city:
                    return city
        
        return None
    
    def _get_city_dashboard(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city dashboard"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\nPlease check the city name and try again.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "dashboard",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_city_dashboard(city_name, dashboard),
                    "menu_type": "city_menu",
                    "action": "dashboard",
                    "data": {"city": city_name, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Service error for {city_name}: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_metric(self, context: CityContext, city_name: str, metric: str) -> Dict[str, Any]:
        """Get specific city metric"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "metric_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                metric_mapping = {
                    "revenue": ("Revenue", f"PKR {dashboard.get('total_revenue', 0):,.2f}"),
                    "units": ("Units", f"{dashboard.get('total_units', 0):,}"),
                }
                
                label, value = metric_mapping.get(metric, ("Metric", "N/A"))
                
                return {
                    "response": "\n".join([
                        f"📊 *{city_name.title()} - {label}*",
                        "",
                        f"{value}",
                        "",
                        "0. Main Menu",
                        "99. Back"
                    ]),
                    "menu_type": "city_menu",
                    "action": f"metric_{metric}",
                    "data": {"city": city_name, "metric": metric, "value": value},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_pending(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city pending summary"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "pending_error",
                        "data": {"city": city_name, "error": "not_found"},
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
                    "response": self._menu_renderer.render_pending_summary(city_name, pending_data),
                    "menu_type": "city_menu",
                    "action": "pending",
                    "data": {"city": city_name, "pending": pending_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_delivery(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city delivery summary"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "delivery_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                delivery_data = {
                    "delivery_success_pct": dashboard.get('delivery_success_pct', 0),
                    "avg_delivery": dashboard.get('avg_delivery', 0),
                    "fastest_delivery": dashboard.get('fastest_delivery_days', 0) or 0,
                    "slowest_delivery": dashboard.get('slowest_delivery_days', 0) or 0,
                    "same_day_deliveries": dashboard.get('same_day_deliveries', 0) or 0,
                    "next_day_deliveries": dashboard.get('next_day_deliveries', 0) or 0,
                    "pod_success_pct": dashboard.get('pod_success_pct', 0),
                    "avg_pod": dashboard.get('avg_pod', 0),
                    "avg_cycle": dashboard.get('avg_cycle', 0),
                }
                
                return {
                    "response": self._menu_renderer.render_delivery_summary(city_name, delivery_data),
                    "menu_type": "city_menu",
                    "action": "delivery",
                    "data": {"city": city_name, "delivery": delivery_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_top_products(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city top products"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "top_products_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_top_products(city_name, dashboard),
                    "menu_type": "city_menu",
                    "action": "top_products",
                    "data": {"city": city_name, "products": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_business_score(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city business score"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "business_score_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_business_score(city_name, dashboard),
                    "menu_type": "city_menu",
                    "action": "business_score",
                    "data": {"city": city_name, "score": dashboard.get('business_score', 0)},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_distance(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city distance info"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "distance_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                distance_data = dashboard.get('distance', {})
                distance_data['warehouse'] = dashboard.get('warehouse', 'Unknown')
                
                return {
                    "response": self._menu_renderer.render_distance_info(city_name, distance_data),
                    "menu_type": "city_menu",
                    "action": "distance",
                    "data": {"city": city_name, "distance": distance_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_growth(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city growth analytics"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "growth_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                growth_data = {
                    "monthly_growth": dashboard.get('monthly_growth', 0),
                    "revenue_growth_pct": dashboard.get('revenue_growth_pct', 0),
                    "current_month_revenue": dashboard.get('current_month_revenue', 0),
                    "previous_month_revenue": dashboard.get('previous_month_revenue', 0),
                    "best_month": dashboard.get('best_month', 'N/A'),
                    "worst_month": dashboard.get('worst_month', 'N/A'),
                }
                
                return {
                    "response": self._menu_renderer.render_growth_summary(city_name, growth_data),
                    "menu_type": "city_menu",
                    "action": "growth",
                    "data": {"city": city_name, "growth": growth_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_summary(self, context: CityContext, city_name: str) -> Dict[str, Any]:
        """Get city executive summary"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dashboard = builder.build(city_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ City '{city_name}' not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "summary_error",
                        "data": {"city": city_name, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_summary(city_name, dashboard),
                    "menu_type": "city_menu",
                    "action": "summary",
                    "data": {"city": city_name, "summary": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_city_ranking(self, context: CityContext) -> Dict[str, Any]:
        """Get city rankings"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.ship_to_city.label("city"),
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue")
                ).filter(
                    DeliveryReport.ship_to_city.isnot(None)
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).desc()
                ).limit(10).all()
                
                ranking = []
                for row in results:
                    city = _text(row.city)
                    if city:
                        ranking.append({
                            "city": city,
                            "value": f"PKR {float(row.revenue or 0):,.2f}"
                        })
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "city_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Ranking error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_comparison(self, context: CityContext, city1: str, city2: str) -> Dict[str, Any]:
        """Perform city comparison"""
        try:
            with self._session() as session:
                builder = CityDashboardBuilder(session)
                dash1 = builder.build(city1)
                dash2 = builder.build(city2)
                
                if not dash1 or not dash2:
                    return {
                        "response": "⚠️ One or both cities not found.\n\n0. Main Menu",
                        "menu_type": "city_menu",
                        "action": "comparison_error",
                        "data": {"error": "not_found"},
                        "exit_menu": False
                    }
                
                metrics = {}
                
                # Build metrics for city1
                metrics[f"{city1}_metrics"] = {
                    "Revenue": f"PKR {dash1.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash1.get('total_units', 0):,}",
                    "DN": f"{dash1.get('total_dn', 0):,}",
                    "Pending": f"{dash1.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash1.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash1.get('business_score', 0):.1f}/100",
                }
                
                # Build metrics for city2
                metrics[f"{city2}_metrics"] = {
                    "Revenue": f"PKR {dash2.get('total_revenue', 0):,.2f}",
                    "Units": f"{dash2.get('total_units', 0):,}",
                    "DN": f"{dash2.get('total_dn', 0):,}",
                    "Pending": f"{dash2.get('pending_dn', 0):,}",
                    "Delivery Days": f"{dash2.get('avg_delivery', 0):.1f}",
                    "Business Score": f"{dash2.get('business_score', 0):.1f}/100",
                }
                
                # Generate comparison summary
                revenue1 = dash1.get('total_revenue', 0)
                revenue2 = dash2.get('total_revenue', 0)
                
                if revenue1 > revenue2:
                    explanation = f"{city1.title()} has higher revenue than {city2.title()}"
                elif revenue2 > revenue1:
                    explanation = f"{city2.title()} has higher revenue than {city1.title()}"
                else:
                    explanation = f"{city1.title()} and {city2.title()} have similar revenue"
                
                metrics["explanation"] = explanation
                
                return {
                    "response": self._menu_renderer.render_comparison_result(city1, city2, metrics),
                    "menu_type": "city_menu",
                    "action": "comparison",
                    "data": {"city1": city1, "city2": city2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Comparison error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "city_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_context(self, session_id: str) -> CityContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = CityContext()
            return self._contexts[session_id]
    
    # Legacy methods for backward compatibility
    def get_city_dashboard(self, city_name: str = "", **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not city_name:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide a city name.",
                "error": "CITY_REQUIRED"
            }
        
        context = self._get_context(kwargs.get("session_id", "default"))
        result = self._get_city_dashboard(context, city_name)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_top_cities(self, limit: int = 10, **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = self._get_context(kwargs.get("session_id", "default"))
        result = self._get_city_ranking(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("ranking", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def compare_cities(self, cities: List[str], **kwargs: Any) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        if not cities or len(cities) < 2:
            return {
                "success": False,
                "whatsapp_message": "⚠️ Please provide at least two cities.",
                "error": "TWO_CITIES_REQUIRED"
            }
        
        context = self._get_context(kwargs.get("session_id", "default"))
        result = self._perform_comparison(context, cities[0], cities[1])
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
                cities = session.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar() or 0
            
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
                "cities": int(cities),
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
        
        This is the main entry point for WhatsApp integration.
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

_service: Optional[CityAnalyticsService] = None
_service_lock = threading.Lock()


def get_city_analytics_service() -> CityAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = CityAnalyticsService()
    return _service


def answer_city_question(question: str, session_id: str = "default", **kwargs) -> Dict[str, Any]:
    """Quick access to answer city questions"""
    service = get_city_analytics_service()
    return service.process_menu_input(session_id, question)


def process_city_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process city menu input for WhatsApp integration"""
    service = get_city_analytics_service()
    return service.process_menu_input(session_id, user_input)


def get_city_main_menu() -> str:
    """Get the main city menu for WhatsApp"""
    service = get_city_analytics_service()
    return service.get_main_menu()


# ============================================================
# BLOCK 13: EXPORTS
# ============================================================

__all__ = [
    "CityAnalyticsService",
    "CityContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_city_analytics_service",
    "answer_city_question",
    "process_city_menu",
    "get_city_main_menu",
    "CityMenuRenderer",
]
