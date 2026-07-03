"""
File: app/services/dn_analysis.py
Version: 19.0 - COMPLETE DN DOMAIN AI EXPERT - ALL DN QUESTIONS ANSWERED HERE

Purpose: Answer ALL DN-related business questions through a single entry point
         PostgreSQL is the ONLY source of truth.
         
THIS FILE HANDLES EVERYTHING DN-RELATED:
- ✅ All DN menu options (1-19)
- ✅ Natural language DN queries
- ✅ DN number auto-detection
- ✅ DN status, history, timeline
- ✅ Transit analysis, distance
- ✅ Pending DNs, PGI, POD
- ✅ Delayed DNs, recent DNs
- ✅ Search, comparison, ranking
- ✅ Insights, SLA, aging, trends
- ✅ Forecast, recommendations
- ✅ Root cause analysis
- ✅ Stay in DN service until "99"

Status: ENTERPRISE READY - ALL DN QUESTIONS GO THROUGH THIS FILE
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Set, Callable, Mapping, Sequence

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
    import openrouteservice
except ImportError:
    openrouteservice = None

try:
    from geopy.geocoders import Nominatim
except ImportError:
    Nominatim = None

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

CACHE_TTL = max(60, int(os.getenv("DN_ANALYTICS_CACHE_TTL", "300")))
USE_SEMANTIC_SEARCH = os.getenv("USE_SEMANTIC_SEARCH", "true").lower() == "true"
USE_AI_EXPLANATION = os.getenv("USE_AI_EXPLANATION", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
SLA_TARGET_DAYS = int(os.getenv("DN_SLA_TARGET_DAYS", "3"))
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

DN_ALIASES: dict[str, str] = {
    "dn": "delivery note",
    "dns": "delivery notes",
    "delivery note": "dn",
}

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class IntentType(Enum):
    """DN question intent types"""
    DASHBOARD = "dashboard"
    STATUS = "status"
    HISTORY = "history"
    SUMMARY = "summary"
    TIMELINE = "timeline"
    TRANSIT = "transit"
    PENDING = "pending"
    PGI = "pgi"
    POD = "pod"
    DELAYED = "delayed"
    RECENT = "recent"
    SEARCH = "search"
    COMPARISON = "comparison"
    RANK = "rank"
    TREND = "trend"
    FORECAST = "forecast"
    INSIGHTS = "insights"
    RECOMMENDATIONS = "recommendations"
    ROOT_CAUSE = "root_cause"
    SLA = "sla"
    AGING = "aging"
    DISTANCE = "distance"
    REVENUE = "revenue"
    UNITS = "units"
    CUSTOMER = "customer"
    WAREHOUSE = "warehouse"
    DEALER = "dealer"
    CITY = "city"
    MENU = "menu"
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu navigation states"""
    MAIN = "main"
    DN_SELECTION = "dn_selection"
    COMPARISON_SELECTION = "comparison_selection"
    ANALYTICS = "analytics"
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
    TIMELINE = "timeline"

# ============================================================
# BLOCK 5: DATACLASSES
# ============================================================

@dataclass
class DNContext:
    """Session context for DN queries"""
    current_dn: Optional[str] = None
    last_question: Optional[str] = None
    last_intent: Optional[IntentType] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    session_start: datetime = field(default_factory=datetime.now)
    menu_state: MenuState = MenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dns: List[str] = field(default_factory=list)
    awaiting_dn: bool = False
    awaiting_comparison: bool = False
    last_analytics: Optional[Dict[str, Any]] = None
    search_results: Optional[List[Dict[str, Any]]] = None
    
    def set_dn(self, dn: str) -> None:
        self.current_dn = dn
    
    def get_dn(self) -> Optional[str]:
        return self.current_dn
    
    def clear(self) -> None:
        self.current_dn = None
        self.last_question = None
        self.last_intent = None
        self.conversation_history = []
        self.menu_state = MenuState.MAIN
        self.selected_option = None
        self.comparison_dns = []
        self.awaiting_dn = False
        self.awaiting_comparison = False
        self.last_analytics = None
        self.search_results = None

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

def _get_status_emoji(status: str) -> str:
    """Get emoji for status"""
    status_map = {
        "Delivered": "✅",
        "Completed": "✅",
        "In Transit": "🚚",
        "Pending PGI": "⏳",
        "Pending POD": "📋",
        "Pending DN": "📦",
        "Delayed": "⚠️",
        "Overdue": "🚨",
        "SLA Breach": "🚨",
    }
    return status_map.get(status, "📊")

def _get_priority_emoji(days: int) -> str:
    """Get priority emoji based on days"""
    if days <= 0:
        return "🟢"
    elif days <= 3:
        return "🟡"
    elif days <= 7:
        return "🟠"
    else:
        return "🔴"

def _extract_dn_numbers(text: str) -> List[str]:
    """Extract DN numbers from text"""
    return re.findall(r'(?<!\d)(\d{8,12})(?!\d)', text)

def _is_valid_dn(dn: str) -> bool:
    """Validate DN number (8-12 digits)"""
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12

# ============================================================
# BLOCK 7: DN MENU RENDERER
# ============================================================

