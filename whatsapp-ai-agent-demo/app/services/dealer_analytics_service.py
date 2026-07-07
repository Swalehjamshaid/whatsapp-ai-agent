#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 12.1 - ENTERPRISE DEALER INTELLIGENCE PLATFORM
# ============================================================

"""
================================================================================
DEALER LOGISTICS INTELLIGENCE PLATFORM - ENTERPRISE EDITION v12.1
================================================================================

This service is a complete Dealer Logistics Intelligence Platform.

SOURCE OF TRUTH: PostgreSQL ONLY

FEATURES:
- ✅ Complete Menu System (20+ options)
- ✅ Dealer Selection Prompts
- ✅ Comparison Flow (2 dealers)
- ✅ Ranking Display with Medals
- ✅ Quick Commands Support
- ✅ Context Memory
- ✅ Dynamic Menu Rendering
- ✅ WhatsApp-Optimized Formatting
- ✅ AI-Powered Natural Language Queries
- ✅ PostgreSQL Integration (Single Source of Truth)
- ✅ Full Analytics Suite
- ✅ Distance Calculation (Haversine)
- ✅ AI Summary (Groq - Optional)

================================================================================
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

CACHE_TTL = max(60, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")

# ============================================================
# BLOCK 3: CONSTANTS
# ============================================================

DEALER_NAMES: List[str] = []  # Will be populated from database

# Common Pakistani city abbreviations used as dealer-name suffixes
CITY_ABBREVIATIONS = {
    'khi': 'karachi',
    'lhr': 'lahore',
    'isb': 'islamabad',
    'rwp': 'rawalpindi',
    'fsd': 'faisalabad',
    'mul': 'multan',
    'pes': 'peshawar',
    'que': 'quetta',
    'hyd': 'hyderabad',
    'guj': 'gujranwala',
    'skt': 'sialkot',
}

CITY_NAMES = set(CITY_ABBREVIATIONS.values())

# Dealer suffixes to remove in search
DEALER_SUFFIXES = [
    'Electronics', 'Digital', 'Technologies', 'Traders',
    'Enterprises', 'Systems', 'Solutions', 'Incorporated',
    'International', 'Corporation', 'Limited', 'Ltd',
    'Pvt', 'Private', 'Co', 'Company'
]

SEPARATOR: str = "────────────────────"
EXIT_SIGNAL = "__EXIT__"
VERSION = "12.1"

# Fallback coordinates (Center of Pakistan)
FALLBACK_COORDINATES = (30.3753, 69.3451)

# Warehouse coordinates
WAREHOUSE_COORDINATES: Dict[str, Tuple[float, float]] = {
    "karachi": (24.8607, 67.0011),
    "lahore": (31.5204, 74.3587),
    "rawalpindi": (33.5651, 73.0169),
    "islamabad": (33.6844, 73.0479),
    "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750),
    "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350),
    "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883),
    "bahawalpur": (29.3956, 71.6836),
    "sukkur": (27.7060, 68.8530),
    "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
}

# City coordinates
CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "karachi": (24.8607, 67.0011),
    "lahore": (31.5204, 74.3587),
    "rawalpindi": (33.5651, 73.0169),
    "islamabad": (33.6844, 73.0479),
    "multan": (30.1575, 71.5249),
    "peshawar": (34.0151, 71.5249),
    "quetta": (30.1798, 66.9750),
    "hyderabad": (25.3960, 68.3578),
    "faisalabad": (31.4504, 73.1350),
    "sialkot": (32.4945, 74.5229),
    "gujranwala": (32.1617, 74.1883),
    "bahawalpur": (29.3956, 71.6836),
    "sukkur": (27.7060, 68.8530),
    "dg khan": (30.0430, 70.6402),
    "abbottabad": (34.1490, 73.2210),
    "gwadar": (25.1260, 62.3250),
    "gilgit": (35.9208, 74.3144),
}

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """Dealer question intent types"""
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
    RECOMMENDATIONS = "recommendations"
    SEARCH = "search"
    MENU = "menu"
    LOGISTICS = "logistics"
    DISTANCE = "distance"
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
    LOGISTICS = "logistics"

# ============================================================
# BLOCK 5: DATACLASSES
# ============================================================

@dataclass
class DealerContext:
    """Session context for dealer queries"""
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    current_customer_code: Optional[str] = None
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
        self.current_customer_code = None
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
    customer_code: Optional[str] = None
    metrics: List[str] = field(default_factory=list)
    timeframe: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    format: str = "standard"
    confidence: float = 1.0
    requires_ai: bool = False

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

def _format_currency(amount: float) -> str:
    """Format currency in PKR"""
    if amount >= 100_000_000:
        return f"PKR {amount/100_000_000:.2f}Cr"
    elif amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount/1_000:.2f}K"
    else:
        return f"PKR {amount:,.0f}"

def _normalize_text(text: str) -> str:
    """Normalize text for search - PRESERVES hyphens"""
    if not text:
        return ""
    normalized = text.lower()
    normalized = re.sub(r'[&\./,()\'\"]', ' ', normalized)
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    return normalized

def _clean_dealer_name(name: str) -> str:
    """Clean dealer name by removing common suffixes"""
    if not name:
        return ""
    cleaned = name.lower().strip()
    for suffix in DEALER_SUFFIXES:
        cleaned = re.sub(r'\s*' + suffix.lower() + r'\s*$', '', cleaned)
    cleaned = re.sub(r'-[a-z]{3}$', '', cleaned)
    return cleaned.strip()

def _calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate distance using Haversine formula"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def _get_coordinates(city: str) -> Tuple[float, float]:
    """Get coordinates with fallback"""
    city_lower = city.lower()
    coords = WAREHOUSE_COORDINATES.get(city_lower)
    if not coords:
        logger.warning(f"No coordinates for city: {city}, using fallback")
        return FALLBACK_COORDINATES
    return coords

def _get_distance_info(warehouse: str, city: str) -> Dict[str, Any]:
    """Calculate distance and estimated delivery time"""
    warehouse_lower = warehouse.lower()
    city_lower = city.lower()
    
    warehouse_coord = WAREHOUSE_COORDINATES.get(warehouse_lower)
    city_coord = CITY_COORDINATES.get(city_lower)
    
    if warehouse_coord and city_coord:
        distance = _calculate_distance(
            warehouse_coord[0], warehouse_coord[1],
            city_coord[0], city_coord[1]
        )
        
        if distance <= 80:
            zone = "Local"
            estimated = "Same Day"
        elif distance <= 200:
            zone = "Short Haul"
            estimated = "1 Day"
        elif distance <= 400:
            zone = "Medium Haul"
            estimated = "2 Days"
        elif distance <= 700:
            zone = "Long Haul"
            estimated = "3 Days"
        else:
            zone = "Extended Haul"
            estimated = "4-5 Days"
        
        return {
            "distance_km": round(distance, 1),
            "estimated_delivery": estimated,
            "transportation_zone": zone,
            "source": "Haversine"
        }
    
    return {
        "distance_km": None,
        "estimated_delivery": "Unknown",
        "transportation_zone": "Unknown",
        "source": "Unavailable"
    }

# ============================================================
# BLOCK 7: MENU SYSTEM
# ============================================================

class DealerMenuRenderer:
    """Render dealer analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main dealer menu"""
        return "\n".join([
            "🏢 *DEALER ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Dealer Dashboard",
            "2. Dealer Revenue",
            "3. Dealer Units",
            "4. Dealer Logistics",
            "5. Dealer Warehouses",
            "6. Dealer Cities",
            "7. Pending DN",
            "8. Pending PGI",
            "9. Pending POD",
            "10. Dealer Comparison",
            "11. Dealer Ranking",
            "12. Monthly Trend",
            "13. Executive Summary",
            "14. AI Insights",
            "15. Recommendations",
            "16. Business Performance",
            "17. Dealer Score",
            "18. Smart Search",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• Compare [Dealer1] and [Dealer2]",
            "• Top dealers by revenue",
            "• Revenue of [Dealer]",
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
            "Arshad Electronics-Khi",
            "Zoom Appliances",
            "RUBA Digital",
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
        """Render dealer dashboard in WhatsApp format"""
        identity = data.get('identity', {})
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        distance = data.get('distance', {})
        product = data.get('product', {})
        warehouse = data.get('warehouse', {})
        
        # Calculate aging metrics
        today = datetime.utcnow().date()
        last_dn_date_str = data.get('dates', {}).get('last_delivery_date', '')
        oldest_pending_days = 0
        
        if last_dn_date_str:
            try:
                last_dn_date = datetime.strptime(last_dn_date_str, "%d-%b-%Y").date()
                oldest_pending_days = (today - last_dn_date).days
            except ValueError:
                pass
        
        # Get primary warehouse info
        primary_warehouse = identity.get('warehouse', 'N/A')
        warehouse_distance = distance.get('distance_km', 'N/A')
        estimated_transit = distance.get('estimated_delivery', 'N/A')
        
        # Get warehouse distribution
        warehouse_distribution = warehouse.get('warehouse_distribution', [])
        
        lines = [
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"        🏢 DEALER INTELLIGENCE CENTER",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Dealer Name        : {identity.get('customer_name', dealer_name)}",
            f"Dealer Code        : {identity.get('dealer_code', 'N/A')}",
            f"City               : {identity.get('city', 'N/A')}",
            f"Sales Office       : {identity.get('sales_office', 'N/A')}",
            f"Sales Manager      : {identity.get('sales_manager', 'N/A')}",
            "",
            f"Primary Warehouse  : {primary_warehouse}",
            f"Distance           : {warehouse_distance} km",
            f"Estimated Transit  : {estimated_transit}",
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📦 DELIVERY SUMMARY",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Total DNs          : {delivery.get('total_dn', 0):,}",
            f"Total Units        : {sales.get('total_quantity', 0):,}",
            f"Total Revenue      : {_format_currency(sales.get('total_revenue', 0))}",
            "",
            f"Delivered DNs      : {delivery.get('delivered_dn', 0):,}",
            f"Pending DNs        : {delivery.get('pending_dn', 0):,}",
            f"Pending PGI        : {delivery.get('pgi_pending', 0):,}",
            f"Pending POD        : {delivery.get('pod_pending', 0):,}",
            "",
            f"Delivery Rate      : {delivery.get('delivery_rate', 0):.1f}%",
            f"POD Completion     : {delivery.get('pod_rate', 0):.1f}%",
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📅 AGING ANALYSIS",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Average Delivery Days : {delivery.get('avg_delivery_days', 0):.1f}",
            f"Average POD Days      : {delivery.get('avg_pod_days', 0):.1f}",
            "",
            f"Oldest Pending DN     : {oldest_pending_days} Days",
            f"Newest Pending DN     : 1 Day",  # Placeholder
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🏭 WAREHOUSE DISTRIBUTION",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        
        # Add warehouse distribution
        if warehouse_distribution:
            for wh in warehouse_distribution[:3]:
                lines.append(f"{wh.get('warehouse', 'Unknown')}: {wh.get('dn_count', 0)} DNs")
        else:
            lines.append(f"{primary_warehouse}: {delivery.get('total_dn', 0)} DNs")
        
        lines.extend([
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📦 PRODUCT SUMMARY",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            f"Top Model             : {product.get('top_model', 'N/A')}",
            f"Top Category          : {product.get('top_category', 'N/A')}",
            f"Total Models          : {product.get('total_models', 0)}",
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"⚠ ISSUES REQUIRING ACTION",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        # Add action items
        issues = data.get('issues', [])
        if issues:
            for issue in issues[:4]:
                lines.append(f"• {issue}")
        else:
            lines.append("• No critical issues found")
            
        lines.extend([
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📈 BUSINESS INSIGHTS",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ])
        
        # Add insights
        insights = data.get('insights', [])
        if insights:
            for insight in insights[:5]:
                lines.append(f"• {insight}")
        else:
            lines.append("• Performance is stable")
            
        # Add warehouse-specific insights
        if warehouse_distance != 'N/A':
            lines.append(f"• Distance to dealer: {warehouse_distance} km")
            lines.append(f"• Estimated transit: {estimated_transit}")
            
        lines.extend([
            "",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main"
        ])
        
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
    
    @staticmethod
    def render_executive_summary(dealer_name: str, data: Dict[str, Any]) -> str:
        """Render executive summary"""
        identity = data.get('identity', {})
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        performance = data.get('performance', {})
        
        revenue = sales.get('total_revenue', 0)
        units = sales.get('total_quantity', 0)
        dn = delivery.get('total_dn', 0)
        pending = delivery.get('pending_dn', 0)
        score = performance.get('business_score', 0)
        tier = performance.get('performance_tier', 'Standard')
        recommendations = data.get('recommendations', [])[:3]
        
        lines = [
            f"📋 *Executive Summary - {dealer_name}*",
            "",
            f"💰 Revenue: {_format_currency(revenue)}",
            f"📦 Units: {units:,}",
            f"📄 DN: {dn:,}",
            f"⏳ Pending: {pending:,}",
            f"⭐ Score: {score}/100",
            f"🏆 Tier: {tier}",
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
    """AI-powered intent detection for dealer questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|get).*(?:dealer|dashboard)",
            r"dealer (?:dashboard|profile|details)",
            r"show me (?:dealer|dashboard)",
            r"(?:dealer|distributor) (?:info|information)",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|sales|income).*(?:dealer)",
            r"dealer (?:revenue|sales)",
            r"how much (?:revenue|sales).*(?:dealer)",
            r"revenue of (?:dealer)",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity|volume).*(?:dealer)",
            r"dealer (?:units|quantity)",
            r"how many units",
            r"units sold",
        ],
        IntentType.LOGISTICS: [
            r"(?:logistics|distance|transport|delivery time).*(?:dealer)",
            r"dealer (?:logistics|distance)",
            r"how far",
            r"delivery (?:time|days)",
        ],
        IntentType.WAREHOUSES: [
            r"(?:warehouse|warehouses).*(?:dealer)",
            r"which warehouse",
            r"warehouse (?:distribution|analysis)",
        ],
        IntentType.CITIES: [
            r"(?:city|cities).*(?:dealer)",
            r"which cities",
            r"city (?:distribution|analysis)",
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
        IntentType.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        IntentType.RANKING: [
            r"(?:top|best|highest).*(?:dealer|dealers)",
            r"dealer (?:ranking|rank|leaderboard)",
            r"top dealers",
            r"best dealer",
            r"worst dealer",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|change).*(?:dealer)",
            r"dealer (?:trend|growth|change)",
            r"monthly trend",
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
        IntentType.PERFORMANCE: [
            r"(?:performance|score|rating).*(?:dealer)",
            r"dealer (?:performance|score|health)",
            r"how is (?:dealer|performance)",
        ],
        IntentType.RECOMMENDATIONS: [
            r"(?:recommend|suggest|advice).*(?:dealer)",
            r"dealer (?:recommendations|suggestions)",
            r"what (?:should|can) be done",
        ],
        IntentType.SEARCH: [
            r"(?:search|find|lookup).*(?:dealer)",
            r"search (?:dealer)",
            r"find dealer",
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
                    Route(name="dealer_logistics", utterances=[
                        "dealer logistics", "dealer distance", "delivery time"
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
                    Route(name="dealer_performance", utterances=[
                        "dealer performance", "dealer score", "dealer health"
                    ]),
                ]
                self._semantic_router = Router(routes=routes, encoder=HuggingFaceEncoder())
                logger.info("✅ Semantic router initialized for dealers")
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
                elif keyword in ["logistics", "distance", "transport"]:
                    best_intent = IntentType.LOGISTICS
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
    """Entity extraction for dealer questions"""
    
    def __init__(self):
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=3600)
        self._lock = threading.RLock()
        self._dealer_cache: Dict[str, str] = {}
    
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
            "customer_codes": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison_dealers": [],
            "requires_comparison": False,
        }
        
        # Extract dealer names from database
        dealers = self._extract_dealers(question_lower)
        if dealers:
            entities["dealers"] = dealers
        
        # Extract dealer codes
        dealer_codes = self._extract_dealer_codes(question_lower)
        if dealer_codes:
            entities["dealer_codes"] = dealer_codes
        
        # Extract customer codes
        customer_codes = self._extract_customer_codes(question_lower)
        if customer_codes:
            entities["customer_codes"] = customer_codes
        
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
        for metric in ["revenue", "units", "dn", "delivery", "pending", "score"]:
            if metric in question_lower:
                entities["sort_by"] = metric
                break
        
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_dealers(self, text: str) -> List[str]:
        """Extract dealer names from text"""
        found = []
        
        # Try to get from database cache
        if not self._dealer_cache:
            try:
                with SessionLocal() as session:
                    dealers = session.query(
                        DeliveryReport.customer_name
                    ).distinct().limit(100).all()
                    for d in dealers:
                        if d.customer_name:
                            self._dealer_cache[d.customer_name.lower()] = d.customer_name
            except Exception:
                pass
        
        # Check direct matches
        for dealer_name, original in self._dealer_cache.items():
            if dealer_name in text:
                found.append(original)
        
        # Check aliases
        for alias, city in CITY_ABBREVIATIONS.items():
            if alias in text:
                # Search for dealers in this city
                try:
                    with SessionLocal() as session:
                        dealers = session.query(
                            DeliveryReport.customer_name
                        ).filter(
                            DeliveryReport.ship_to_city.ilike(f"%{city}%")
                        ).distinct().limit(5).all()
                        for d in dealers:
                            if d.customer_name and d.customer_name not in found:
                                found.append(d.customer_name)
                except Exception:
                    pass
        
        # Check for quoted dealer names
        match = re.search(r'"([^"]+)"', text)
        if match:
            found.append(match.group(1))
        
        return found
    
    def _extract_dealer_codes(self, text: str) -> List[str]:
        """Extract dealer codes from text"""
        matches = re.findall(r'\b(DEAL_[A-Z0-9_]+)\b', text.upper())
        return matches
    
    def _extract_customer_codes(self, text: str) -> List[str]:
        """Extract customer codes from text"""
        matches = re.findall(r'\b(CUST_[A-Z0-9_]+)\b', text.upper())
        return matches
    
    def _extract_metrics(self, text: str) -> List[str]:
        """Extract metrics from text"""
        metric_keywords = {
            "revenue": ["revenue", "sales", "income"],
            "units": ["units", "quantity", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "logistics": ["logistics", "distance", "transport"],
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
# BLOCK 10: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    """Dealer data access layer - PostgreSQL only"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=2048, ttl=CACHE_TTL)
        self._lock = threading.RLock()
    
    def get_dealer_by_name(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Get dealer by name, code, or customer code"""
        dealer_identifier_lower = dealer_identifier.lower()
        cache_key = f"dealer_{dealer_identifier_lower}"
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            # Search by customer_name, dealer_code, or customer_code
            query = self.session.query(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.delivery_location,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealer_count'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('city_count'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouse_count'),
                func.min(DeliveryReport.dn_create_date).label('first_sale'),
                func.max(DeliveryReport.dn_create_date).label('last_sale'),
                func.avg(DeliveryReport.dn_amount).label('avg_dn_value'),
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
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_identifier_lower,
                    func.lower(DeliveryReport.dealer_code) == dealer_identifier_lower,
                    func.lower(DeliveryReport.customer_code) == dealer_identifier_lower,
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_identifier_lower}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_identifier_lower}%"),
                    func.lower(DeliveryReport.customer_code).ilike(f"%{dealer_identifier_lower}%"),
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.delivery_location,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division
            ).first()
            
            if not query:
                return None
            
            dealer_data = {
                'customer_name': _text(query.customer_name),
                'dealer_code': _text(query.dealer_code),
                'customer_code': _text(query.customer_code),
                'city': _text(query.ship_to_city),
                'warehouse': _text(query.warehouse),
                'warehouse_code': _text(query.warehouse_code),
                'delivery_location': _text(query.delivery_location),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'division': _text(query.division),
                'dn_count': int(query.dn_count or 0),
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'dealer_count': int(query.dealer_count or 0),
                'city_count': int(query.city_count or 0),
                'warehouse_count': int(query.warehouse_count or 0),
                'first_sale': _date_text(query.first_sale),
                'last_sale': _date_text(query.last_sale),
                'avg_dn_value': float(query.avg_dn_value or 0.0),
                'pending_dn': int(query.pending_dn or 0),
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pod_completed': int(query.pod_completed or 0),
                'pgi_completed': int(query.pgi_completed or 0),
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
            }
            
            # Calculate metrics
            dealer_data['delivery_success_pct'] = _percent(
                dealer_data.get('pod_completed', 0),
                dealer_data.get('dn_count', 0)
            )
            dealer_data['pgi_rate'] = _percent(
                dealer_data.get('pgi_completed', 0),
                dealer_data.get('dn_count', 0)
            )
            dealer_data['pod_rate'] = _percent(
                dealer_data.get('pod_completed', 0),
                dealer_data.get('dn_count', 0)
            )
            dealer_data['pending_pct'] = _percent(
                dealer_data.get('pending_dn', 0),
                dealer_data.get('dn_count', 0)
            )
            dealer_data['avg_units_per_dn'] = (
                dealer_data.get('total_units', 0) / dealer_data.get('dn_count', 1)
                if dealer_data.get('dn_count', 0) > 0 else 0
            )
            
            # Distance info
            dealer_data['distance'] = _get_distance_info(
                dealer_data.get('warehouse', ''),
                dealer_data.get('city', '')
            )
            
            # Business score
            score = (
                dealer_data.get('delivery_success_pct', 0) * 0.25 +
                (100 - dealer_data.get('pending_pct', 0)) * 0.25 +
                min(100, dealer_data.get('total_units', 0) / 100) * 0.20 +
                min(100, dealer_data.get('avg_dn_value', 0) / 1000) * 0.15 +
                min(100, dealer_data.get('dealer_count', 0) * 5) * 0.15
            )
            dealer_data['business_score'] = round(min(100, max(0, score)), 1)
            
            # Performance grade
            if dealer_data['business_score'] >= 85:
                dealer_data['performance_grade'] = "A"
                dealer_data['overall_status'] = "Excellent"
                dealer_data['performance_tier'] = "Platinum"
                dealer_data['dealer_rating'] = 5.0
            elif dealer_data['business_score'] >= 70:
                dealer_data['performance_grade'] = "B"
                dealer_data['overall_status'] = "Good"
                dealer_data['performance_tier'] = "Gold"
                dealer_data['dealer_rating'] = 4.0
            elif dealer_data['business_score'] >= 50:
                dealer_data['performance_grade'] = "C"
                dealer_data['overall_status'] = "Watch"
                dealer_data['performance_tier'] = "Silver"
                dealer_data['dealer_rating'] = 3.0
            else:
                dealer_data['performance_grade'] = "D"
                dealer_data['overall_status'] = "Critical"
                dealer_data['performance_tier'] = "Bronze"
                dealer_data['dealer_rating'] = 2.0
            
            # Risk score
            dealer_data['risk_score'] = 100 - dealer_data['business_score']
            
            # Generate insights and recommendations
            dealer_data['insights'] = self._generate_insights(dealer_data)
            dealer_data['recommendations'] = self._generate_recommendations(dealer_data)
            dealer_data['executive_summary'] = self._generate_executive_summary(dealer_data)
            
            with self._lock:
                self._cache[cache_key] = dealer_data.copy()
            
            return dealer_data
            
        except Exception as e:
            logger.error(f"Failed to get dealer data for {dealer_identifier}: {e}")
            return None
    
    def _generate_insights(self, data: Dict[str, Any]) -> List[str]:
        """Generate insights from data"""
        insights = []
        
        revenue = data.get('total_revenue', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('business_score', 0)
        dealers = data.get('dealer_count', 0)
        delivery = data.get('delivery_success_pct', 0)
        pod = data.get('pod_rate', 0)
        pgi = data.get('pgi_rate', 0)
        
        if delivery >= 95:
            insights.append("✅ Excellent delivery performance")
        elif delivery >= 85:
            insights.append("✅ Good delivery performance")
        elif delivery < 75:
            insights.append("⚠️ Delivery rate needs improvement")
        
        if pod >= 95:
            insights.append("✅ Excellent POD completion")
        elif pod < 80:
            insights.append("⚠️ POD completion needs attention")
        
        if pgi >= 95:
            insights.append("✅ Excellent PGI completion")
        elif pgi < 80:
            insights.append("⚠️ PGI completion needs attention")
        
        if pending == 0:
            insights.append("✅ No pending orders - excellent efficiency")
        elif pending < 10:
            insights.append(f"📋 Low pending orders: {pending}")
        else:
            insights.append(f"⚠️ High pending orders: {pending}")
        
        if revenue > 10_000_000:
            insights.append("📈 Revenue is above dealer average")
        elif revenue > 5_000_000:
            insights.append("📈 Revenue is at dealer average")
        
        if score >= 85:
            insights.append(f"⭐ Excellent business score: {score:.1f}/100")
        elif score >= 70:
            insights.append(f"⭐ Good business score: {score:.1f}/100")
        elif score < 50:
            insights.append(f"⚠️ Critical business score: {score:.1f}/100")
        
        if dealers >= 50:
            insights.append(f"🏪 Strong dealer network with {dealers} dealers")
        elif dealers >= 20:
            insights.append(f"🏪 Good dealer network with {dealers} dealers")
        
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
        
        if pending > 20:
            recommendations.append(f"📋 Escalate {pending} pending DNs")
        elif pending > 10:
            recommendations.append("📋 Review pending orders")
        
        if delivery < 80:
            recommendations.append("📋 Improve delivery performance")
        
        if data.get('pod_rate', 0) < 85:
            recommendations.append("📋 Focus on POD completion")
        
        if score < 70:
            recommendations.append("📋 Develop performance improvement plan")
        
        if dealers < 10:
            recommendations.append("📋 Expand dealer network")
        
        if not recommendations:
            recommendations.append("📋 Maintain current performance")
            recommendations.append("📋 Monitor key metrics")
            recommendations.append("📋 Explore growth opportunities")
        
        return recommendations
    
    def _generate_executive_summary(self, data: Dict[str, Any]) -> str:
        """Generate executive summary"""
        dealer = data.get('customer_name', 'Dealer')
        revenue = data.get('total_revenue', 0)
        pending = data.get('pending_dn', 0)
        score = data.get('business_score', 0)
        status = data.get('overall_status', 'Unknown')
        tier = data.get('performance_tier', 'Standard')
        
        if score >= 70:
            action = "maintain current controls"
        else:
            action = "prioritize pending DN and POD closure"
        
        return (
            f"{dealer} has {status.lower()} performance with a {score:.1f}/100 business score. "
            f"Revenue is {_format_currency(revenue)} with {pending} pending deliveries. "
            f"Performance tier: {tier}. Recommendation: {action}."
        )
    
    def get_top_dealers_by_revenue(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top dealers by revenue"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.sum(DeliveryReport.dn_amount).label('revenue')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.dealer:
                    ranking.append({
                        'dealer': _text(row.dealer),
                        'value': f"PKR {float(row.revenue or 0):,.2f}"
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get top dealers: {e}")
            return []
    
    def get_top_dealers_by_units(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top dealers by units sold"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.sum(DeliveryReport.dn_qty).label('units')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.dealer:
                    ranking.append({
                        'dealer': _text(row.dealer),
                        'value': f"{int(row.units or 0):,} units"
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get top dealers by units: {e}")
            return []
    
    def get_top_dealers_by_delivery(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top dealers by delivery performance"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                func.count(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no))).label('delivered'),
                func.count(DeliveryReport.dn_no).label('total')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no))).desc()
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.dealer:
                    delivered = int(row.delivered or 0)
                    total = int(row.total or 1)
                    pct = (delivered / total * 100) if total > 0 else 0
                    ranking.append({
                        'dealer': _text(row.dealer),
                        'value': f"{delivered}/{total} ({pct:.1f}%)"
                    })
            return ranking
        except Exception as e:
            logger.error(f"Failed to get top dealers by delivery: {e}")
            return []
    
    def search_dealers(self, query: str) -> List[Dict[str, Any]]:
        """Search for dealers"""
        try:
            search_pattern = f"%{query}%"
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
            ).filter(
                or_(
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.dealer_code.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.ship_to_city.ilike(search_pattern),
                    DeliveryReport.warehouse.ilike(search_pattern),
                )
            ).distinct().limit(20).all()
            
            dealers = []
            for row in results:
                if row.dealer:
                    dealers.append({
                        'dealer': _text(row.dealer),
                        'dealer_code': _text(row.dealer_code),
                        'customer_code': _text(row.customer_code),
                        'city': _text(row.ship_to_city),
                        'warehouse': _text(row.warehouse),
                    })
            return dealers
        except Exception as e:
            logger.error(f"Failed to search dealers: {e}")
            return []
    
    def get_pending_dealers(self) -> List[Dict[str, Any]]:
        """Get dealers with pending deliveries"""
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer_name'),
                func.count(distinct(DeliveryReport.dn_no)).label('pending_count')
            ).filter(
                or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(distinct(DeliveryReport.dn_no)).desc()
            ).limit(10).all()
            
            dealers = []
            for row in results:
                if row.dealer_name:
                    dealers.append({
                        'dealer_name': _text(row.dealer_name),
                        'pending_count': int(row.pending_count or 0)
                    })
            return dealers
        except Exception as e:
            logger.error(f"Failed to get pending dealers: {e}")
            return []

# ============================================================
# BLOCK 11: DEALER DASHBOARD BUILDER
# ============================================================

class DealerDashboardBuilder:
    """Build dealer dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self.repository = DealerRepository(session)
    
    def build(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for dealer"""
        cache_key = dealer_identifier.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        dealer_data = self.repository.get_dealer_by_name(dealer_identifier)
        
        if dealer_data:
            # Build complete dashboard structure
            dashboard = {
                'identity': {
                    'customer_name': dealer_data.get('customer_name', ''),
                    'dealer_code': dealer_data.get('dealer_code', ''),
                    'customer_code': dealer_data.get('customer_code', ''),
                    'city': dealer_data.get('city', ''),
                    'warehouse': dealer_data.get('warehouse', ''),
                    'warehouse_code': dealer_data.get('warehouse_code', ''),
                    'delivery_location': dealer_data.get('delivery_location', ''),
                    'sales_office': dealer_data.get('sales_office', ''),
                    'sales_manager': dealer_data.get('sales_manager', ''),
                    'division': dealer_data.get('division', ''),
                },
                'delivery': {
                    'total_dn': dealer_data.get('dn_count', 0),
                    'pending_dn': dealer_data.get('pending_dn', 0),
                    'pgi_pending': dealer_data.get('pgi_pending_dn', 0),
                    'pod_pending': dealer_data.get('pod_pending_dn', 0),
                    'delivered_dn': dealer_data.get('pod_completed', 0),
                    'pgi_completed': dealer_data.get('pgi_completed', 0),
                    'pod_completed': dealer_data.get('pod_completed', 0),
                    'delivery_rate': dealer_data.get('delivery_success_pct', 0),
                    'pgi_rate': dealer_data.get('pgi_rate', 0),
                    'pod_rate': dealer_data.get('pod_rate', 0),
                    'avg_delivery_days': dealer_data.get('avg_delivery_days', 0),
                    'avg_pod_days': dealer_data.get('avg_pod_days', 0),
                },
                'sales': {
                    'total_revenue': dealer_data.get('total_revenue', 0),
                    'total_quantity': dealer_data.get('total_units', 0),
                    'avg_dn_value': dealer_data.get('avg_dn_value', 0),
                    'avg_quantity_per_dn': dealer_data.get('avg_units_per_dn', 0),
                },
                'product': {
                    'total_models': self._get_product_count(dealer_identifier),
                    'top_models': self._get_top_models(dealer_identifier),
                },
                'warehouse': {
                    'primary_warehouse': dealer_data.get('warehouse', ''),
                    'warehouses_used': dealer_data.get('warehouse_count', 0),
                    'warehouse_distribution': self._get_warehouse_distribution(dealer_identifier),
                },
                'city': {
                    'cities_served': dealer_data.get('city_count', 0),
                    'top_destination_cities': self._get_top_cities(dealer_identifier),
                },
                'performance': {
                    'business_score': dealer_data.get('business_score', 0),
                    'risk_score': dealer_data.get('risk_score', 0),
                    'performance_tier': dealer_data.get('performance_tier', 'Standard'),
                    'dealer_rating': dealer_data.get('dealer_rating', 0),
                    'dealer_rank': 0,
                },
                'distance': dealer_data.get('distance', {}),
                'dates': {
                    'last_delivery_date': dealer_data.get('last_sale', 'N/A'),
                    'last_pgi_date': self._get_last_pgi_date(dealer_identifier),
                    'last_pod_date': dealer_data.get('last_pod_date', 'N/A'),
                },
                'insights': dealer_data.get('insights', []),
                'issues': self._get_issues(dealer_data),
                'recommendations': dealer_data.get('recommendations', []),
                'executive_summary': dealer_data.get('executive_summary', ''),
            }
            
            # Add top model and category
            if dashboard['product']['top_models']:
                dashboard['product']['top_model'] = dashboard['product']['top_models'][0].get('model', 'N/A')
                dashboard['product']['top_category'] = dashboard['product']['top_models'][0].get('category', 'N/A')
            else:
                dashboard['product']['top_model'] = 'N/A'
                dashboard['product']['top_category'] = 'N/A'
            
            # Add warehouse insights
            dashboard['insights'].extend(self._get_warehouse_insights(dashboard))
            
            with self._lock:
                self._cache[cache_key] = dashboard.copy()
            
            return dashboard
        
        return None
    
    def _get_product_count(self, dealer_identifier: str) -> int:
        """Get total product models for dealer"""
        try:
            with self.session as session:
                count = session.query(func.count(distinct(DeliveryReport.material_no))).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).scalar()
                return count or 0
        except Exception:
            return 0
    
    def _get_top_models(self, dealer_identifier: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get top models for dealer"""
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.material_no.label('model'),
                    func.count(DeliveryReport.dn_no).label('dn_count'),
                    func.sum(DeliveryReport.dn_qty).label('total_units')
                ).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).group_by(
                    DeliveryReport.material_no
                ).order_by(
                    func.sum(DeliveryReport.dn_qty).desc()
                ).limit(limit).all()
                
                return [{
                    'model': r.model,
                    'dn_count': r.dn_count,
                    'total_units': r.total_units
                } for r in results]
        except Exception:
            return []
    
    def _get_warehouse_distribution(self, dealer_identifier: str) -> List[Dict[str, Any]]:
        """Get warehouse distribution for dealer"""
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.warehouse,
                    func.count(DeliveryReport.dn_no).label('dn_count')
                ).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).group_by(
                    DeliveryReport.warehouse
                ).order_by(
                    func.count(DeliveryReport.dn_no).desc()
                ).all()
                
                return [{
                    'warehouse': r.warehouse,
                    'dn_count': r.dn_count
                } for r in results]
        except Exception:
            return []
    
    def _get_last_pgi_date(self, dealer_identifier: str) -> str:
        """Get last PGI date for dealer"""
        try:
            with self.session as session:
                date_val = session.query(
                    func.max(DeliveryReport.good_issue_date)
                ).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).scalar()
                return _date_text(date_val) if date_val else 'N/A'
        except Exception:
            return 'N/A'
    
    def _get_top_cities(self, dealer_identifier: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Get top destination cities for dealer"""
        try:
            with self.session as session:
                results = session.query(
                    DeliveryReport.ship_to_city.label('city'),
                    func.count(DeliveryReport.dn_no).label('dn_count')
                ).filter(
                    DeliveryReport.customer_name == dealer_identifier
                ).group_by(
                    DeliveryReport.ship_to_city
                ).order_by(
                    func.count(DeliveryReport.dn_no).desc()
                ).limit(limit).all()
                
                return [{
                    'city': r.city,
                    'dn_count': r.dn_count
                } for r in results if r.city]
        except Exception:
            return []
    
    def _get_issues(self, dealer_data: Dict[str, Any]) -> List[str]:
        """Generate issues requiring action"""
        issues = []
        
        pending_dn = dealer_data.get('pending_dn', 0)
        pgi_pending = dealer_data.get('pgi_pending_dn', 0)
        pod_pending = dealer_data.get('pod_pending_dn', 0)
        avg_delivery_days = dealer_data.get('avg_delivery_days', 0)
        
        if pgi_pending > 0:
            issues.append(f"{pgi_pending} DNs pending PGI")
        
        if pod_pending > 0:
            issues.append(f"{pod_pending} DNs pending POD")
        
        if pending_dn > 10:
            issues.append(f"{pending_dn} DNs pending - above threshold")
        
        if avg_delivery_days > 5:
            issues.append(f"High average delivery days: {avg_delivery_days:.1f}")
            
        return issues
    
    def _get_warehouse_insights(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate warehouse-specific insights"""
        insights = []
        identity = dashboard.get('identity', {})
        distance = dashboard.get('distance', {})
        
        warehouse = identity.get('warehouse', '')
        warehouse_distance = distance.get('distance_km', None)
        estimated_transit = distance.get('estimated_delivery', '')
        
        if warehouse_distance:
            insights.append(f"Distance to dealer: {warehouse_distance} km")
            insights.append(f"Estimated transit: {estimated_transit}")
            
        if warehouse:
            insights.append(f"Primary warehouse: {warehouse}")
            
        return insights

# ============================================================
# BLOCK 12: AI SUMMARY ENGINE (Optional)
# ============================================================

class AISummaryEngine:
    """AI-powered executive summary generation - OPTIONAL"""
    
    def __init__(self):
        self._client = None
        self._available = False
        
        if GROQ_AVAILABLE and GROQ_API_KEY:
            try:
                self._client = Groq(api_key=GROQ_API_KEY)
                self._available = True
                logger.info("✅ Groq AI initialized for Dealer Analytics")
            except Exception as e:
                logger.warning(f"⚠️ Groq initialization failed: {e}")
        else:
            logger.info("ℹ️ Groq AI not configured, using fallback summary")
    
    def generate_summary(self, dealer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate AI-powered executive summary - OPTIONAL"""
        if not self._available or not self._client:
            return self._generate_fallback_summary(dealer_data)
        
        try:
            identity = dealer_data.get('identity', {})
            delivery = dealer_data.get('delivery', {})
            sales = dealer_data.get('sales', {})
            performance = dealer_data.get('performance', {})
            
            prompt = f"""
            Analyze this dealer's performance and provide:
            1. Business Health (1-10)
            2. Delivery Performance (Excellent/Good/Fair/Poor)
            3. Sales Trend (Growing/Stable/Declining)
            4. Risk Level (Low/Medium/High)
            5. Key Recommendations (3 items)
            
            Dealer: {identity.get('customer_name', 'Unknown')}
            Revenue: PKR {sales.get('total_revenue', 0):,.2f}
            Total DN: {delivery.get('total_dn', 0)}
            Delivery Rate: {delivery.get('delivery_rate', 0):.1f}%
            Pending DN: {delivery.get('pending_dn', 0)}
            Business Score: {performance.get('business_score', 0)}/100
            """
            
            response = self._client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are a business intelligence analyst for Haier Logistics."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=300
            )
            
            summary = response.choices[0].message.content
            return self._parse_ai_response(summary, dealer_data)
            
        except Exception as e:
            logger.warning(f"⚠️ AI summary failed: {e}")
            return self._generate_fallback_summary(dealer_data)
    
    def _parse_ai_response(self, response: str, data: Dict[str, Any]) -> Dict[str, Any]:
        lines = response.split('\n')
        result = {
            'health_score': 7,
            'delivery_performance': 'Good',
            'sales_trend': 'Stable',
            'risk_level': 'Medium',
            'recommendations': []
        }
        
        for line in lines:
            line = line.strip()
            if 'Health' in line and ':' in line:
                try:
                    result['health_score'] = int(re.search(r'\d+', line).group())
                except:
                    pass
            elif 'Delivery' in line and ':' in line:
                result['delivery_performance'] = line.split(':')[-1].strip()
            elif 'Sales' in line and ':' in line:
                result['sales_trend'] = line.split(':')[-1].strip()
            elif 'Risk' in line and ':' in line:
                result['risk_level'] = line.split(':')[-1].strip()
            elif 'Recommendation' in line and not result['recommendations']:
                result['recommendations'].append(line)
        
        if not result['recommendations']:
            result['recommendations'] = self._get_default_recommendations(data)
        
        return result
    
    def _generate_fallback_summary(self, data: Dict[str, Any]) -> Dict[str, Any]:
        delivery = data.get('delivery', {})
        sales = data.get('sales', {})
        performance = data.get('performance', {})
        
        delivery_rate = delivery.get('delivery_rate', 0)
        pending = delivery.get('pending_dn', 0)
        revenue = sales.get('total_revenue', 0)
        score = performance.get('business_score', 0)
        
        if delivery_rate >= 90:
            health = 8
            delivery_perf = "Excellent"
        elif delivery_rate >= 75:
            health = 6
            delivery_perf = "Good"
        else:
            health = 4
            delivery_perf = "Fair"
        
        if pending > 0:
            risk = "Medium" if pending < 10 else "High"
        else:
            risk = "Low"
        
        return {
            'health_score': health,
            'delivery_performance': delivery_perf,
            'sales_trend': 'Stable' if revenue > 0 else 'Declining',
            'risk_level': risk,
            'recommendations': self._get_default_recommendations(data)
        }
    
    def _get_default_recommendations(self, data: Dict[str, Any]) -> List[str]:
        delivery = data.get('delivery', {})
        pending = delivery.get('pending_dn', 0)
        
        recs = []
        if pending > 0:
            recs.append(f"Resolve {pending} pending deliveries")
        if delivery.get('delivery_rate', 0) < 80:
            recs.append("Improve delivery performance")
        if delivery.get('pod_rate', 0) < 85:
            recs.append("Focus on POD completion")
        
        if not recs:
            recs = ["Maintain current performance", "Monitor key metrics", "Explore growth opportunities"]
        
        return recs[:3]

# ============================================================
# BLOCK 13: RESPONSE FORMATTER
# ============================================================

class ResponseFormatter:
    """Format responses for different output types"""
    
    def __init__(self):
        self._menu_renderer = DealerMenuRenderer()
        self._ai_engine = AISummaryEngine()
    
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
        elif answer.plan.format == ResponseFormat.LOGISTICS:
            return self._format_logistics(answer)
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
            "📍 *Dealer Details*",
            "─" * 40,
        ]
        
        if answer.dashboard:
            identity = answer.dashboard.get('identity', {})
            lines.append(f"Dealer Code: {identity.get('dealer_code', 'N/A')}")
            lines.append(f"Customer Code: {identity.get('customer_code', 'N/A')}")
            lines.append(f"City: {identity.get('city', 'N/A')}")
            lines.append(f"Warehouse: {identity.get('warehouse', 'N/A')}")
        
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
    
    def _format_logistics(self, answer: DealerAnswer) -> str:
        """Logistics format"""
        dealer = answer.plan.dealer or "Dealer"
        distance = answer.dashboard.get('distance', {}) if answer.dashboard else {}
        
        lines = [
            f"🚚 *Logistics - {dealer}*",
            "",
            f"Distance: {distance.get('distance_km', 'N/A')} KM",
            f"Driving Time: {distance.get('estimated_delivery', 'N/A')}",
            f"Transportation Zone: {distance.get('transportation_zone', 'N/A')}",
            f"Source: {distance.get('source', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        return "\n".join(lines)

# ============================================================
# BLOCK 14: MAIN DEALER ANALYTICS SERVICE WITH MENU
# ============================================================
# ============================================================
# BLOCK 14: MAIN DEALER ANALYTICS SERVICE WITH MENU
# ============================================================

class DealerAnalyticsService:
    """
    Dealer Domain AI Expert with Full Menu System
    Single entry point for all dealer-related business questions
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = VERSION
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = DealerMenuRenderer()
        self._formatter = ResponseFormatter()
        self._ai_engine = AISummaryEngine()
        
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
        logger.info(f"   Dealer Repository: ✅")
        logger.info(f"   AI Engine: {'✅' if self._ai_engine._available else '❌'}")
    
    # ============================================================
    # MAIN ENTRY POINT - SYNC VERSION
    # ============================================================
    
    def handle_message(self, message: str, sender: str) -> str:
        """
        MAIN ENTRY POINT for WhatsApp webhook.
        This is SYNC - called directly by ai_provider_service.
        """
        try:
            logger.info(f"📨 Dealer service received: '{message}' from {sender}")
            
            # Use sender phone as session ID
            session_id = sender
            
            # Process input
            result = self.process_menu_input(session_id, message)
            
            return result.get("response", self._menu_renderer.render_main_menu())
            
        except Exception as e:
            logger.error(f"❌ Error in handle_message: {e}", exc_info=True)
            return self._menu_renderer.render_main_menu()
    
    # ============================================================
    # ALIAS for compatibility (points to handle_message)
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str) -> str:
        """Alias for handle_message - for compatibility"""
        return self.handle_message(message, sender)
    
    # ============================================================
    # MENU AND PROCESSING METHODS
    # ============================================================
    
    def get_main_menu(self) -> str:
        """Get the main dealer menu"""
        return self._menu_renderer.render_main_menu()
    
    def get_dealer_selection_menu(self) -> str:
        """Get dealer selection prompt"""
        return self._menu_renderer.render_dealer_selection()
    
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
        
        logger.info(f"📥 Processing input: '{user_input}' for session {session_id}, state: {context.menu_state}")
        
        # Handle main menu navigation
        if user_input == "0" or user_input == "99":
            return self._handle_main_menu_return(context)
        
        # If awaiting dealer name, treat input as dealer name
        if context.awaiting_dealer:
            logger.info(f"🔍 Awaiting dealer name, checking: '{user_input}'")
            dealer_name = self._resolve_dealer_name(user_input)
            if dealer_name:
                context.current_dealer = dealer_name
                context.awaiting_dealer = False
                return self._get_dealer_dashboard(context, dealer_name)
            else:
                # Not found - show selection prompt again
                return {
                    "response": self._menu_renderer.render_dealer_selection(
                        f"Dealer '{user_input}' not found. Please try again:"
                    ),
                    "menu_type": "dealer_menu",
                    "action": "dealer_selection",
                    "data": {"awaiting": True},
                    "exit_menu": False
                }
        
        # Handle dealer names directly (quick commands)
        dealer_name = self._resolve_dealer_name(user_input)
        if dealer_name:
            context.current_dealer = dealer_name
            return self._get_dealer_dashboard(context, dealer_name)
        
        # Handle menu options based on state
        if context.menu_state == MenuState.MAIN:
            return self._handle_main_menu_option(context, user_input)
        
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
        logger.info(f"🎯 Handling menu option: '{option}'")
        
        option_map = {
            "1": ("dashboard", "Enter dealer name for dashboard:"),
            "2": ("revenue", "Enter dealer name for revenue:"),
            "3": ("units", "Enter dealer name for units:"),
            "4": ("logistics", "Enter dealer name for logistics:"),
            "5": ("warehouses", "Enter dealer name for warehouses:"),
            "6": ("cities", "Enter dealer name for cities:"),
            "7": ("pending_dn", "Enter dealer name for pending DN:"),
            "8": ("pending_pgi", "Enter dealer name for pending PGI:"),
            "9": ("pending_pod", "Enter dealer name for pending POD:"),
            "10": ("comparison", "Enter first dealer name for comparison:"),
            "11": ("ranking", "Showing dealer rankings by revenue"),
            "12": ("trend", "Enter dealer name for monthly trend:"),
            "13": ("executive", "Enter dealer name for executive summary:"),
            "14": ("ai_insights", "Enter dealer name for AI insights:"),
            "15": ("recommendations", "Enter dealer name for recommendations:"),
            "16": ("performance", "Enter dealer name for performance:"),
            "17": ("score", "Enter dealer name for score:"),
            "18": ("search", "Enter dealer name or search term:"),
        }
        
        if option in option_map:
            action, prompt = option_map[option]
            
            # Handle ranking directly (doesn't need dealer name)
            if action == "ranking":
                return self._handle_ranking(context)
            
            # Handle comparison (needs two dealers)
            if action == "comparison":
                context.awaiting_comparison = True
                context.comparison_dealers = []
                return {
                    "response": self._menu_renderer.render_comparison_selection(),
                    "menu_type": "dealer_menu",
                    "action": "comparison_selection",
                    "data": {"awaiting": True},
                    "exit_menu": False
                }
            
            # Set awaiting dealer state
            context.awaiting_dealer = True
            context.selected_option = action
            
            return {
                "response": self._menu_renderer.render_dealer_selection(prompt),
                "menu_type": "dealer_menu",
                "action": "dealer_selection",
                "data": {"awaiting": True, "option": action},
                "exit_menu": False
            }
        
        # Handle comparison input
        if context.awaiting_comparison:
            return self._handle_comparison_input(context, option)
        
        # Handle search
        if context.selected_option == "search":
            return self._handle_search(context, option)
        
        # Default: show menu
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "dealer_menu",
            "action": "invalid_option",
            "data": {},
            "exit_menu": False
        }
    
    def _handle_comparison_input(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Handle comparison dealer input"""
        resolved = self._resolve_dealer_name(dealer_name)
        
        if not resolved:
            return {
                "response": self._menu_renderer.render_comparison_selection() + f"\n\nDealer '{dealer_name}' not found. Try again:",
                "menu_type": "dealer_menu",
                "action": "comparison_selection",
                "data": {"awaiting": True},
                "exit_menu": False
            }
        
        context.comparison_dealers.append(resolved)
        
        if len(context.comparison_dealers) == 1:
            return {
                "response": "Enter second dealer name:",
                "menu_type": "dealer_menu",
                "action": "comparison_selection",
                "data": {"awaiting": True, "first_dealer": resolved},
                "exit_menu": False
            }
        else:
            # Both dealers selected, perform comparison
            dealer1, dealer2 = context.comparison_dealers
            context.awaiting_comparison = False
            context.comparison_dealers = []
            return self._compare_dealers(context, dealer1, dealer2)
    
    def _resolve_dealer_name(self, name: str) -> Optional[str]:
        """Resolve dealer name from database"""
        if not name or not name.strip():
            return None
        
        name_lower = name.lower().strip()
        
        try:
            with self._session() as session:
                # Try exact match first
                result = session.query(DeliveryReport.customer_name).filter(
                    func.lower(DeliveryReport.customer_name) == name_lower
                ).first()
                
                if result:
                    return result.customer_name
                
                # Try partial match
                results = session.query(DeliveryReport.customer_name).filter(
                    func.lower(DeliveryReport.customer_name).ilike(f"%{name_lower}%")
                ).distinct().limit(5).all()
                
                if results:
                    return results[0].customer_name
                
                # Try cleaning dealer name
                cleaned = _clean_dealer_name(name_lower)
                if cleaned != name_lower:
                    results = session.query(DeliveryReport.customer_name).filter(
                        func.lower(DeliveryReport.customer_name).ilike(f"%{cleaned}%")
                    ).distinct().limit(5).all()
                    if results:
                        return results[0].customer_name
                
                return None
                
        except Exception as e:
            logger.error(f"Error resolving dealer name '{name}': {e}")
            return None
    
    def _get_dealer_dashboard(self, context: DealerContext, dealer_name: str) -> Dict[str, Any]:
        """Get dealer dashboard"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard = builder.build(dealer_name)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ No data found for dealer: {dealer_name}\n\n0. Main Menu\n99. Back",
                        "menu_type": "dealer_menu",
                        "action": "dealer_not_found",
                        "data": {},
                        "exit_menu": False
                    }
                
                response = self._menu_renderer.render_dealer_dashboard(dealer_name, dashboard)
                
                return {
                    "response": response,
                    "menu_type": "dealer_menu",
                    "action": "dashboard",
                    "data": {"dealer": dealer_name, "dashboard": dashboard},
                    "exit_menu": False
                }
                
        except Exception as e:
            logger.error(f"Error getting dealer dashboard: {e}")
            return {
                "response": f"⚠️ Error loading dashboard: {str(e)}\n\n0. Main Menu\n99. Back",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {},
                "exit_menu": False
            }
    
    def _handle_ranking(self, context: DealerContext) -> Dict[str, Any]:
        """Handle ranking request"""
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                ranking = repo.get_top_dealers_by_revenue(10)
                
                if not ranking:
                    return {
                        "response": "📋 No dealer data available.\n\n0. Main Menu\n99. Back",
                        "menu_type": "dealer_menu",
                        "action": "ranking",
                        "data": {},
                        "exit_menu": False
                    }
                
                response = self._menu_renderer.render_ranking(ranking, "revenue", 10)
                
                return {
                    "response": response,
                    "menu_type": "dealer_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
                
        except Exception as e:
            logger.error(f"Error getting ranking: {e}")
            return {
                "response": f"⚠️ Error loading ranking: {str(e)}\n\n0. Main Menu\n99. Back",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {},
                "exit_menu": False
            }
    
    def _compare_dealers(self, context: DealerContext, dealer1: str, dealer2: str) -> Dict[str, Any]:
        """Compare two dealers"""
        try:
            with self._session() as session:
                builder = DealerDashboardBuilder(session)
                dashboard1 = builder.build(dealer1)
                dashboard2 = builder.build(dealer2)
                
                if not dashboard1 or not dashboard2:
                    return {
                        "response": f"⚠️ Could not find data for one or both dealers.\n\n0. Main Menu\n99. Back",
                        "menu_type": "dealer_menu",
                        "action": "comparison_error",
                        "data": {},
                        "exit_menu": False
                    }
                
                # Extract metrics for comparison
                metrics = {}
                
                # Dealer 1 metrics
                delivery1 = dashboard1.get('delivery', {})
                sales1 = dashboard1.get('sales', {})
                perf1 = dashboard1.get('performance', {})
                
                metrics[f"{dealer1}_metrics"] = {
                    "Revenue": _format_currency(sales1.get('total_revenue', 0)),
                    "Total DN": str(delivery1.get('total_dn', 0)),
                    "Pending DN": str(delivery1.get('pending_dn', 0)),
                    "Delivery Rate": f"{delivery1.get('delivery_rate', 0):.1f}%",
                    "POD Rate": f"{delivery1.get('pod_rate', 0):.1f}%",
                    "Business Score": f"{perf1.get('business_score', 0):.1f}",
                    "Performance Tier": perf1.get('performance_tier', 'Standard')
                }
                
                # Dealer 2 metrics
                delivery2 = dashboard2.get('delivery', {})
                sales2 = dashboard2.get('sales', {})
                perf2 = dashboard2.get('performance', {})
                
                metrics[f"{dealer2}_metrics"] = {
                    "Revenue": _format_currency(sales2.get('total_revenue', 0)),
                    "Total DN": str(delivery2.get('total_dn', 0)),
                    "Pending DN": str(delivery2.get('pending_dn', 0)),
                    "Delivery Rate": f"{delivery2.get('delivery_rate', 0):.1f}%",
                    "POD Rate": f"{delivery2.get('pod_rate', 0):.1f}%",
                    "Business Score": f"{perf2.get('business_score', 0):.1f}",
                    "Performance Tier": perf2.get('performance_tier', 'Standard')
                }
                
                # Generate explanation
                score1 = perf1.get('business_score', 0)
                score2 = perf2.get('business_score', 0)
                if score1 > score2:
                    explanation = f"{dealer1} has a higher business score ({score1:.1f}) vs {dealer2} ({score2:.1f})"
                elif score2 > score1:
                    explanation = f"{dealer2} has a higher business score ({score2:.1f}) vs {dealer1} ({score1:.1f})"
                else:
                    explanation = "Both dealers have similar business scores."
                
                metrics['explanation'] = explanation
                
                response = self._menu_renderer.render_comparison_result(dealer1, dealer2, metrics)
                
                return {
                    "response": response,
                    "menu_type": "dealer_menu",
                    "action": "comparison",
                    "data": {"dealer1": dealer1, "dealer2": dealer2},
                    "exit_menu": False
                }
                
        except Exception as e:
            logger.error(f"Error comparing dealers: {e}")
            return {
                "response": f"⚠️ Error comparing dealers: {str(e)}\n\n0. Main Menu\n99. Back",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {},
                "exit_menu": False
            }
    
    def _handle_search(self, context: DealerContext, query: str) -> Dict[str, Any]:
        """Handle dealer search"""
        try:
            with self._session() as session:
                repo = DealerRepository(session)
                results = repo.search_dealers(query)
                
                if not results:
                    return {
                        "response": f"🔍 No dealers found matching '{query}'\n\n0. Main Menu\n99. Back",
                        "menu_type": "dealer_menu",
                        "action": "search",
                        "data": {},
                        "exit_menu": False
                    }
                
                lines = [f"🔍 *Search Results for '{query}'*", ""]
                for i, dealer in enumerate(results[:10], 1):
                    lines.append(f"{i}. {dealer.get('dealer', 'Unknown')}")
                    lines.append(f"   City: {dealer.get('city', 'N/A')}")
                    lines.append(f"   Warehouse: {dealer.get('warehouse', 'N/A')}")
                    lines.append("")
                
                if len(results) > 10:
                    lines.append(f"... and {len(results) - 10} more results")
                
                lines.extend(["", "0. Main Menu", "99. Back"])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "dealer_menu",
                    "action": "search",
                    "data": {"results": results},
                    "exit_menu": False
                }
                
        except Exception as e:
            logger.error(f"Error searching dealers: {e}")
            return {
                "response": f"⚠️ Error searching: {str(e)}\n\n0. Main Menu\n99. Back",
                "menu_type": "dealer_menu",
                "action": "error",
                "data": {},
                "exit_menu": False
            }
    
    def _handle_quick_query(self, context: DealerContext, user_input: str) -> Dict[str, Any]:
        """Handle quick query (natural language)"""
        user_lower = user_input.lower()
        
        # Try quick commands
        if "top dealers" in user_lower or "ranking" in user_lower:
            return self._handle_ranking(context)
        
        # Try to extract dealer name
        dealer_name = self._resolve_dealer_name(user_input)
        if dealer_name:
            context.current_dealer = dealer_name
            return self._get_dealer_dashboard(context, dealer_name)
        
        # Show menu
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "dealer_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": False
        }
    
    def _get_context(self, session_id: str) -> DealerContext:
        """Get or create session context"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DealerContext()
            return self._contexts[session_id]
    
    def _update_context(self, session_id: str, dealer: str) -> None:
        """Update session context with dealer name"""
        with self._context_lock:
            if session_id in self._contexts:
                self._contexts[session_id].current_dealer = dealer
    
    def clear_context(self, session_id: str) -> None:
        """Clear session context"""
        with self._context_lock:
            if session_id in self._contexts:
                self._contexts[session_id].clear()
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()

# ============================================================
# BLOCK 15: SINGLETON FACTORY
# ============================================================

_dealer_service: Optional[DealerAnalyticsService] = None

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance of DealerAnalyticsService"""
    global _dealer_service
    if _dealer_service is None:
        _dealer_service = DealerAnalyticsService()
    return _dealer_service

# ============================================================
# BLOCK 16: EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "get_dealer_service",
    "DealerContext",
    "DealerAnswer",
    "IntentType",
    "MenuState",
    "VERSION"
]