class DNMenuRenderer:
    """Render DN analytics menus in WhatsApp format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Render main DN menu"""
        return "\n".join([
            "📦 *DN ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. DN History",
            "4. DN Timeline",
            "5. Transit Analysis",
            "6. Pending DN",
            "7. Pending PGI",
            "8. Pending POD",
            "9. Delayed DN",
            "10. Recent DN",
            "11. Search DN",
            "12. Compare DN",
            "13. DN Rankings",
            "14. DN Insights",
            "15. SLA Compliance",
            "16. Aging Analysis",
            "17. DN Trends",
            "18. DN Forecast",
            "19. AI Recommendations",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type DN number for dashboard",
            "• Compare [DN1] [DN2]",
            "• Search [keyword]",
            "• Status of [DN]",
            "• Where is [DN]",
            "• History of [DN]",
            "",
            "Reply with a number or DN number:"
        ])
    
    @staticmethod
    def render_dn_selection(prompt: str = "Enter DN number:") -> str:
        """Render DN selection prompt"""
        return "\n".join([
            "🔍 *DN Selection*",
            "",
            prompt,
            "",
            "💡 *Format:* 8-12 digit number",
            "Example: 1234567890",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_selection() -> str:
        """Render comparison DN selection"""
        return "\n".join([
            "🔄 *Compare DNs*",
            "",
            "Enter first DN number:",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_dn_dashboard(dn_no: str, data: Dict[str, Any]) -> str:
        """Render DN dashboard"""
        status = data.get('computed_delivery_status', 'Unknown')
        status_emoji = _get_status_emoji(status)
        dn_age = data.get('dn_age', 0)
        priority_emoji = _get_priority_emoji(dn_age if dn_age else 0)
        
        lines = [
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            f"{status_emoji} *Status:* {status}  {priority_emoji} Age: {dn_age} Days" if dn_age else f"{status_emoji} *Status:* {status}",
            "",
            "📊 *Key Information*",
            f"Customer: {data.get('customer_name', 'N/A')}",
            f"Dealer: {data.get('dealer_code', 'N/A')}",
            f"Warehouse: {data.get('warehouse', 'N/A')}",
            f"City: {data.get('ship_to_city', 'N/A')}",
            f"Division: {data.get('division', 'N/A')}",
            "",
            "💰 *Financials*",
            f"Revenue: PKR {float(data.get('total_revenue', 0)):,.2f}",
            f"Units: {data.get('total_units', 0):,}",
            f"Avg Price: PKR {float(data.get('total_revenue', 0)) / max(1, data.get('total_units', 0)):,.2f}",
            "",
            "📅 *Dates*",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"PGI: {_format_date(data.get('good_issue_date'))}",
            f"POD: {_format_date(data.get('pod_date'))}",
            "",
            "📈 *Aging*",
            f"DN Age: {data.get('dn_age', 0)} Days",
            f"PGI Aging: {data.get('pgi_aging', 'N/A')} Days",
            f"POD Aging: {data.get('pod_aging', 'N/A')} Days",
            "",
            "🚚 *Delivery*",
            f"Transit Days: {data.get('transit_days', 'N/A')}",
            f"Delivery Days: {data.get('delivery_days', 'N/A')}",
            f"Distance: {data.get('distance_km', 'N/A')} KM",
            f"Est. Time: {data.get('estimated_delivery_time', 'N/A')}",
            "",
            "📋 *Details*",
            f"Materials: {data.get('material_count', 0):,}",
            f"Models: {data.get('model_count', 0):,}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main",
            "",
            "📌 *Try:* 'Status {dn_no}' or 'History {dn_no}'"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_dn_status(dn_no: str, data: Dict[str, Any]) -> str:
        """Render DN status"""
        status = data.get('computed_delivery_status', 'Unknown')
        status_emoji = _get_status_emoji(status)
        
        lines = [
            f"📊 *DN {dn_no} - Status*",
            "",
            f"{status_emoji} *{status}*",
            "",
            "📋 *Status Details*",
            f"Delivery Status: {data.get('delivery_status', 'N/A')}",
            f"PGI Status: {data.get('pgi_status', 'N/A')}",
            f"POD Status: {data.get('pod_status', 'N/A')}",
            "",
            "📅 *Timeline*",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"PGI: {_format_date(data.get('good_issue_date'))}",
            f"POD: {_format_date(data.get('pod_date'))}",
            "",
            "⏱️ *Aging*",
            f"DN Age: {data.get('dn_age', 0)} Days",
            f"PGI Aging: {data.get('pgi_aging', 'N/A')} Days",
            f"POD Aging: {data.get('pod_aging', 'N/A')} Days",
            "",
            f"👤 Customer: {data.get('customer_name', 'N/A')}",
            f"🏭 Warehouse: {data.get('warehouse', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_dn_history(dn_no: str, events: List[Dict[str, Any]]) -> str:
        """Render DN history"""
        lines = [
            f"📋 *DN {dn_no} - History*",
            "",
            "📅 *Event Timeline:*",
            "",
        ]
        
        if not events:
            lines.append("No history found for this DN.")
        else:
            for event in events:
                timestamp = event.get('timestamp', 'N/A')
                status = event.get('status', '')
                description = event.get('description', '')
                emoji = _get_status_emoji(status)
                lines.append(f"{emoji} *{timestamp}* - {status}")
                lines.append(f"   {description}")
                lines.append("")
        
        lines.extend([
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_dn_timeline(dn_no: str, events: List[Dict[str, Any]]) -> str:
        """Render DN timeline with visual representation"""
        lines = [
            f"📅 *DN {dn_no} - Timeline*",
            "",
        ]
        
        if not events:
            lines.append("No timeline found for this DN.")
        else:
            for i, event in enumerate(events):
                timestamp = event.get('timestamp', 'N/A')
                status = event.get('status', '')
                description = event.get('description', '')
                emoji = _get_status_emoji(status)
                
                if i == 0:
                    prefix = "🟢 START"
                elif i == len(events) - 1:
                    prefix = "🏁 END"
                else:
                    prefix = "⬇️ STEP"
                
                lines.append(f"{prefix} {emoji} *{timestamp}*")
                lines.append(f"   → {status}: {description}")
                lines.append("")
        
        lines.extend([
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_transit_analysis(dn_no: str, data: Dict[str, Any]) -> str:
        """Render transit analysis"""
        lines = [
            f"🚚 *Transit Analysis - DN {dn_no}*",
            "",
            "📍 *Route*",
            f"Warehouse: {data.get('warehouse', 'N/A')}",
            f"Delivery: {data.get('delivery_location', 'N/A')}",
            f"City: {data.get('ship_to_city', 'N/A')}",
            "",
            "📏 *Distance*",
            f"Distance: {data.get('distance_km', 'N/A')} KM",
            f"Est. Time: {data.get('estimated_delivery_time', 'N/A')}",
            f"Source: {data.get('distance_source', 'N/A')}",
            "",
            "⏱️ *Timing*",
            f"Transit Days: {data.get('transit_days', 'N/A')}",
            f"Delivery Days: {data.get('delivery_days', 'N/A')}",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        return "\n".join(lines)
    
    @staticmethod
    def render_pending_list(title: str, dns: List[Dict[str, Any]]) -> str:
        """Render pending DN list"""
        if not dns:
            return f"📋 *{title}*\n\n✅ No pending DNs found."
        
        lines = [f"📋 *{title}*", ""]
        lines.append(f"Total: {len(dns)} pending DNs")
        lines.append("")
        
        for i, item in enumerate(dns[:10], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', 'N/A')
            status = item.get('computed_delivery_status', 'N/A')
            created = _format_date(item.get('dn_create_date'))
            status_emoji = _get_status_emoji(status)
            
            lines.append(f"{i}. {status_emoji} *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Status: {status}")
            lines.append(f"   Created: {created}")
            lines.append("")
        
        if len(dns) > 10:
            lines.append(f"... and {len(dns) - 10} more")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "revenue", limit: int = 10) -> str:
        """Render DN rankings"""
        lines = [
            f"🏆 *DN Rankings by {metric.title()}*",
            "",
        ]
        
        for i, item in enumerate(ranking[:limit], 1):
            dn = item.get('dn_no', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} DN {dn}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison_result(dn1: str, dn2: str, metrics: Dict[str, Any]) -> str:
        """Render comparison result"""
        lines = [
            f"🔄 *Comparison: DN {dn1} vs DN {dn2}*",
            "",
            "───────────────────",
            "",
        ]
        
        metrics1 = metrics.get(f"{dn1}_metrics", {})
        metrics2 = metrics.get(f"{dn2}_metrics", {})
        
        all_keys = set(metrics1.keys()) | set(metrics2.keys())
        
        for key in sorted(all_keys):
            v1 = metrics1.get(key, "N/A")
            v2 = metrics2.get(key, "N/A")
            
            if isinstance(v1, str) and isinstance(v2, str):
                try:
                    num1 = float(re.sub(r'[^\d.]', '', v1))
                    num2 = float(re.sub(r'[^\d.]', '', v2))
                    if key.lower() in ['pending', 'delivery days']:
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
    def render_sla_report(dns: List[Dict[str, Any]]) -> str:
        """Render SLA compliance report"""
        if not dns:
            return "📋 *SLA Report*\n\nNo DNs found."
        
        total = len(dns)
        compliant = sum(1 for dn in dns if dn.get('sla_compliant', False))
        compliance_pct = _percent(compliant, total)
        
        lines = [
            "📋 *SLA Compliance Report*",
            "",
            f"Total DNs: {total}",
            f"Compliant: {compliant} ({compliance_pct:.1f}%)",
            f"Breached: {total - compliant} ({100 - compliance_pct:.1f}%)",
            "",
            "📊 *Status Breakdown*",
        ]
        
        status_counts = defaultdict(int)
        for dn in dns:
            status = dn.get('computed_delivery_status', 'Unknown')
            status_counts[status] += 1
        
        for status, count in sorted(status_counts.items(), key=lambda x: x[1], reverse=True):
            emoji = _get_status_emoji(status)
            lines.append(f"{emoji} {status}: {count}")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_aging_report(data: Dict[str, Any]) -> str:
        """Render aging report"""
        lines = [
            "📈 *DN Aging Analysis*",
            "",
            "📊 *Age Distribution*",
        ]
        
        age_groups = data.get('age_groups', {})
        for age_group, count in sorted(age_groups.items()):
            lines.append(f"• {age_group}: {count}")
        
        lines.extend([
            "",
            "📋 *Summary*",
            f"Total DNs: {data.get('total', 0)}",
            f"Average Age: {data.get('average_age', 0):.1f} Days",
            f"Max Age: {data.get('max_age', 0)} Days",
            f"Min Age: {data.get('min_age', 0)} Days",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_insights(insights: List[str], recommendations: List[str]) -> str:
        """Render insights and recommendations"""
        lines = [
            "💡 *DN Insights*",
            "",
        ]
        
        if insights:
            lines.append("📊 *Key Findings*")
            for insight in insights:
                lines.append(f"• {insight}")
            lines.append("")
        
        if recommendations:
            lines.append("🎯 *Recommendations*")
            for rec in recommendations:
                lines.append(f"• {rec}")
            lines.append("")
        
        lines.extend([
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_trends(trend_data: Dict[str, Any]) -> str:
        """Render DN trends"""
        lines = [
            "📈 *DN Trends*",
            "",
        ]
        
        daily = trend_data.get('daily', [])
        if daily:
            lines.append("📅 *Last 7 Days*")
            for day in daily[:7]:
                lines.append(f"• {day.get('date', 'N/A')}: {day.get('count', 0)} DNs, PKR {day.get('revenue', 0):,.2f}")
            lines.append("")
        
        weekly = trend_data.get('weekly', [])
        if weekly:
            lines.append("📅 *Weekly Summary*")
            for week in weekly[:4]:
                lines.append(f"• Week {week.get('week', 'N/A')}: {week.get('count', 0)} DNs, PKR {week.get('revenue', 0):,.2f}")
            lines.append("")
        
        growth = trend_data.get('growth', 0)
        lines.append(f"📈 *Growth Rate: {growth:+.1f}%*")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_forecast(forecast_data: Dict[str, Any]) -> str:
        """Render forecast"""
        lines = [
            "🔮 *DN Forecast*",
            "",
            f"Expected DNs: {forecast_data.get('expected_count', 0):,}",
            f"Expected Revenue: PKR {forecast_data.get('expected_revenue', 0):,.2f}",
            f"Expected Units: {forecast_data.get('expected_units', 0):,}",
            "",
            "📊 *Confidence Interval*",
            f"Lower Bound: {forecast_data.get('lower_bound', 0):,}",
            f"Upper Bound: {forecast_data.get('upper_bound', 0):,}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)

# ============================================================
# BLOCK 8: INTENT ENGINE - ALL DN INTENTS
# ============================================================

class IntentEngine:
    """AI-powered intent detection for ALL DN questions"""
    
    INTENT_PATTERNS = {
        IntentType.DASHBOARD: [
            r"(?:show|display|get|view).*(?:dn|delivery note).*(?:dashboard|details|info)",
            r"dn\s+(\d{8,12})",
            r"^(\d{8,12})$",
        ],
        IntentType.STATUS: [
            r"(?:status|state|current).*(?:dn|delivery note)",
            r"what.*status.*dn",
            r"dn status",
            r"where is dn\s+(\d{8,12})",
            r"track dn\s+(\d{8,12})",
            r"check dn\s+(\d{8,12})",
        ],
        IntentType.HISTORY: [
            r"(?:history|tracking).*(?:dn|delivery note)",
            r"dn history",
            r"what happened to dn\s+(\d{8,12})",
            r"history of dn\s+(\d{8,12})",
        ],
        IntentType.TIMELINE: [
            r"(?:timeline|sequence|chronology)",
            r"dn timeline",
            r"show timeline for dn\s+(\d{8,12})",
        ],
        IntentType.TRANSIT: [
            r"(?:transit|travel|journey|route).*(?:dn|delivery)",
            r"transit time",
            r"travel time",
            r"how far is dn\s+(\d{8,12})",
            r"distance of dn\s+(\d{8,12})",
        ],
        IntentType.PENDING: [
            r"(?:pending|outstanding|backlog|overdue).*(?:dn|delivery)",
            r"pending dns",
            r"undelivered dns",
            r"all pending",
        ],
        IntentType.PGI: [
            r"(?:pgi|goods issue).*(?:pending|status)",
            r"pending pgi",
            r"pgi not done",
        ],
        IntentType.POD: [
            r"(?:pod|proof of delivery).*(?:pending|status|missing)",
            r"pending pod",
            r"pod missing",
        ],
        IntentType.DELAYED: [
            r"(?:delayed|late|overdue|stuck).*(?:dn|delivery)",
            r"delayed dns",
            r"overdue deliveries",
        ],
        IntentType.RECENT: [
            r"(?:recent|latest|new).*(?:dn|delivery)",
            r"recent dns",
            r"latest dns",
        ],
        IntentType.SEARCH: [
            r"(?:search|find|lookup).*(?:dn|delivery|customer|warehouse)",
            r"search dn",
            r"find dn",
            r"lookup\s+([\w\s]+)",
        ],
        IntentType.COMPARISON: [
            r"compare\s+(\d+)\s+and\s+(\d+)",
            r"dn\s+(\d+)\s+vs\s+(\d+)",
            r"comparison",
            r"vs",
        ],
        IntentType.RANK: [
            r"(?:top|best|highest).*(?:dn|delivery)",
            r"dn ranking",
            r"top dns",
            r"best performing dns",
        ],
        IntentType.TREND: [
            r"(?:trend|pattern|change).*(?:dn|delivery)",
            r"dn trend",
            r"delivery trend",
        ],
        IntentType.FORECAST: [
            r"(?:forecast|predict|project).*(?:dn|delivery)",
            r"dn forecast",
            r"predict dns",
        ],
        IntentType.INSIGHTS: [
            r"(?:insight|analytics|key findings).*(?:dn|delivery)",
            r"dn insights",
            r"what does dn data show",
        ],
        IntentType.RECOMMENDATIONS: [
            r"(?:recommend|suggest|advice).*(?:dn|delivery)",
            r"dn recommendations",
            r"how to improve dns",
        ],
        IntentType.ROOT_CAUSE: [
            r"why (?:is|are|was|were)\s+(\w+)\s+(?:delayed|late|pending)",
            r"(?:reason|cause).*(?:delay|issue).*(?:dn|delivery)",
            r"why is dn\s+(\d{8,12})\s+(?:delayed|late)",
        ],
        IntentType.SLA: [
            r"(?:sla|service level).*(?:compliance|performance)",
            r"sla compliance",
            r"delivery sla",
        ],
        IntentType.AGING: [
            r"(?:aging|age|aged).*(?:dn|delivery)",
            r"dn aging",
            r"oldest dns",
            r"dn age analysis",
        ],
        IntentType.DISTANCE: [
            r"(?:distance|how far).*(?:dn|delivery)",
            r"distance from warehouse",
        ],
        IntentType.REVENUE: [
            r"(?:revenue|amount).*(?:dn|delivery)",
            r"dn revenue",
            r"highest revenue dn",
        ],
        IntentType.UNITS: [
            r"(?:units|quantity).*(?:dn|delivery)",
            r"dn units",
            r"highest units dn",
        ],
        IntentType.CUSTOMER: [
            r"(?:customer).*(?:dn|delivery)",
            r"dn customer",
            r"customer dns",
        ],
        IntentType.WAREHOUSE: [
            r"(?:warehouse).*(?:dn|delivery)",
            r"dn warehouse",
            r"warehouse dns",
        ],
        IntentType.DEALER: [
            r"(?:dealer).*(?:dn|delivery)",
            r"dn dealer",
            r"dealer dns",
        ],
        IntentType.CITY: [
            r"(?:city).*(?:dn|delivery)",
            r"dn city",
            r"city dns",
        ],
        IntentType.MENU: [
            r"menu",
            r"dn menu",
            r"options",
            r"help",
            r"show menu",
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
                    Route(name="dn_dashboard", utterances=["show dn", "dn dashboard", "dn details"]),
                    Route(name="dn_status", utterances=["dn status", "status of dn", "where is dn"]),
                    Route(name="dn_history", utterances=["dn history", "history of dn", "track dn"]),
                    Route(name="dn_transit", utterances=["transit time", "travel time", "how far"]),
                    Route(name="pending_dns", utterances=["pending dns", "pending deliveries"]),
                    Route(name="dn_comparison", utterances=["compare dns", "dn vs dn"]),
                    Route(name="dn_ranking", utterances=["top dns", "dn ranking"]),
                    Route(name="dn_trends", utterances=["dn trends", "delivery trends"]),
                    Route(name="dn_insights", utterances=["dn insights", "analysis"]),
                    Route(name="dn_recommendations", utterances=["recommendations", "how to improve"]),
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
        if question_lower in ["menu", "dn menu", "options", "help", "show menu"]:
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
                    intent_name = result.name.replace("dn_", "")
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
                if keyword in ["pending", "overdue", "backlog"]:
                    best_intent = IntentType.PENDING
                    best_score = 0.5
                    break
                elif keyword in ["status", "state", "where", "track"]:
                    best_intent = IntentType.STATUS
                    best_score = 0.5
                    break
                elif keyword in ["history", "tracking", "happened"]:
                    best_intent = IntentType.HISTORY
                    best_score = 0.5
                    break
                elif keyword in ["compare", "vs", "versus"]:
                    best_intent = IntentType.COMPARISON
                    best_score = 0.6
                    break
                elif keyword in ["search", "find", "lookup"]:
                    best_intent = IntentType.SEARCH
                    best_score = 0.5
                    break
                elif keyword in ["top", "ranking", "best"]:
                    best_intent = IntentType.RANK
                    best_score = 0.5
                    break
                elif keyword in ["transit", "travel", "distance", "far"]:
                    best_intent = IntentType.TRANSIT
                    best_score = 0.5
                    break
                elif keyword in ["forecast", "predict"]:
                    best_intent = IntentType.FORECAST
                    best_score = 0.5
                    break
                elif keyword in ["insight", "analysis"]:
                    best_intent = IntentType.INSIGHTS
                    best_score = 0.5
                    break
                elif keyword in ["recommend", "suggest", "improve"]:
                    best_intent = IntentType.RECOMMENDATIONS
                    best_score = 0.5
                    break
                elif keyword in ["sla", "compliance"]:
                    best_intent = IntentType.SLA
                    best_score = 0.5
                    break
                elif keyword in ["aging", "age", "oldest"]:
                    best_intent = IntentType.AGING
                    best_score = 0.5
                    break
        
        with self._lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        return best_intent, best_score

# ============================================================
# BLOCK 9: ENTITY EXTRACTION ENGINE
# ============================================================

class EntityEngine:
    """Entity extraction for DN questions"""
    
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
            "dn_numbers": [],
            "search_query": None,
            "customer_name": None,
            "warehouse": None,
            "city": None,
            "dealer": None,
            "limit": 20,
            "requires_comparison": False,
            "requires_forecast": False,
            "requires_trend": False,
        }
        
        # Extract DN numbers
        dns = _extract_dn_numbers(question_lower)
        if dns:
            entities["dn_numbers"] = dns
        
        # Check for comparison
        if "compare" in question_lower or "vs" in question_lower or "versus" in question_lower:
            entities["requires_comparison"] = True
            if len(entities["dn_numbers"]) >= 2:
                entities["comparison_dns"] = entities["dn_numbers"][:2]
        
        # Extract search query
        search = self._extract_search_query(question_lower)
        if search:
            entities["search_query"] = search
        
        # Extract customer name
        customer = self._extract_customer(question_lower)
        if customer:
            entities["customer_name"] = customer
        
        # Extract warehouse
        warehouse = self._extract_warehouse(question_lower)
        if warehouse:
            entities["warehouse"] = warehouse
        
        # Extract city
        city = self._extract_city(question_lower)
        if city:
            entities["city"] = city
        
        # Extract dealer
        dealer = self._extract_dealer(question_lower)
        if dealer:
            entities["dealer"] = dealer
        
        # Check for forecast
        if "forecast" in question_lower or "predict" in question_lower:
            entities["requires_forecast"] = True
        
        # Check for trend
        if "trend" in question_lower or "pattern" in question_lower:
            entities["requires_trend"] = True
        
        # Extract limit
        limit = self._extract_limit(question_lower)
        if limit:
            entities["limit"] = limit
        
        with self._lock:
            self._cache[cache_key] = entities.copy()
        
        return entities
    
    def _extract_search_query(self, text: str) -> Optional[str]:
        """Extract search query from text"""
        patterns = [
            r'(?:search|find|lookup|for)\s+([a-zA-Z0-9\s\-_]+)',
            r'(?:customer|dealer|warehouse)\s+([a-zA-Z0-9\s\-_]+)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                query = match.group(1).strip()
                if query and len(query) > 2:
                    return query
        
        return None
    
    def _extract_customer(self, text: str) -> Optional[str]:
        """Extract customer name"""
        match = re.search(r'(?:customer|cust)\s+([a-zA-Z0-9\s\-_]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _extract_warehouse(self, text: str) -> Optional[str]:
        """Extract warehouse name"""
        match = re.search(r'(?:warehouse|wh)\s+([a-zA-Z0-9\s\-_]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _extract_city(self, text: str) -> Optional[str]:
        """Extract city name"""
        match = re.search(r'(?:city|in)\s+([a-zA-Z\s]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _extract_dealer(self, text: str) -> Optional[str]:
        """Extract dealer name"""
        match = re.search(r'(?:dealer)\s+([a-zA-Z0-9\s\-_]+)', text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None
    
    def _extract_limit(self, text: str) -> Optional[int]:
        """Extract numeric limit from text"""
        patterns = [
            r"top\s+(\d+)",
            r"first\s+(\d+)",
            r"limit\s+(\d+)",
            r"(\d+)\s+(?:dns|deliveries|items)",
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
    """Route distance calculation"""
    
    def __init__(self) -> None:
        self._cache: TTLCache[str, tuple[float, float] | None] = TTLCache(512, 86_400)
        self._ors_key = os.getenv("OPENROUTESERVICE_API_KEY")
        self._geocoder = Nominatim(user_agent="dn-analysis-service", timeout=4) if Nominatim else None
    
    def _coordinates(self, location: str) -> tuple[float, float] | None:
        key = location.strip().casefold()
        if key in self._cache:
            return self._cache[key]
        
        coordinates = None
        
        normalized_key = key.replace(" warehouse", "").strip()
        if normalized_key in WAREHOUSE_COORDINATES:
            coordinates = WAREHOUSE_COORDINATES[normalized_key]
        elif key in WAREHOUSE_COORDINATES:
            coordinates = WAREHOUSE_COORDINATES[key]
        
        if coordinates is None and self._geocoder and key:
            try:
                result = self._geocoder.geocode(location, exactly_one=True)
                if result:
                    coordinates = (float(result.latitude), float(result.longitude))
            except Exception:
                pass
        
        self._cache[key] = coordinates
        return coordinates
    
    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        dlat, dlon = lat2 - lat1, lon2 - lon1
        value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        return 6_371.0088 * 2 * math.asin(math.sqrt(value))
    
    def calculate(self, origin_name: str | None, destination_name: str | None) -> Dict[str, Any]:
        if not origin_name or not destination_name:
            return {"distance_km": None, "estimated_delivery_time": None, "source": None}
        
        origin, destination = self._coordinates(origin_name), self._coordinates(destination_name)
        if not origin or not destination:
            return {"distance_km": None, "estimated_delivery_time": None, "source": None}
        
        if openrouteservice and self._ors_key:
            try:
                client = openrouteservice.Client(key=self._ors_key, timeout=5)
                route = client.directions(
                    [(origin[1], origin[0]), (destination[1], destination[0])],
                    profile="driving-car",
                )["routes"][0]["summary"]
                kilometres = round(float(route["distance"]) / 1000, 1)
                hours = float(route["duration"]) / 3600
                return {
                    "distance_km": kilometres,
                    "estimated_delivery_time": self._format_duration(hours),
                    "source": "openrouteservice"
                }
            except Exception:
                pass
        
        kilometres = round(self._haversine(origin, destination), 1)
        return {
            "distance_km": kilometres,
            "estimated_delivery_time": self._format_duration(kilometres / 45),
            "source": "haversine"
        }
    
    @staticmethod
    def _format_duration(hours: float) -> str:
        total_minutes = max(0, round(hours * 60))
        whole_hours, minutes = divmod(total_minutes, 60)
        return f"{whole_hours} Hours {minutes} Minutes" if minutes else f"{whole_hours} Hours"

# ============================================================
# BLOCK 11: DN DASHBOARD BUILDER
# ============================================================

class DNDashboardBuilder:
    """Build DN dashboards from database"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self.distance_service = DistanceService()
    
    def build(self, dn_no: str) -> Optional[Dict[str, Any]]:
        """Build dashboard for DN"""
        cache_key = dn_no.lower()
        
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                func.count(distinct(DeliveryReport.material_no)).label("material_count"),
                func.count(distinct(DeliveryReport.customer_model)).label("model_count"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).group_by(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.division,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
            ).first()
            
            if not query:
                return None
            
            today = datetime.now(timezone.utc).date()
            dn_date = query.dn_create_date
            issue_date = query.good_issue_date
            pod_date = query.pod_date
            pending = _flag(query.pending_flag) or not pod_date
            
            # Calculate aging
            pgi_aging = None
            pod_aging = None
            if dn_date and issue_date:
                pgi_aging = (issue_date - dn_date).days if issue_date else None
            if issue_date and pod_date:
                pod_aging = (pod_date - issue_date).days if pod_date else None
            delivery_aging = None
            if dn_date:
                delivery_aging = (pod_date or (today if pending else None) - dn_date).days if pod_date or pending else None
            
            # Distance
            distance = self.distance_service.calculate(
                query.warehouse or query.warehouse_code,
                query.delivery_location or query.ship_to_city
            )
            
            dashboard = {
                "dn_no": _text(query.dn_no),
                "customer_name": _text(query.customer_name),
                "dealer_code": _text(query.dealer_code),
                "warehouse": _text(query.warehouse),
                "warehouse_code": _text(query.warehouse_code),
                "sales_office": _text(query.sales_office),
                "sales_manager": _text(query.sales_manager),
                "division": _text(query.division),
                "ship_to_city": _text(query.ship_to_city),
                "delivery_location": _text(query.delivery_location),
                "total_units": int(query.total_units or 0),
                "total_revenue": float(query.total_revenue or 0.0),
                "dn_create_date": query.dn_create_date,
                "good_issue_date": query.good_issue_date,
                "pod_date": query.pod_date,
                "delivery_status": _text(query.delivery_status),
                "pgi_status": _text(query.pgi_status),
                "pod_status": _text(query.pod_status),
                "pending_flag": pending,
                "material_count": int(query.material_count or 0),
                "model_count": int(query.model_count or 0),
                "pgi_aging": pgi_aging,
                "pod_aging": pod_aging,
                "delivery_aging": delivery_aging,
                "distance_km": distance.get("distance_km"),
                "estimated_delivery_time": distance.get("estimated_delivery_time"),
                "distance_source": distance.get("source"),
                "computed_delivery_status": self._compute_status(query, dn_date, issue_date, pod_date, today),
                "dn_age": (today - dn_date).days if dn_date else None,
                "transit_days": (pod_date - issue_date).days if issue_date and pod_date else None,
                "delivery_days": (pod_date - dn_date).days if dn_date and pod_date else None,
                "sla_compliant": self._check_sla_compliance(query, dn_date, issue_date, pod_date, today),
            }
            
            # Generate insights
            dashboard["insights"] = self._generate_insights(dashboard)
            dashboard["recommendations"] = self._generate_recommendations(dashboard)
            
            with self._lock:
                self._cache[cache_key] = dashboard.copy()
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for DN {dn_no}: {e}")
            return None
    
    def _compute_status(self, query: Any, dn_date: date, issue: date, pod: date, today: date) -> str:
        delivery = str(query.delivery_status or "").casefold()
        pgi = str(query.pgi_status or "").casefold()
        pod_status = str(query.pod_status or "").casefold()
        
        if pod or "complete" in pod_status or "deliver" in delivery:
            return "Delivered" if "deliver" in delivery else "Completed"
        if not issue or "pending" in pgi:
            return "Pending PGI"
        if "pending" in pod_status:
            return "Pending POD"
        if issue and (today - issue).days > DN_DELAY_THRESHOLD_DAYS:
            return "Delayed"
        if issue:
            return "In Transit"
        return "Pending DN"
    
    def _check_sla_compliance(self, query: Any, dn_date: date, issue: date, pod: date, today: date) -> bool:
        """Check if DN meets SLA target"""
        if not dn_date:
            return True
        if pod:
            return (pod - dn_date).days <= SLA_TARGET_DAYS
        if issue:
            return (today - issue).days <= SLA_TARGET_DAYS
        return (today - dn_date).days <= SLA_TARGET_DAYS
    
    def _generate_insights(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate insights from dashboard"""
        insights = []
        
        status = dashboard.get('computed_delivery_status', '')
        dn_age = dashboard.get('dn_age', 0)
        revenue = dashboard.get('total_revenue', 0)
        units = dashboard.get('total_units', 0)
        distance = dashboard.get('distance_km', 0)
        transit = dashboard.get('transit_days', 0)
        
        if status == "Delivered" or status == "Completed":
            insights.append("✅ DN is successfully delivered")
        elif status == "Pending PGI":
            insights.append("⏳ DN is pending PGI - needs warehouse action")
        elif status == "Pending POD":
            insights.append("📋 DN is pending POD - needs customer confirmation")
        elif status == "Delayed":
            insights.append(f"⚠️ DN is delayed by {dn_age - DN_DELAY_THRESHOLD_DAYS} days")
        elif status == "In Transit":
            insights.append("🚚 DN is in transit")
        
        if revenue > 1000000:
            insights.append(f"💰 High value DN: PKR {revenue:,.2f}")
        elif revenue > 500000:
            insights.append(f"💰 Medium value DN: PKR {revenue:,.2f}")
        
        if units > 100:
            insights.append(f"📦 Large order: {units} units")
        elif units > 50:
            insights.append(f"📦 Medium order: {units} units")
        
        if distance and distance > 500:
            insights.append(f"📏 Long distance delivery: {distance} KM")
        elif distance and distance > 200:
            insights.append(f"📏 Medium distance delivery: {distance} KM")
        
        if transit and transit > 5:
            insights.append(f"⏱️ Long transit time: {transit} days")
        
        return insights
    
    def _generate_recommendations(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate recommendations"""
        recommendations = []
        
        status = dashboard.get('computed_delivery_status', '')
        dn_age = dashboard.get('dn_age', 0)
        transit = dashboard.get('transit_days', 0)
        distance = dashboard.get('distance_km', 0)
        
        if status == "Pending PGI":
            recommendations.append("🏭 Fast-track PGI processing at warehouse")
        elif status == "Pending POD":
            recommendations.append("📋 Follow up with customer for POD submission")
        elif status == "Delayed":
            recommendations.append("🚨 Escalate delayed DN for priority handling")
        elif status == "In Transit":
            if transit and transit > 3:
                recommendations.append("🚚 Track and expedite in-transit delivery")
        
        if distance and distance > 500:
            recommendations.append("📏 Consider alternate routing for long distance")
        
        if dn_age and dn_age > 10:
            recommendations.append("⚠️ Review DN aging and implement corrective actions")
        
        if not recommendations:
            recommendations.append("✅ Maintain current delivery performance")
        
        return recommendations

# ============================================================
# BLOCK 12: MAIN DN ANALYTICS SERVICE - ALL DN ANSWERS HERE
# ============================================================

class DNAnalysisService:
    """
    DN Domain AI Expert - ALL DN-related answers come through this file.
    PostgreSQL is the ONLY source of truth.
    """
    
    def __init__(self) -> None:
        self._service_name = "dn_analysis"
        self._version = "19.0.0-menu"
        self._startup_time = datetime.utcnow().isoformat()
        
        # Initialize engines
        self._intent_engine = IntentEngine()
        self._entity_engine = EntityEngine()
        self._menu_renderer = DNMenuRenderer()
        
        # Context memory
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        
        # Caches
        self._dashboard_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._answer_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=1024, ttl=300)
        
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=4)
        
        logger.info(f"✅ DNAnalysisService initialized (v{self._version})")
        logger.info(f"   Menu System: ✅ (19 options)")
        logger.info(f"   Source of Truth: PostgreSQL")
        logger.info(f"   ALL DN Questions go through this file")
    
    @staticmethod
    def _session() -> Session:
        return SessionLocal()
    
    def get_main_menu(self) -> str:
        """Get the main DN menu"""
        return self._menu_renderer.render_main_menu()
    
    # ============================================================
    # MAIN PROCESSING - ALL DN QUERIES ENTER HERE
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default", **kwargs: Any) -> str:
        """
        Main entry point for ALL DN-related WhatsApp queries.
        ALWAYS returns a string - never a dict.
        
        This handles:
        - Menu navigation (1-19, 99, 0)
        - Natural language DN queries
        - DN number auto-detection
        - All DN analytics
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Service processing: '{message_clean}' from {sender}")
        
        # Check if it's a menu navigation command
        if message_clean.lower() in ["menu", "help", "options"]:
            return self.get_main_menu()
        
        # Process as menu input
        result = self.process_menu_input(sender, message_clean)
        
        # Extract response string
        response = result.get("response", self.get_main_menu())
        
        # If exit_menu is True, user wants to go back to main menu
        if result.get("exit_menu", False):
            return response
        
        return response
    
    def process_menu_input(self, session_id: str, user_input: str) -> Dict[str, Any]:
        """
        Process menu input and return response.
        ALL DN menu options (1-19) and natural language queries.
        
        Returns:
            {
                "response": str,           # WhatsApp message
                "menu_type": str,          # "dn_menu"
                "action": str,             # Action performed
                "data": dict,              # Additional data
                "exit_menu": bool          # True if should return to main menu
            }
        """
        context = self._get_context(session_id)
        user_input = user_input.strip()
        
        # ============================================================
        # STEP 1: Check for DN number (auto-detect)
        # ============================================================
        dns = _extract_dn_numbers(user_input)
        if dns and len(dns) == 1:
            # Single DN number - show dashboard
            context.current_dn = dns[0]
            result = self._get_dn_dashboard(context, dns[0])
            result["exit_menu"] = False  # Stay in DN service
            return result
        
        if dns and len(dns) >= 2:
            # Multiple DN numbers - show comparison
            context.comparison_dns = dns[:2]
            result = self._perform_comparison(context, dns[0], dns[1])
            result["exit_menu"] = False  # Stay in DN service
            return result
        
        # ============================================================
        # STEP 2: Check for natural language queries
        # ============================================================
        # Detect intent
        intent, confidence = self._intent_engine.detect_intent(user_input)
        
        # Extract entities
        entities = self._entity_engine.extract_entities(user_input)
        
        # Handle based on intent
        if intent == IntentType.DASHBOARD and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_dn_dashboard(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.STATUS and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_dn_status(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.HISTORY and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_dn_history(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.TIMELINE and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_dn_timeline(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.TRANSIT and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_transit_analysis(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.COMPARISON and entities.get("dn_numbers") and len(entities["dn_numbers"]) >= 2:
            result = self._perform_comparison(context, entities["dn_numbers"][0], entities["dn_numbers"][1])
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.SEARCH:
            query = entities.get("search_query") or user_input
            result = self._search_dns(context, query)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.PENDING:
            result = self._get_pending_dns(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.PGI:
            result = self._get_pending_pgi(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.POD:
            result = self._get_pending_pod(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.DELAYED:
            result = self._get_delayed_dns(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.RECENT:
            result = self._get_recent_dns(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.RANK:
            result = self._get_ranking(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.INSIGHTS:
            result = self._get_insights(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.SLA:
            result = self._get_sla_report(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.AGING:
            result = self._get_aging_report(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.TREND:
            result = self._get_trends(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.FORECAST:
            result = self._get_forecast(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.RECOMMENDATIONS:
            result = self._get_recommendations(context)
            result["exit_menu"] = False
            return result
        
        if intent == IntentType.ROOT_CAUSE and entities.get("dn_numbers"):
            context.current_dn = entities["dn_numbers"][0]
            result = self._get_root_cause(context, entities["dn_numbers"][0])
            result["exit_menu"] = False
            return result
        
        # ============================================================
        # STEP 3: Handle menu navigation (0, 99, 1-19)
        # ============================================================
        
        # Handle main menu navigation
        if user_input == "0":
            return self._handle_main_menu_return(context)
        elif user_input == "99":
            return self._handle_main_menu_return(context)
        
        # Handle menu options based on state
        if context.menu_state == MenuState.MAIN:
            return self._handle_main_menu_option(context, user_input)
        elif context.menu_state == MenuState.DN_SELECTION:
            return self._handle_dn_selection(context, user_input)
        elif context.menu_state == MenuState.COMPARISON_SELECTION:
            return self._handle_comparison_selection(context, user_input)
        
        # ============================================================
        # STEP 4: Unknown query - show help
        # ============================================================
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *DN Service Commands:*",
                "• Type a DN number (8-12 digits) for dashboard",
                "• 'Status [DN]' - Show DN status",
                "• 'History [DN]' - Show DN history",
                "• 'Compare [DN1] [DN2]' - Compare DNs",
                "• 'Search [keyword]' - Search DNs",
                "• 'Pending' - Show pending DNs",
                "• 'Delayed' - Show delayed DNs",
                "• 'Recent' - Show recent DNs",
                "• 'Ranking' - Show DN rankings",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dn_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False  # Stay in DN service
        }
    
    # ============================================================
    # MENU HANDLING METHODS
    # ============================================================
    
    def _handle_main_menu_return(self, context: DNContext) -> Dict[str, Any]:
        """Return to main menu"""
        context.menu_state = MenuState.MAIN
        context.selected_option = None
        context.comparison_dns = []
        context.awaiting_dn = False
        context.awaiting_comparison = False
        
        return {
            "response": self._menu_renderer.render_main_menu(),
            "menu_type": "dn_menu",
            "action": "main_menu",
            "data": {},
            "exit_menu": True  # Exit to main AI Logistics menu
        }
    
    def _handle_main_menu_option(self, context: DNContext, option: str) -> Dict[str, Any]:
        """Handle main menu option selection"""
        
        option_map = {
            "1": ("dashboard", "Enter DN number for dashboard:"),
            "2": ("status", "Enter DN number for status:"),
            "3": ("history", "Enter DN number for history:"),
            "4": ("timeline", "Enter DN number for timeline:"),
            "5": ("transit", "Enter DN number for transit analysis:"),
            "6": ("pending", None),
            "7": ("pgi", None),
            "8": ("pod", None),
            "9": ("delayed", None),
            "10": ("recent", None),
            "11": ("search", None),
            "12": ("comparison", None),
            "13": ("ranking", None),
            "14": ("insights", None),
            "15": ("sla", None),
            "16": ("aging", None),
            "17": ("trends", None),
            "18": ("forecast", None),
            "19": ("recommendations", None),
        }
        
        # Direct actions (no DN needed)
        if option in ["6", "7", "8", "9", "10", "13", "14", "15", "16", "17", "18", "19"]:
            return self._handle_direct_action(context, option)
        
        if option not in option_map:
            return self._handle_quick_query(context, option)
        
        action, prompt = option_map[option]
        
        # Check if we already have a selected DN
        if context.current_dn:
            result = self._execute_dn_action(context, action, context.current_dn)
            result["exit_menu"] = False
            return result
        
        # Ask for DN
        context.menu_state = MenuState.DN_SELECTION
        context.selected_option = action
        context.awaiting_dn = True
        
        return {
            "response": self._menu_renderer.render_dn_selection(prompt),
            "menu_type": "dn_menu",
            "action": "dn_selection",
            "data": {"purpose": action},
            "exit_menu": False  # Stay in DN service
        }
    
    def _handle_direct_action(self, context: DNContext, option: str) -> Dict[str, Any]:
        """Handle direct actions that don't need DN selection"""
        action_map = {
            "6": self._get_pending_dns,
            "7": self._get_pending_pgi,
            "8": self._get_pending_pod,
            "9": self._get_delayed_dns,
            "10": self._get_recent_dns,
            "13": self._get_ranking,
            "14": self._get_insights,
            "15": self._get_sla_report,
            "16": self._get_aging_report,
            "17": self._get_trends,
            "18": self._get_forecast,
            "19": self._get_recommendations,
        }
        
        if option in action_map:
            result = action_map[option](context)
            result["exit_menu"] = False
            return result
        
        return self._handle_quick_query(context, option)
    
    def _handle_dn_selection(self, context: DNContext, dn_input: str) -> Dict[str, Any]:
        """Handle DN selection response"""
        if not _is_valid_dn(dn_input):
            return {
                "response": "\n".join([
                    "❌ Invalid DN number.",
                    "",
                    "Please enter a valid 8-12 digit DN number.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dn_menu",
                "action": "dn_selection_error",
                "data": {},
                "exit_menu": False
            }
        
        context.current_dn = dn_input
        context.menu_state = MenuState.MAIN
        context.awaiting_dn = False
        
        action = context.selected_option or "dashboard"
        result = self._execute_dn_action(context, action, dn_input)
        result["exit_menu"] = False
        return result
    
    def _handle_comparison_selection(self, context: DNContext, dn_input: str) -> Dict[str, Any]:
        """Handle comparison DN selection"""
        if not _is_valid_dn(dn_input):
            return {
                "response": "\n".join([
                    "❌ Invalid DN number.",
                    "",
                    "Please enter a valid 8-12 digit DN number.",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dn_menu",
                "action": "comparison_error",
                "data": {},
                "exit_menu": False
            }
        
        context.comparison_dns.append(dn_input)
        
        if len(context.comparison_dns) == 1:
            return {
                "response": "\n".join([
                    f"✅ First DN selected: {dn_input}",
                    "",
                    "Enter second DN number:",
                    "",
                    "0. Main Menu",
                    "99. Back"
                ]),
                "menu_type": "dn_menu",
                "action": "comparison_second",
                "data": {"first_dn": dn_input},
                "exit_menu": False
            }
        else:
            dn1, dn2 = context.comparison_dns[0], context.comparison_dns[1]
            context.menu_state = MenuState.MAIN
            context.comparison_dns = []
            return self._perform_comparison(context, dn1, dn2)
    
    def _handle_quick_query(self, context: DNContext, query: str) -> Dict[str, Any]:
        """Handle quick query from main menu"""
        # Check if it's a comparison
        if "compare" in query.lower() or "vs" in query.lower():
            dns = re.findall(r'\b\d{8,12}\b', query)
            if len(dns) >= 2:
                return self._perform_comparison(context, dns[0], dns[1])
        
        # Check if it's a valid DN number
        if _is_valid_dn(query):
            context.current_dn = query
            return self._get_dn_dashboard(context, query)
        
        # Check if it's a search query
        if len(query) >= 3:
            return self._search_dns(context, query)
        
        # Default response
        return {
            "response": "\n".join([
                "❌ I didn't understand that.",
                "",
                "💡 *Try one of these:*",
                "• '1234567890' - Show DN dashboard",
                "• 'Status 1234567890' - Show DN status",
                "• 'History 1234567890' - Show DN history",
                "• 'Compare 1234567890 0987654321' - Compare DNs",
                "• 'Search [keyword]' - Search DNs",
                "",
                "0. Main Menu",
                "99. Back"
            ]),
            "menu_type": "dn_menu",
            "action": "unknown_query",
            "data": {},
            "exit_menu": False
        }
    
    def _execute_dn_action(self, context: DNContext, action: str, dn_no: str) -> Dict[str, Any]:
        """Execute DN action based on selected option"""
        action_map = {
            "dashboard": self._get_dn_dashboard,
            "status": self._get_dn_status,
            "history": self._get_dn_history,
            "timeline": self._get_dn_timeline,
            "transit": self._get_transit_analysis,
        }
        
        handler = action_map.get(action, self._get_dn_dashboard)
        return handler(context, dn_no)
    
    def _get_context(self, session_id: str) -> DNContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DNContext()
            return self._contexts[session_id]
    
    # ============================================================
    # DN OPERATIONS - ALL DATA FROM POSTGRESQL
    # ============================================================
    
    def _get_dn_dashboard(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get DN dashboard"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\nPlease check the DN number and try again.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "dashboard",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                response = self._menu_renderer.render_dn_dashboard(dn_no, dashboard)
                
                # Add insights if available
                insights = dashboard.get('insights', [])
                if insights:
                    response += "\n\n💡 *Insights*\n" + "\n".join(f"• {i}" for i in insights[:3])
                
                recommendations = dashboard.get('recommendations', [])
                if recommendations:
                    response += "\n\n🎯 *Recommendations*\n" + "\n".join(f"• {r}" for r in recommendations[:2])
                
                return {
                    "response": response,
                    "menu_type": "dn_menu",
                    "action": "dashboard",
                    "data": {"dn": dn_no, "dashboard": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return {
                "response": f"⚠️ Service error for DN {dn_no}: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dn_status(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get DN status"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "status_error",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_dn_status(dn_no, dashboard),
                    "menu_type": "dn_menu",
                    "action": "status",
                    "data": {"dn": dn_no, "status": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dn_history(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get DN history"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "history_error",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                events = []
                if dashboard.get("dn_create_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("dn_create_date")),
                        "status": "Created",
                        "description": f"DN {dn_no} created for {dashboard.get('customer_name', 'N/A')}"
                    })
                
                if dashboard.get("good_issue_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("good_issue_date")),
                        "status": "PGI Created",
                        "description": "Goods Issue created at warehouse"
                    })
                
                if dashboard.get("pod_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("pod_date")),
                        "status": "Delivered",
                        "description": "Proof of Delivery received"
                    })
                
                return {
                    "response": self._menu_renderer.render_dn_history(dn_no, events),
                    "menu_type": "dn_menu",
                    "action": "history",
                    "data": {"dn": dn_no, "events": events},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_dn_timeline(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get DN timeline"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "timeline_error",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                events = []
                if dashboard.get("dn_create_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("dn_create_date")),
                        "status": "Created",
                        "description": f"DN {dn_no} created"
                    })
                
                if dashboard.get("good_issue_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("good_issue_date")),
                        "status": "PGI Created",
                        "description": "Goods issued from warehouse"
                    })
                
                if dashboard.get("pod_date"):
                    events.append({
                        "timestamp": _format_date(dashboard.get("pod_date")),
                        "status": "Delivered",
                        "description": "Delivery completed"
                    })
                
                return {
                    "response": self._menu_renderer.render_dn_timeline(dn_no, events),
                    "menu_type": "dn_menu",
                    "action": "timeline",
                    "data": {"dn": dn_no, "events": events},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_transit_analysis(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get transit analysis for DN"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "transit_error",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                return {
                    "response": self._menu_renderer.render_transit_analysis(dn_no, dashboard),
                    "menu_type": "dn_menu",
                    "action": "transit",
                    "data": {"dn": dn_no, "transit": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_pending_dns(self, context: DNContext) -> Dict[str, Any]:
        """Get pending DNs"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                ).filter(
                    or_(
                        DeliveryReport.pending_flag.is_(True),
                        DeliveryReport.pod_date.is_(None)
                    )
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "dn_create_date": row.dn_create_date,
                        "computed_delivery_status": "Pending",
                    })
                
                return {
                    "response": self._menu_renderer.render_pending_list("📋 Pending DNs", dns),
                    "menu_type": "dn_menu",
                    "action": "pending",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_pending_pgi(self, context: DNContext) -> Dict[str, Any]:
        """Get pending PGI"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                ).filter(
                    DeliveryReport.good_issue_date.is_(None)
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "dn_create_date": row.dn_create_date,
                        "computed_delivery_status": "Pending PGI",
                    })
                
                return {
                    "response": self._menu_renderer.render_pending_list("⏳ Pending PGI", dns),
                    "menu_type": "dn_menu",
                    "action": "pgi",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_pending_pod(self, context: DNContext) -> Dict[str, Any]:
        """Get pending POD"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                    DeliveryReport.good_issue_date,
                ).filter(
                    DeliveryReport.good_issue_date.isnot(None),
                    DeliveryReport.pod_date.is_(None)
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "dn_create_date": row.dn_create_date,
                        "computed_delivery_status": "Pending POD",
                    })
                
                return {
                    "response": self._menu_renderer.render_pending_list("📋 Pending POD", dns),
                    "menu_type": "dn_menu",
                    "action": "pod",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_delayed_dns(self, context: DNContext) -> Dict[str, Any]:
        """Get delayed DNs"""
        try:
            threshold = datetime.now().date() - timedelta(days=DN_DELAY_THRESHOLD_DAYS)
            
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                    DeliveryReport.good_issue_date,
                    DeliveryReport.pod_date,
                ).filter(
                    DeliveryReport.good_issue_date.isnot(None),
                    DeliveryReport.good_issue_date < threshold,
                    DeliveryReport.pod_date.is_(None)
                ).order_by(
                    DeliveryReport.good_issue_date.asc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "dn_create_date": row.dn_create_date,
                        "computed_delivery_status": "Delayed",
                    })
                
                return {
                    "response": self._menu_renderer.render_pending_list(f"⚠️ Delayed DNs (>{DN_DELAY_THRESHOLD_DAYS} days)", dns),
                    "menu_type": "dn_menu",
                    "action": "delayed",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_recent_dns(self, context: DNContext) -> Dict[str, Any]:
        """Get recent DNs"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "dn_create_date": row.dn_create_date,
                        "computed_delivery_status": "Recent",
                    })
                
                return {
                    "response": self._menu_renderer.render_pending_list("🔄 Recent DNs", dns),
                    "menu_type": "dn_menu",
                    "action": "recent",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _search_dns(self, context: DNContext, query: str) -> Dict[str, Any]:
        """Search DNs"""
        try:
            with self._session() as session:
                search_pattern = f"%{query}%"
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.warehouse,
                    DeliveryReport.dn_create_date,
                ).filter(
                    or_(
                        DeliveryReport.dn_no.ilike(search_pattern),
                        DeliveryReport.customer_name.ilike(search_pattern),
                        DeliveryReport.warehouse.ilike(search_pattern),
                        DeliveryReport.sales_office.ilike(search_pattern),
                    )
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(20).all()
                
                dns = []
                for row in results:
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "warehouse": _text(row.warehouse),
                        "dn_create_date": row.dn_create_date,
                    })
                
                if not dns:
                    return {
                        "response": f"🔍 No results found for '{query}'\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "search",
                        "data": {"query": query, "dns": []},
                        "exit_menu": False
                    }
                
                context.search_results = dns
                
                return {
                    "response": self._menu_renderer.render_pending_list(f"🔍 Search Results for '{query}'", dns),
                    "menu_type": "dn_menu",
                    "action": "search",
                    "data": {"query": query, "dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _perform_comparison(self, context: DNContext, dn1: str, dn2: str) -> Dict[str, Any]:
        """Perform DN comparison"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dash1 = builder.build(dn1)
                dash2 = builder.build(dn2)
                
                if not dash1 or not dash2:
                    return {
                        "response": "⚠️ One or both DNs not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "comparison_error",
                        "data": {"error": "not_found"},
                        "exit_menu": False
                    }
                
                metrics = {}
                
                metrics[f"{dn1}_metrics"] = {
                    "Customer": dash1.get('customer_name', 'N/A'),
                    "Status": dash1.get('computed_delivery_status', 'N/A'),
                    "Units": f"{dash1.get('total_units', 0):,}",
                    "Revenue": f"PKR {float(dash1.get('total_revenue', 0)):,.2f}",
                    "Warehouse": dash1.get('warehouse', 'N/A'),
                    "Age": f"{dash1.get('dn_age', 0)} Days",
                    "Transit": f"{dash1.get('transit_days', 'N/A')} Days",
                    "Distance": f"{dash1.get('distance_km', 'N/A')} KM",
                }
                
                metrics[f"{dn2}_metrics"] = {
                    "Customer": dash2.get('customer_name', 'N/A'),
                    "Status": dash2.get('computed_delivery_status', 'N/A'),
                    "Units": f"{dash2.get('total_units', 0):,}",
                    "Revenue": f"PKR {float(dash2.get('total_revenue', 0)):,.2f}",
                    "Warehouse": dash2.get('warehouse', 'N/A'),
                    "Age": f"{dash2.get('dn_age', 0)} Days",
                    "Transit": f"{dash2.get('transit_days', 'N/A')} Days",
                    "Distance": f"{dash2.get('distance_km', 'N/A')} KM",
                }
                
                revenue1 = float(dash1.get('total_revenue', 0))
                revenue2 = float(dash2.get('total_revenue', 0))
                
                if revenue1 > revenue2:
                    explanation = f"DN {dn1} has higher revenue than DN {dn2}"
                elif revenue2 > revenue1:
                    explanation = f"DN {dn2} has higher revenue than DN {dn1}"
                else:
                    explanation = f"DN {dn1} and DN {dn2} have similar revenue"
                
                metrics["explanation"] = explanation
                
                return {
                    "response": self._menu_renderer.render_comparison_result(dn1, dn2, metrics),
                    "menu_type": "dn_menu",
                    "action": "comparison",
                    "data": {"dn1": dn1, "dn2": dn2, "metrics": metrics},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Comparison error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_ranking(self, context: DNContext) -> Dict[str, Any]:
        """Get DN rankings"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                    func.sum(DeliveryReport.dn_qty).label("units"),
                ).group_by(
                    DeliveryReport.dn_no
                ).order_by(
                    func.sum(DeliveryReport.dn_amount).desc()
                ).limit(10).all()
                
                ranking = []
                for row in results:
                    ranking.append({
                        "dn_no": _text(row.dn_no),
                        "value": f"PKR {float(row.revenue or 0):,.2f}",
                        "units": int(row.units or 0),
                    })
                
                return {
                    "response": self._menu_renderer.render_ranking(ranking, "Revenue", 10),
                    "menu_type": "dn_menu",
                    "action": "ranking",
                    "data": {"ranking": ranking},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Ranking error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_insights(self, context: DNContext) -> Dict[str, Any]:
        """Get DN insights"""
        try:
            with self._session() as session:
                stats = session.query(
                    func.count(DeliveryReport.dn_no).label("total"),
                    func.count(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no))).label("delivered"),
                    func.count(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no))).label("pending"),
                    func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_delivery_days"),
                    func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                    func.avg(DeliveryReport.dn_qty).label("avg_units"),
                ).first()
                
                insights = []
                total = int(stats.total or 0)
                delivered = int(stats.delivered or 0)
                pending = int(stats.pending or 0)
                avg_days = float(stats.avg_delivery_days or 0)
                total_revenue = float(stats.total_revenue or 0)
                avg_units = float(stats.avg_units or 0)
                
                if total > 0:
                    insights.append(f"📊 Total DNs: {total:,}")
                    insights.append(f"✅ Delivered: {delivered:,} ({_percent(delivered, total):.1f}%)")
                    insights.append(f"⏳ Pending: {pending:,} ({_percent(pending, total):.1f}%)")
                    
                    if avg_days > 0:
                        insights.append(f"📅 Average Delivery: {avg_days:.1f} Days")
                    
                    if total_revenue > 0:
                        insights.append(f"💰 Total Revenue: PKR {total_revenue:,.2f}")
                    
                    if avg_units > 0:
                        insights.append(f"📦 Average Units: {avg_units:.1f}")
                
                recommendations = []
                if pending > 10:
                    recommendations.append(f"🚨 High pending DNs: {pending}. Focus on resolution.")
                if avg_days > SLA_TARGET_DAYS:
                    recommendations.append(f"⏱️ Delivery time ({avg_days:.1f} days) exceeds SLA ({SLA_TARGET_DAYS} days).")
                
                return {
                    "response": self._menu_renderer.render_insights(insights, recommendations),
                    "menu_type": "dn_menu",
                    "action": "insights",
                    "data": {"insights": insights, "recommendations": recommendations},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_sla_report(self, context: DNContext) -> Dict[str, Any]:
        """Get SLA compliance report"""
        try:
            with self._session() as session:
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.customer_name,
                    DeliveryReport.dn_create_date,
                    DeliveryReport.pod_date,
                ).order_by(
                    DeliveryReport.dn_create_date.desc()
                ).limit(50).all()
                
                dns = []
                for row in results:
                    dn_date = row.dn_create_date
                    pod_date = row.pod_date
                    status = "Delivered" if pod_date else "Pending"
                    days = (pod_date - dn_date).days if pod_date and dn_date else None
                    
                    dns.append({
                        "dn_no": _text(row.dn_no),
                        "customer_name": _text(row.customer_name),
                        "computed_delivery_status": status,
                        "delivery_days": days,
                        "sla_compliant": days is not None and days <= SLA_TARGET_DAYS if days is not None else False,
                    })
                
                return {
                    "response": self._menu_renderer.render_sla_report(dns),
                    "menu_type": "dn_menu",
                    "action": "sla",
                    "data": {"dns": dns},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_aging_report(self, context: DNContext) -> Dict[str, Any]:
        """Get aging report"""
        try:
            with self._session() as session:
                today = date.today()
                results = session.query(
                    DeliveryReport.dn_no,
                    DeliveryReport.dn_create_date,
                    DeliveryReport.pod_date,
                ).filter(
                    DeliveryReport.pod_date.is_(None)
                ).all()
                
                age_groups = {
                    "0-3 Days": 0,
                    "4-7 Days": 0,
                    "8-15 Days": 0,
                    "16-30 Days": 0,
                    "30+ Days": 0,
                }
                
                ages = []
                for row in results:
                    if row.dn_create_date:
                        age = (today - row.dn_create_date).days
                        ages.append(age)
                        
                        if age <= 3:
                            age_groups["0-3 Days"] += 1
                        elif age <= 7:
                            age_groups["4-7 Days"] += 1
                        elif age <= 15:
                            age_groups["8-15 Days"] += 1
                        elif age <= 30:
                            age_groups["16-30 Days"] += 1
                        else:
                            age_groups["30+ Days"] += 1
                
                data = {
                    "age_groups": age_groups,
                    "total": len(ages),
                    "average_age": sum(ages) / len(ages) if ages else 0,
                    "max_age": max(ages) if ages else 0,
                    "min_age": min(ages) if ages else 0,
                }
                
                return {
                    "response": self._menu_renderer.render_aging_report(data),
                    "menu_type": "dn_menu",
                    "action": "aging",
                    "data": {"aging": data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_trends(self, context: DNContext) -> Dict[str, Any]:
        """Get DN trends"""
        try:
            with self._session() as session:
                daily = session.query(
                    func.date(DeliveryReport.dn_create_date).label("date"),
                    func.count(DeliveryReport.dn_no).label("count"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                ).filter(
                    DeliveryReport.dn_create_date >= date.today() - timedelta(days=7)
                ).group_by(
                    func.date(DeliveryReport.dn_create_date)
                ).order_by(
                    func.date(DeliveryReport.dn_create_date).desc()
                ).all()
                
                weekly = session.query(
                    func.extract('week', DeliveryReport.dn_create_date).label("week"),
                    func.count(DeliveryReport.dn_no).label("count"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                ).filter(
                    DeliveryReport.dn_create_date >= date.today() - timedelta(days=30)
                ).group_by(
                    func.extract('week', DeliveryReport.dn_create_date)
                ).order_by(
                    func.extract('week', DeliveryReport.dn_create_date).desc()
                ).limit(4).all()
                
                if len(daily) >= 2:
                    current = daily[0].count if daily[0].count else 0
                    previous = daily[1].count if daily[1].count else 0
                    growth = _growth(current, previous)
                else:
                    growth = 0
                
                trend_data = {
                    "daily": [{"date": _format_date(d.date), "count": d.count or 0, "revenue": float(d.revenue or 0)} for d in daily],
                    "weekly": [{"week": f"Week {int(w.week)}", "count": w.count or 0, "revenue": float(w.revenue or 0)} for w in weekly],
                    "growth": growth,
                }
                
                return {
                    "response": self._menu_renderer.render_trends(trend_data),
                    "menu_type": "dn_menu",
                    "action": "trends",
                    "data": {"trends": trend_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_forecast(self, context: DNContext) -> Dict[str, Any]:
        """Get DN forecast"""
        try:
            with self._session() as session:
                results = session.query(
                    func.date(DeliveryReport.dn_create_date).label("date"),
                    func.count(DeliveryReport.dn_no).label("count"),
                    func.sum(DeliveryReport.dn_amount).label("revenue"),
                    func.sum(DeliveryReport.dn_qty).label("units"),
                ).filter(
                    DeliveryReport.dn_create_date >= date.today() - timedelta(days=30)
                ).group_by(
                    func.date(DeliveryReport.dn_create_date)
                ).order_by(
                    func.date(DeliveryReport.dn_create_date).asc()
                ).all()
                
                if len(results) < 7:
                    return {
                        "response": "🔮 Insufficient data for forecast. Need at least 7 days of data.",
                        "menu_type": "dn_menu",
                        "action": "forecast",
                        "data": {},
                        "exit_menu": False
                    }
                
                avg_count = sum(r.count or 0 for r in results[-7:]) / 7
                avg_revenue = sum(float(r.revenue or 0) for r in results[-7:]) / 7
                avg_units = sum(r.units or 0 for r in results[-7:]) / 7
                
                forecast_data = {
                    "expected_count": int(avg_count * 1.1),
                    "expected_revenue": avg_revenue * 1.1,
                    "expected_units": int(avg_units * 1.1),
                    "lower_bound": int(avg_count * 0.9),
                    "upper_bound": int(avg_count * 1.3),
                    "confidence": 0.85,
                }
                
                return {
                    "response": self._menu_renderer.render_forecast(forecast_data),
                    "menu_type": "dn_menu",
                    "action": "forecast",
                    "data": {"forecast": forecast_data},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_recommendations(self, context: DNContext) -> Dict[str, Any]:
        """Get DN recommendations"""
        try:
            with self._session() as session:
                pending_count = session.query(
                    func.count(DeliveryReport.dn_no)
                ).filter(
                    or_(
                        DeliveryReport.pending_flag.is_(True),
                        DeliveryReport.pod_date.is_(None)
                    )
                ).scalar() or 0
                
                threshold = datetime.now().date() - timedelta(days=DN_DELAY_THRESHOLD_DAYS)
                delayed_count = session.query(
                    func.count(DeliveryReport.dn_no)
                ).filter(
                    DeliveryReport.good_issue_date.isnot(None),
                    DeliveryReport.good_issue_date < threshold,
                    DeliveryReport.pod_date.is_(None)
                ).scalar() or 0
                
                recommendations = []
                
                if pending_count > 10:
                    recommendations.append(f"🚨 Action Required: {pending_count} pending DNs need resolution")
                elif pending_count > 5:
                    recommendations.append(f"📋 Review {pending_count} pending DNs for timely closure")
                
                if delayed_count > 5:
                    recommendations.append(f"⏰ Priority: {delayed_count} DNs are delayed beyond {DN_DELAY_THRESHOLD_DAYS} days")
                
                if not recommendations:
                    recommendations.append("✅ Current DN performance is good. Continue monitoring.")
                    recommendations.append("📊 Consider periodic DN audits")
                
                return {
                    "response": self._menu_renderer.render_insights([], recommendations),
                    "menu_type": "dn_menu",
                    "action": "recommendations",
                    "data": {"recommendations": recommendations},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    def _get_root_cause(self, context: DNContext, dn_no: str) -> Dict[str, Any]:
        """Get root cause analysis for a DN"""
        try:
            with self._session() as session:
                builder = DNDashboardBuilder(session)
                dashboard = builder.build(dn_no)
                
                if not dashboard:
                    return {
                        "response": f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu",
                        "menu_type": "dn_menu",
                        "action": "root_cause_error",
                        "data": {"dn": dn_no, "error": "not_found"},
                        "exit_menu": False
                    }
                
                status = dashboard.get('computed_delivery_status', '')
                lines = [
                    f"🔍 *Root Cause Analysis - DN {dn_no}*",
                    "",
                    f"Current Status: {status}",
                    "",
                ]
                
                if status == "Pending PGI":
                    lines.extend([
                        "📋 *Analysis:*",
                        "• PGI is pending at warehouse",
                        "• Possible causes:",
                        "  - Warehouse capacity issue",
                        "  - Inventory availability",
                        "  - Documentation pending",
                        "",
                        "🎯 *Recommendations:*",
                        "• Contact warehouse for status",
                        "• Expedite PGI processing",
                        "• Check inventory availability",
                    ])
                elif status == "Pending POD":
                    lines.extend([
                        "📋 *Analysis:*",
                        "• POD is pending from customer",
                        "• Possible causes:",
                        "  - Customer not available",
                        "  - Delivery confirmation pending",
                        "  - POD document missing",
                        "",
                        "🎯 *Recommendations:*",
                        "• Follow up with customer",
                        "• Send POD reminder",
                        "• Escalate to sales team",
                    ])
                elif status == "Delayed":
                    lines.extend([
                        "📋 *Analysis:*",
                        f"• DN is delayed by {dashboard.get('dn_age', 0)} days",
                        "• Possible causes:",
                        "  - Transit delay",
                        "  - Weather conditions",
                        "  - Route congestion",
                        "",
                        "🎯 *Recommendations:*",
                        "• Expedite delivery",
                        "• Consider alternate route",
                        "• Communicate with customer",
                    ])
                elif status == "Delivered" or status == "Completed":
                    lines.extend([
                        "✅ *Analysis:*",
                        "• DN is successfully delivered",
                        "• No issues identified",
                        "",
                        "🎯 *Recommendations:*",
                        "• Close the DN",
                        "• Update records",
                        "• Process payment",
                    ])
                else:
                    lines.extend([
                        "📋 *Analysis:*",
                        "• Status: In Transit",
                        "• DN is on track",
                        "",
                        "🎯 *Recommendations:*",
                        "• Continue monitoring",
                        "• Track delivery progress",
                    ])
                
                lines.extend([
                    "",
                    "0. Main Menu",
                    "99. Back"
                ])
                
                return {
                    "response": "\n".join(lines),
                    "menu_type": "dn_menu",
                    "action": "root_cause",
                    "data": {"dn": dn_no, "root_cause": dashboard},
                    "exit_menu": False
                }
        except Exception as e:
            return {
                "response": f"⚠️ Error: {str(e)[:100]}\n\n0. Main Menu",
                "menu_type": "dn_menu",
                "action": "error",
                "data": {"error": str(e)},
                "exit_menu": False
            }
    
    # ============================================================
    # LEGACY METHODS - BACKWARD COMPATIBILITY
    # ============================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = DNContext()
        result = self._get_dn_dashboard(context, dn_no)
        return {
            "success": True,
            "data": result.get("data", {}).get("dashboard", {}),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_pending_dns(self, limit: int = 20) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = DNContext()
        result = self._get_pending_dns(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("dns", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def get_top_performers(self, limit: int = 10) -> Dict[str, Any]:
        """Legacy method for backward compatibility"""
        context = DNContext()
        result = self._get_ranking(context)
        return {
            "success": True,
            "data": result.get("data", {}).get("ranking", []),
            "whatsapp_message": result.get("response", ""),
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
            
            return {
                "healthy": True,
                "service": self._service_name,
                "version": self._version,
                "database": "connected",
                "records": int(rows),
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

# ============================================================
# BLOCK 13: SERVICE SINGLETON
# ============================================================

_service: Optional[DNAnalysisService] = None
_service_lock = threading.Lock()

def get_dn_analysis_service() -> DNAnalysisService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalysisService()
    return _service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process DN menu input for WhatsApp integration"""
    service = get_dn_analysis_service()
    return service.process_menu_input(session_id, user_input)

def get_dn_main_menu() -> str:
    """Get the main DN menu for WhatsApp"""
    service = get_dn_analysis_service()
    return service.get_main_menu()

# ============================================================
# BLOCK 14: EXPORTS
# ============================================================

__all__ = [
    "DNAnalysisService",
    "DNContext",
    "IntentType",
    "MenuState",
    "ResponseFormat",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
    "DNMenuRenderer",
    "get_dn_dashboard",
    "get_dn_status",
    "get_dn_history",
    "get_pending_dns",
    "get_top_performers",
    "health_check",
]
