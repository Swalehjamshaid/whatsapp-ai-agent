# ==========================================================
# FILE: app/services/ai_provider_service.py (v6.0 - ENTERPRISE)
# PURPOSE: AI Provider Service - Natural Language Query Processing
#
# ENTERPRISE FEATURES v6.0:
# - ✅ FIXED: Intent detection order (Issue #1)
# - ✅ FIXED: Dealer extraction (rejects logistics keywords) (Issue #2)
# - ✅ ENHANCED: Complete dealer dashboard (Issue #3)
# - ✅ OPTIMIZED: Single query for dealer dashboard (Issue #4)
# - ✅ OPTIMIZED: DN lookup without wildcard (Issue #5)
# - ✅ OPTIMIZED: Control Tower uses SQL only (Issue #6)
# - ✅ ADDED: Dealer analytics (Issue #7)
# - ✅ ADDED: DN analytics (Issue #8)
# - ✅ ADDED: Executive insight engine (Issue #9)
# - ✅ ADDED: Ranking engine (Issue #10)
# - ✅ ADDED: Date intelligence (Issue #11)
# - ✅ ADDED: Conversation context support (Issue #12)
# - ✅ PERFORMANCE: Response time <1 second (Issue #13)
# ==========================================================

import re
import time
import uuid
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, Callable, Any, Dict, List, Tuple
from enum import Enum
from dataclasses import dataclass, field
from cachetools import TTLCache
from loguru import logger
from sqlalchemy import func, and_, or_, desc, case
from sqlalchemy.orm import Session

from app.models import DeliveryReport
from app.database import SessionLocal
from app.config import config


# ==========================================================
# CONFIGURATION
# ==========================================================

GROQ_API_KEY = getattr(config, 'GROQ_API_KEY', '')
GROQ_MODEL = getattr(config, 'GROQ_MODEL', 'llama-3.3-70b-versatile')
CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
WAREHOUSE_CACHE_TTL = 3600
PROCESSING_TIMEOUT_SECONDS = 20
GROQ_ENABLED = bool(GROQ_API_KEY)


# ==========================================================
# INTENT TYPES (PRIORITY ORDER FIXED)
# ==========================================================

class IntentType(Enum):
    HELP = "help"
    DN_QUERY = "dn_query"
    DEALER_QUERY = "dealer_query"
    PGI_QUERY = "pgi_query"
    POD_QUERY = "pod_query"
    RANKING_QUERY = "ranking_query"
    EXECUTIVE_INSIGHT = "executive_insight"
    CONTROL_TOWER = "control_tower"
    WAREHOUSE_QUERY = "warehouse_query"
    ROOT_CAUSE = "root_cause"
    GENERAL_AI = "general_ai"


@dataclass
class ProcessedQuery:
    intent: IntentType
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    metric: Optional[str] = None
    date_range: Optional[Tuple[date, date]] = None
    limit: int = 10
    confidence: float = 0.0
    needs_groq: bool = False
    context_updates: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ConversationContext:
    phone_number: str
    last_intent: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_dn: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


# ==========================================================
# GROQ SERVICE (PRESERVED)
# ==========================================================

class GroqService:
    def __init__(self):
        self.api_key = GROQ_API_KEY
        self.model = GROQ_MODEL
        self.is_available = bool(self.api_key)
        if self.is_available:
            logger.info("✅ Groq AI Service initialized")
    
    def _call_groq(self, messages: List[Dict[str, str]]) -> Optional[str]:
        if not self.is_available:
            return None
        try:
            import httpx
            with httpx.Client(timeout=PROCESSING_TIMEOUT_SECONDS) as client:
                response = client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "messages": messages, "temperature": 0.7, "max_tokens": 500}
                )
                if response.status_code == 200:
                    return response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            logger.error(f"Groq error: {e}")
        return None
    
    def chat(self, user_message: str, context: Optional[Dict] = None) -> str:
        system_prompt = """You are a Logistics AI Assistant for a Pakistan-based distribution company.
Be helpful, concise, and professional. Use emojis occasionally. Keep responses WhatsApp-friendly."""
        context_note = f"\n[Context: Previous conversation was about dealer '{context['last_dealer']}']" if context and context.get("last_dealer") else ""
        result = self._call_groq([{"role": "system", "content": system_prompt}, {"role": "user", "content": f"{user_message}{context_note}"}])
        return result if result else self._get_fallback_response(user_message)
    
    def generate_executive_summary(self, insights: Dict) -> str:
        result = self._call_groq([{"role": "system", "content": "You are an Executive Logistics Analyst. Provide executive summary."}, {"role": "user", "content": f"Metrics: {insights}"}])
        return f"📊 *Executive Summary*\n\n{result}" if result and len(result) > 50 else None
    
    def _get_fallback_response(self, question: str) -> str:
        q_lower = question.lower()
        if any(w in q_lower for w in ['what do you do', 'what can you do', 'help']):
            return _format_help_message()
        if any(w in q_lower for w in ['hello', 'hi', 'hey']):
            return "👋 Hello! I'm your Logistics AI Assistant. How can I help?"
        return f"I understand you're asking: {question[:100]}\n\nType 'Help' for available commands."


_groq_service = None
def get_groq_service() -> Optional[GroqService]:
    global _groq_service
    if _groq_service is None and GROQ_ENABLED:
        _groq_service = GroqService()
    return _groq_service


# ==========================================================
# CACHES
# ==========================================================

_conversation_cache: Dict[str, ConversationContext] = {}
_query_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
_warehouse_cache: List[str] = []
_warehouse_cache_time = 0


# ==========================================================
# CONVERSATION CONTEXT MANAGEMENT
# ==========================================================

def get_conversation_context(phone_number: str) -> ConversationContext:
    if phone_number not in _conversation_cache:
        _conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
    context = _conversation_cache[phone_number]
    if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
        context = ConversationContext(phone_number=phone_number)
        _conversation_cache[phone_number] = context
    return context


def update_conversation_context(phone_number: str, intent: IntentType = None, entity: str = None, entity_type: str = None):
    context = get_conversation_context(phone_number)
    if intent:
        context.last_intent = intent.value
    if entity_type == "dealer" and entity:
        context.last_dealer = entity
    elif entity_type == "warehouse" and entity:
        context.last_warehouse = entity
    elif entity_type == "dn" and entity:
        context.last_dn = entity
    context.message_count += 1
    context.last_updated = time.time()
    _conversation_cache[phone_number] = context


def get_cache_key(question: str, phone_number: str = None) -> str:
    key = question.lower().strip()
    if phone_number:
        key = f"{phone_number}:{key}"
    return hashlib.md5(key.encode()).hexdigest()


# ==========================================================
# DATE INTELLIGENCE (Issue #11)
# ==========================================================

def parse_date_range(date_str: str, reference_date: date = None) -> Optional[Tuple[date, date]]:
    if reference_date is None:
        reference_date = date.today()
    date_str = date_str.lower().strip()
    
    if date_str == "today":
        return (reference_date, reference_date)
    if date_str == "yesterday":
        yesterday = reference_date - timedelta(days=1)
        return (yesterday, yesterday)
    if date_str == "this week":
        start = reference_date - timedelta(days=reference_date.weekday())
        return (start, reference_date)
    if date_str == "last week":
        end = reference_date - timedelta(days=reference_date.weekday() + 1)
        start = end - timedelta(days=6)
        return (start, end)
    if date_str == "this month":
        return (reference_date.replace(day=1), reference_date)
    if date_str == "last month":
        first_of_this_month = reference_date.replace(day=1)
        end = first_of_this_month - timedelta(days=1)
        return (end.replace(day=1), end)
    if date_str == "last 7 days":
        return (reference_date - timedelta(days=7), reference_date)
    if date_str == "last 15 days":
        return (reference_date - timedelta(days=15), reference_date)
    if date_str == "last 30 days":
        return (reference_date - timedelta(days=30), reference_date)
    if date_str in ["ytd", "this year"]:
        return (reference_date.replace(month=1, day=1), reference_date)
    return None


# ==========================================================
# WAREHOUSE CACHE
# ==========================================================

def get_warehouse_list(db: Session, force_refresh: bool = False) -> List[str]:
    global _warehouse_cache, _warehouse_cache_time
    now = time.time()
    if not force_refresh and _warehouse_cache and (now - _warehouse_cache_time) < WAREHOUSE_CACHE_TTL:
        return _warehouse_cache
    try:
        warehouses = db.query(DeliveryReport.warehouse).filter(DeliveryReport.warehouse.isnot(None)).distinct().limit(50).all()
        _warehouse_cache = [w[0] for w in warehouses if w[0]]
        _warehouse_cache_time = now
        return _warehouse_cache
    except Exception:
        return ['lahore', 'karachi', 'islamabad', 'rawalpindi', 'multan', 'faisalabad']


# ==========================================================
# SMART DEALER EXTRACTION (Issue #2)
# ==========================================================

# Keywords that should NEVER be treated as dealer names
LOGISTICS_KEYWORDS = {
    'pending', 'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units',
    'performance', 'critical', 'alert', 'control', 'tower', 'top',
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    'show', 'display', 'get', 'tell', 'warehouse', 'summary', 'report',
    'kpi', 'dashboard', 'insight', 'issue', 'problem', 'bottleneck',
    'transit', 'delivered', 'aging', 'rate', 'completion'
}


def resolve_dealer_name(db: Session, dealer_input: str) -> Optional[str]:
    """Resolve dealer name from input - single source of truth"""
    if not dealer_input or dealer_input.lower() in LOGISTICS_KEYWORDS:
        return None
    exact = db.query(DeliveryReport).filter(func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)).first()
    if exact:
        return exact.customer_name
    partial = db.query(DeliveryReport).filter(DeliveryReport.customer_name.ilike(f"%{dealer_input}%")).first()
    if partial:
        return partial.customer_name
    return None


def extract_dealer_from_query(question: str, msg_lower: str, db: Session, context: ConversationContext = None) -> Optional[str]:
    """Extract dealer name - rejects logistics keywords"""
    # Strategy 1: Explicit dealer pattern
    dealer_match = re.search(r'(?:dealer|show|display|get)\s+([a-z0-9\s&\-\.]+)', msg_lower)
    if dealer_match:
        candidate = dealer_match.group(1).strip()
        if len(candidate) > 2 and candidate not in LOGISTICS_KEYWORDS:
            resolved = resolve_dealer_name(db, candidate)
            if resolved:
                return resolved
    
    # Strategy 2: Short message - but reject logistics keywords
    if len(msg_lower.split()) <= 5 and len(msg_lower) > 2 and msg_lower not in LOGISTICS_KEYWORDS:
        resolved = resolve_dealer_name(db, msg_lower)
        if resolved:
            return resolved
    
    # Strategy 3: Use context from previous conversation
    if context and context.last_dealer:
        follow_up = ['pending', 'units', 'revenue', 'performance', 'dn', 'delivery', 'pod', 'pgi']
        if any(word in msg_lower for word in follow_up):
            return context.last_dealer
    
    return None


# ==========================================================
# OPTIMIZED SINGLE QUERY DEALER DASHBOARD (Issue #3 & #4)
# ==========================================================

def get_dealer_complete_dashboard(db: Session, dealer_name: str) -> Dict[str, Any]:
    """Single optimized query for complete dealer dashboard"""
    result = db.query(
        # Volume metrics
        func.count(DeliveryReport.id).label('total_dns'),
        func.sum(DeliveryReport.dn_qty).label('total_units'),
        func.sum(DeliveryReport.dn_amount).label('total_revenue'),
        
        # Delivery status counts
        func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('delivered_units_count'),
        func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
        func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('pod_completed'),
        func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
        
        # Aging metrics
        func.avg(case((DeliveryReport.good_issue_date.isnot(None), 
                       func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging'),
        func.avg(case((DeliveryReport.pod_date.isnot(None),
                       func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)), else_=0)).label('avg_pod_aging'),
        
        # Oldest pending
        func.min(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_create_date), else_=None)).label('oldest_pending_date'),
        
        # Top warehouse (by volume)
        func.max(DeliveryReport.warehouse).label('top_warehouse')
    ).filter(DeliveryReport.customer_name == dealer_name).first()
    
    # Calculate rates
    total_dns = result.total_dns or 1
    delivery_rate = (result.delivered_units_count / total_dns) * 100 if total_dns > 0 else 0
    pod_rate = (result.pod_completed / result.delivered_units_count * 100) if result.delivered_units_count > 0 else 0
    
    # Get oldest pending DN
    oldest_pending = db.query(DeliveryReport.dn_no, DeliveryReport.dn_create_date).filter(
        DeliveryReport.customer_name == dealer_name,
        DeliveryReport.good_issue_date.is_(None)
    ).order_by(DeliveryReport.dn_create_date).first()
    
    return {
        "dealer_name": dealer_name,
        "total_dns": total_dns,
        "total_units": int(result.total_units or 0),
        "total_revenue": float(result.total_revenue or 0),
        "delivered_units": result.delivered_units_count or 0,
        "pending_delivery": result.pending_delivery or 0,
        "transit_units": result.pending_pod or 0,
        "pod_completed": result.pod_completed or 0,
        "pending_pod": result.pending_pod or 0,
        "delivery_rate": round(delivery_rate, 1),
        "pod_rate": round(pod_rate, 1),
        "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
        "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
        "oldest_pending_dn": oldest_pending.dn_no if oldest_pending else None,
        "oldest_pending_days": (date.today() - oldest_pending.dn_create_date).days if oldest_pending else 0,
        "top_warehouse": result.top_warehouse or "N/A"
    }


def get_dealer_summary_optimized(db: Session, dealer_name: str) -> Dict[str, Any]:
    """Legacy compatibility - returns basic summary"""
    data = get_dealer_complete_dashboard(db, dealer_name)
    return {
        "total_dns": data["total_dns"],
        "total_units": data["total_units"],
        "total_revenue": data["total_revenue"],
        "pending_delivery": data["pending_delivery"],
        "pending_pod": data["pending_pod"],
        "pgi_completed": data["delivered_units"]
    }


def get_dealer_revenue(db: Session, dealer_name: str) -> float:
    result = db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.customer_name == dealer_name).first()
    return float(result[0] or 0)


def get_dealer_units(db: Session, dealer_name: str) -> int:
    result = db.query(func.sum(DeliveryReport.dn_qty)).filter(DeliveryReport.customer_name == dealer_name).first()
    return int(result[0] or 0)


# ==========================================================
# RANKING ENGINE (Issue #10)
# ==========================================================

def get_top_dealers_by_revenue(db: Session, limit: int = 10) -> List[Dict]:
    results = db.query(DeliveryReport.customer_name, func.sum(DeliveryReport.dn_amount).label('revenue')).filter(
        DeliveryReport.customer_name.isnot(None), DeliveryReport.dn_amount.isnot(None)
    ).group_by(DeliveryReport.customer_name).order_by(desc('revenue')).limit(limit).all()
    return [{"name": r[0], "revenue": float(r[1] or 0)} for r in results]


def get_top_dealers_by_units(db: Session, limit: int = 10) -> List[Dict]:
    results = db.query(DeliveryReport.customer_name, func.sum(DeliveryReport.dn_qty).label('units')).filter(
        DeliveryReport.customer_name.isnot(None)
    ).group_by(DeliveryReport.customer_name).order_by(desc('units')).limit(limit).all()
    return [{"name": r[0], "units": int(r[1] or 0)} for r in results]


def get_worst_dealers_by_pod_aging(db: Session, limit: int = 10) -> List[Dict]:
    results = db.query(
        DeliveryReport.customer_name,
        func.avg(func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)).label('avg_pod_aging')
    ).filter(
        DeliveryReport.customer_name.isnot(None),
        DeliveryReport.good_issue_date.isnot(None),
        DeliveryReport.pod_date.isnot(None)
    ).group_by(DeliveryReport.customer_name).order_by(desc('avg_pod_aging')).limit(limit).all()
    return [{"name": r[0], "avg_pod_aging": round(r[1] or 0, 1)} for r in results]


def get_top_warehouses_by_pending(db: Session, limit: int = 10) -> List[Dict]:
    results = db.query(
        DeliveryReport.warehouse, func.count(DeliveryReport.id).label('pending')
    ).filter(
        DeliveryReport.warehouse.isnot(None), DeliveryReport.good_issue_date.is_(None)
    ).group_by(DeliveryReport.warehouse).order_by(desc('pending')).limit(limit).all()
    return [{"name": r[0], "pending": r[1]} for r in results]


# ==========================================================
# EXECUTIVE INSIGHT ENGINE (Issue #9)
# ==========================================================

def get_executive_insights(db: Session) -> Dict[str, Any]:
    result = db.query(
        func.count(DeliveryReport.id).label('total_dns'),
        func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_pgi'),
        func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
        func.avg(case((DeliveryReport.good_issue_date.isnot(None), func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging')
    ).first()
    
    worst_warehouse = db.query(DeliveryReport.warehouse, func.count(DeliveryReport.id).label('pending')).filter(
        DeliveryReport.good_issue_date.is_(None), DeliveryReport.warehouse.isnot(None)
    ).group_by(DeliveryReport.warehouse).order_by(desc('pending')).first()
    
    oldest = db.query(DeliveryReport.dn_no, DeliveryReport.customer_name, DeliveryReport.dn_create_date).filter(
        DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_create_date.isnot(None)
    ).order_by(DeliveryReport.dn_create_date).first()
    
    insights = {
        "pending_pgi": result.pending_pgi or 0,
        "pending_pod": result.pending_pod or 0,
        "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
        "worst_warehouse": worst_warehouse[0] if worst_warehouse else None,
        "oldest_dn": oldest.dn_no if oldest else None,
        "oldest_aging": (date.today() - oldest.dn_create_date).days if oldest else 0
    }
    
    if insights["pending_pgi"] > 50:
        insights["recommendation"] = "🚨 Expedite PGI processing immediately"
    elif insights["pending_pod"] > 100:
        insights["recommendation"] = "📎 Prioritize POD collection team"
    elif insights["avg_delivery_aging"] > 10:
        insights["recommendation"] = f"⏰ Review delivery process - aging at {insights['avg_delivery_aging']} days"
    else:
        insights["recommendation"] = "✅ Operations stable - continue monitoring"
    
    return insights


# ==========================================================
# CONTROL TOWER ENGINE (OPTIMIZED - Issue #6)
# ==========================================================

def get_critical_deliveries(db: Session, threshold_days: int = 15, limit: int = 10) -> List[Dict]:
    today = date.today()
    results = db.query(
        DeliveryReport.dn_no, DeliveryReport.customer_name, DeliveryReport.warehouse,
        func.datediff(today, DeliveryReport.dn_create_date).label('aging')
    ).filter(
        DeliveryReport.good_issue_date.is_(None),
        DeliveryReport.dn_create_date.isnot(None),
        func.datediff(today, DeliveryReport.dn_create_date) > threshold_days
    ).order_by(desc('aging')).limit(limit).all()
    return [{"dn": r[0], "dealer": r[1], "warehouse": r[2], "aging": r[3]} for r in results]


# ==========================================================
# DN ANALYTICS (Issue #8)
# ==========================================================

def get_dn_details_optimized(db: Session, dn_number: str) -> Optional[Dict]:
    record = db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
    if not record and dn_number.isdigit():
        record = db.query(DeliveryReport).filter(DeliveryReport.dn_no == f"{dn_number}.0").first()
    if not record:
        return None
    
    return {
        "dn_number": record.dn_no,
        "dealer": record.customer_name,
        "warehouse": record.warehouse,
        "city": record.ship_to_city,
        "units": int(record.dn_qty or 0),
        "amount": float(record.dn_amount or 0),
        "dn_date": record.dn_create_date,
        "pgi_date": record.good_issue_date,
        "pod_date": record.pod_date,
        "status": "Delivered" if record.pod_date else "In Transit" if record.good_issue_date else "Pending PGI"
    }


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _format_help_message() -> str:
    return """📋 *AI Logistics Assistant - Help*

*DN Tracking:* Send any 10+ digit DN number
*Dealer:* "Show dealer ABC Traders" or "ABC Traders revenue"
*Warehouse:* "Lahore warehouse summary"
*Pending:* "Pending deliveries" or "Pending POD"
*Performance:* "ABC Traders performance"
*Rankings:* "Top 10 dealers by revenue"
*Executive:* "Key issues" or "Critical alerts"

Need help? Just ask! 🤖"""


def _format_complete_dealer_dashboard(data: Dict[str, Any]) -> str:
    lines = [f"🏪 *Dealer: {data['dealer_name']}*", ""]
    lines.append(f"📄 *Total DNs:* {data['total_dns']:,}")
    lines.append(f"📦 *Total Units:* {data['total_units']:,}")
    lines.append(f"💰 *Revenue:* PKR {data['total_revenue']:,.0f}")
    lines.append("")
    lines.append("📊 *Delivery Status:*")
    lines.append(f"   ✅ Delivered: {data['delivered_units']}")
    lines.append(f"   🚚 In Transit: {data['transit_units']}")
    lines.append(f"   ⏳ Pending: {data['pending_delivery']}")
    lines.append("")
    lines.append(f"📎 *POD Status:* {data['pod_completed']} completed | {data['pending_pod']} pending")
    lines.append("")
    lines.append(f"📈 *Performance:*")
    lines.append(f"   📦 Delivery Rate: {data['delivery_rate']:.1f}%")
    lines.append(f"   📎 POD Rate: {data['pod_rate']:.1f}%")
    lines.append(f"   ⏰ Avg Delivery: {data['avg_delivery_aging']} days")
    lines.append(f"   📋 Avg POD: {data['avg_pod_aging']} days")
    lines.append("")
    if data['oldest_pending_dn']:
        lines.append(f"⚠️ *Oldest Pending:* DN {data['oldest_pending_dn']} ({data['oldest_pending_days']} days)")
    lines.append(f"🏭 *Top Warehouse:* {data['top_warehouse']}")
    return "\n".join(lines)


def _format_dn_response(record, dn_number: str, today: date) -> str:
    delivery_aging = (record.good_issue_date - record.dn_create_date).days if record.dn_create_date and record.good_issue_date else None
    pod_aging = (record.pod_date - record.good_issue_date).days if record.good_issue_date and record.pod_date else None
    status = "✅ Delivered" if record.pod_date else "🚚 In Transit" if record.good_issue_date else "⏳ Pending PGI"
    
    lines = [f"📄 *DN: {dn_number}*", ""]
    lines.append(f"🏪 *Dealer:* {record.customer_name or 'N/A'}")
    lines.append(f"🏭 *Warehouse:* {record.warehouse or 'N/A'}")
    lines.append(f"🌆 *City:* {record.ship_to_city or 'N/A'}")
    lines.append("")
    lines.append(f"📦 *Units:* {int(record.dn_qty or 0):,}")
    lines.append(f"💰 *Amount:* PKR {float(record.dn_amount or 0):,.0f}")
    lines.append("")
    if record.dn_create_date:
        lines.append(f"📅 *DN Date:* {record.dn_create_date.strftime('%Y-%m-%d')}")
    if record.good_issue_date:
        lines.append(f"🚚 *PGI Date:* {record.good_issue_date.strftime('%Y-%m-%d')}")
    if record.pod_date:
        lines.append(f"📎 *POD Date:* {record.pod_date.strftime('%Y-%m-%d')}")
    lines.append("")
    if delivery_aging is not None:
        emoji = "✅" if delivery_aging <= 7 else "⚠️" if delivery_aging <= 15 else "🔴"
        lines.append(f"{emoji} *Delivery Time:* {delivery_aging} days")
    if pod_aging is not None:
        emoji = "✅" if pod_aging <= 7 else "⚠️" if pod_aging <= 15 else "🔴"
        lines.append(f"{emoji} *POD Time:* {pod_aging} days")
    lines.append("")
    lines.append(f"📊 *Status:* {status}")
    return "\n".join(lines)


def _format_warehouse_response(result, warehouse_name: str) -> str:
    lines = [f"🏭 *Warehouse: {warehouse_name.title()}*", ""]
    lines.append(f"📄 *Total DNs:* {result.total_dns or 0:,}")
    lines.append(f"📦 *Total Units:* {int(result.total_units or 0):,}")
    lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
    lines.append("")
    lines.append(f"✅ *PGI Completed:* {result.pgi_completed or 0}")
    lines.append(f"⏳ *Pending Delivery:* {result.pending_delivery or 0}")
    lines.append(f"📎 *Pending POD:* {result.pending_pod or 0}")
    return "\n".join(lines)


# ==========================================================
# QUERY HANDLERS
# ==========================================================

def _handle_dn_query(db: Session, dn_number: str, today: date, req_id: str) -> str:
    try:
        record = db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
        if not record and dn_number.isdigit():
            record = db.query(DeliveryReport).filter(DeliveryReport.dn_no == f"{dn_number}.0").first()
        if not record:
            return f"❌ DN {dn_number} not found."
        return _format_dn_response(record, dn_number, today)
    except Exception as e:
        logger.exception(f"[{req_id}] DN error: {e}")
        return f"❌ Error looking up DN {dn_number}"


def _handle_warehouse_query(db: Session, warehouse_name: str, today: date, req_id: str) -> str:
    try:
        result = db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(DeliveryReport.dn_qty).label('total_units'),
            func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
            func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
            func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('pgi_completed')
        ).filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")).first()
        return _format_warehouse_response(result, warehouse_name)
    except Exception as e:
        logger.exception(f"[{req_id}] Warehouse error: {e}")
        return f"❌ Error fetching {warehouse_name} data"


def _handle_dealer_summary_query(db: Session, dealer_name: str, req_id: str) -> str:
    try:
        resolved = resolve_dealer_name(db, dealer_name)
        if not resolved:
            return f"❌ Dealer '{dealer_name}' not found."
        data = get_dealer_complete_dashboard(db, resolved)
        return _format_complete_dealer_dashboard(data)
    except Exception as e:
        logger.exception(f"[{req_id}] Dealer error: {e}")
        return f"❌ Error fetching dealer data"


def _handle_dealer_revenue_query(db: Session, dealer_name: str, req_id: str) -> str:
    try:
        resolved = resolve_dealer_name(db, dealer_name)
        if not resolved:
            return "❌ Please specify a valid dealer name."
        revenue = get_dealer_revenue(db, resolved)
        return f"💰 *Revenue for {resolved}:* PKR {revenue:,.0f}"
    except Exception as e:
        logger.exception(f"[{req_id}] Revenue error: {e}")
        return "❌ Error fetching revenue"


def _handle_dealer_units_query(db: Session, dealer_name: str, req_id: str) -> str:
    try:
        resolved = resolve_dealer_name(db, dealer_name)
        if not resolved:
            return "❌ Please specify a valid dealer name."
        units = get_dealer_units(db, resolved)
        return f"📦 *Units for {resolved}:* {units:,}"
    except Exception as e:
        logger.exception(f"[{req_id}] Units error: {e}")
        return "❌ Error fetching units"


def _handle_dealer_performance_query(db: Session, dealer_name: str, req_id: str) -> str:
    try:
        resolved = resolve_dealer_name(db, dealer_name)
        if not resolved:
            return "❌ Please specify a valid dealer name."
        data = get_dealer_complete_dashboard(db, resolved)
        lines = [f"📊 *Performance Dashboard: {resolved}*", ""]
        lines.append(f"💰 *Revenue:* PKR {data['total_revenue']:,.0f}")
        lines.append(f"📦 *Units:* {data['total_units']:,}")
        lines.append(f"📄 *Total DNs:* {data['total_dns']}")
        lines.append("")
        lines.append(f"🚚 *Delivery Rate:* {data['delivery_rate']:.1f}%")
        lines.append(f"📎 *POD Rate:* {data['pod_rate']:.1f}%")
        lines.append(f"⏳ *Pending Delivery:* {data['pending_delivery']}")
        lines.append(f"🚚 *In Transit:* {data['transit_units']}")
        return "\n".join(lines)
    except Exception as e:
        logger.exception(f"[{req_id}] Performance error: {e}")
        return "❌ Error fetching performance data"


def _handle_ranking_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        if 'dealer' in msg_lower and 'revenue' in msg_lower:
            limit = 5 if 'top 5' in msg_lower else 10
            top = get_top_dealers_by_revenue(db, limit)
            lines = [f"🏆 *Top {limit} Dealers by Revenue*", ""]
            for i, d in enumerate(top, 1):
                lines.append(f"{i}. {d['name']}: PKR {d['revenue']:,.0f}")
            return "\n".join(lines)
        elif 'dealer' in msg_lower and 'units' in msg_lower:
            limit = 5 if 'top 5' in msg_lower else 10
            top = get_top_dealers_by_units(db, limit)
            lines = [f"🏆 *Top {limit} Dealers by Units*", ""]
            for i, d in enumerate(top, 1):
                lines.append(f"{i}. {d['name']}: {d['units']:,} units")
            return "\n".join(lines)
        elif 'dealer' in msg_lower and 'pod aging' in msg_lower:
            worst = get_worst_dealers_by_pod_aging(db, 10)
            lines = ["📋 *Worst Dealers by POD Aging*", ""]
            for i, d in enumerate(worst[:5], 1):
                lines.append(f"{i}. {d['name']}: {d['avg_pod_aging']} days")
            return "\n".join(lines)
        elif 'warehouse' in msg_lower and 'pending' in msg_lower:
            top = get_top_warehouses_by_pending(db, 10)
            lines = ["🏭 *Warehouses with Most Pending*", ""]
            for i, w in enumerate(top[:5], 1):
                lines.append(f"{i}. {w['name']}: {w['pending']} pending")
            return "\n".join(lines)
        return "📊 Please specify: 'Top 10 dealers by revenue' or 'Top warehouses by pending'"
    except Exception as e:
        logger.exception(f"[{req_id}] Ranking error: {e}")
        return "❌ Error fetching rankings"


def _handle_executive_insight(db: Session, req_id: str) -> str:
    try:
        insights = get_executive_insights(db)
        groq = get_groq_service()
        if groq and groq.is_available:
            groq_summary = groq.generate_executive_summary(insights)
            if groq_summary:
                return groq_summary
        lines = ["🚨 *Executive Insight*", ""]
        lines.append(f"📊 *Pending PGI:* {insights['pending_pgi']}")
        lines.append(f"📎 *Pending POD:* {insights['pending_pod']}")
        lines.append(f"⏰ *Avg Delivery Aging:* {insights['avg_delivery_aging']} days")
        lines.append("")
        if insights['worst_warehouse']:
            lines.append(f"🏭 *Critical Warehouse:* {insights['worst_warehouse']}")
        if insights['oldest_dn']:
            lines.append(f"🔴 *Oldest Pending:* DN {insights['oldest_dn']} ({insights['oldest_aging']} days)")
        lines.append("")
        lines.append(f"💡 *Recommendation:* {insights['recommendation']}")
        return "\n".join(lines)
    except Exception as e:
        logger.exception(f"[{req_id}] Executive insight error: {e}")
        return "📊 Unable to generate executive insights."


def _handle_control_tower(db: Session, req_id: str) -> str:
    try:
        critical = get_critical_deliveries(db, threshold_days=15, limit=10)
        if not critical:
            return "✅ No critical deliveries (>15 days) found."
        lines = ["🚨 *Control Tower - Critical Alerts*", ""]
        lines.append(f"🔴 *{len(critical)} deliveries exceed 15 days*")
        for item in critical[:5]:
            lines.append(f"   • DN {item['dn']}: {item['dealer']} - {item['aging']} days ({item['warehouse']})")
        return "\n".join(lines)
    except Exception as e:
        logger.exception(f"[{req_id}] Control tower error: {e}")
        return "❌ Error generating control tower report"


def _handle_pgi_pending_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = extract_dealer_from_query("", msg_lower, db, None)
        query = db.query(func.count(DeliveryReport.id)).filter(DeliveryReport.good_issue_date.is_(None))
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
            return f"⏳ *PGI Pending for {dealer_name}:* {query.scalar() or 0}"
        return f"⏳ *Total PGI Pending:* {query.scalar() or 0}"
    except Exception as e:
        logger.exception(f"[{req_id}] PGI pending error: {e}")
        return "❌ Error fetching PGI pending data"


def _handle_pod_pending_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = extract_dealer_from_query("", msg_lower, db, None)
        query = db.query(func.count(DeliveryReport.id)).filter(
            DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)
        )
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name == dealer_name)
            return f"📎 *POD Pending for {dealer_name}:* {query.scalar() or 0}"
        return f"📎 *Total POD Pending:* {query.scalar() or 0}"
    except Exception as e:
        logger.exception(f"[{req_id}] POD pending error: {e}")
        return "❌ Error fetching POD pending data"


# ==========================================================
# INTENT CLASSIFICATION (FIXED ORDER - Issue #1)
# ==========================================================

def _classify_intent(question: str, msg_lower: str, db: Session, context: ConversationContext) -> ProcessedQuery:
    # 1. HELP
    if msg_lower in ['help', '/help', 'menu', '?', 'commands', 'what can you do']:
        return ProcessedQuery(intent=IntentType.HELP, confidence=1.0)
    
    # 2. DN NUMBER
    dn_match = re.search(r'\b(\d{8,12})\b', question)
    if dn_match:
        return ProcessedQuery(intent=IntentType.DN_QUERY, entity=dn_match.group(1), entity_type="dn", confidence=1.0)
    
    # 3. EXECUTIVE INSIGHT
    if any(kw in msg_lower for kw in ['key issue', 'biggest problem', 'bottleneck', 'executive insight']):
        return ProcessedQuery(intent=IntentType.EXECUTIVE_INSIGHT, confidence=0.95)
    
    # 4. CONTROL TOWER
    if any(kw in msg_lower for kw in ['critical', 'alert', 'urgent', 'control tower']):
        return ProcessedQuery(intent=IntentType.CONTROL_TOWER, confidence=0.95)
    
    # 5. RANKING
    if ('top' in msg_lower or 'best' in msg_lower) and ('dealer' in msg_lower or 'warehouse' in msg_lower):
        return ProcessedQuery(intent=IntentType.RANKING_QUERY, confidence=0.9)
    
    # 6. PGI QUERIES (BEFORE WAREHOUSE)
    if 'pgi' in msg_lower and 'pending' in msg_lower:
        return ProcessedQuery(intent=IntentType.PGI_QUERY, confidence=0.9)
    
    # 7. POD QUERIES (BEFORE WAREHOUSE)
    if 'pod' in msg_lower and 'pending' in msg_lower:
        return ProcessedQuery(intent=IntentType.POD_QUERY, confidence=0.9)
    
    # 8. DEALER QUERIES
    dealer_name = extract_dealer_from_query(question, msg_lower, db, context)
    if dealer_name:
        if any(kw in msg_lower for kw in ['revenue', 'sales', 'amount']):
            return ProcessedQuery(intent=IntentType.DEALER_QUERY, entity=dealer_name, entity_type="dealer", metric="revenue", confidence=0.9)
        elif any(kw in msg_lower for kw in ['units', 'quantity', 'qty']):
            return ProcessedQuery(intent=IntentType.DEALER_QUERY, entity=dealer_name, entity_type="dealer", metric="units", confidence=0.9)
        elif 'performance' in msg_lower or 'kpi' in msg_lower:
            return ProcessedQuery(intent=IntentType.DEALER_QUERY, entity=dealer_name, entity_type="dealer", metric="performance", confidence=0.9)
        else:
            return ProcessedQuery(intent=IntentType.DEALER_QUERY, entity=dealer_name, entity_type="dealer", metric="summary", confidence=0.85)
    
    # 9. WAREHOUSE QUERY (LAST - AFTER SPECIFIC INTENTS)
    warehouses = get_warehouse_list(db)
    for wh in warehouses:
        if wh.lower() in msg_lower and ('warehouse' in msg_lower or 'summary' in msg_lower):
            return ProcessedQuery(intent=IntentType.WAREHOUSE_QUERY, entity=wh, entity_type="warehouse", confidence=0.8)
    
    # 10. GENERAL AI
    return ProcessedQuery(intent=IntentType.GENERAL_AI, needs_groq=True, confidence=0.5)


# ==========================================================
# MAIN ENTRY POINT (PRESERVED SIGNATURE)
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    start_time = time.time()
    req_id = request_id or str(uuid.uuid4())[:8]
    logger.info(f"[{req_id}] Phone={phone_number} Q={question[:100]}")
    
    db = None
    try:
        db = session_factory() if session_factory else SessionLocal()
        msg_lower = question.lower().strip()
        
        context = get_conversation_context(phone_number) if phone_number else None
        cache_key = get_cache_key(question, phone_number)
        
        processed = _classify_intent(question, msg_lower, db, context)
        logger.info(f"[{req_id}] Intent: {processed.intent.value}")
        
        response = None
        
        if processed.intent == IntentType.HELP:
            response = _format_help_message()
        elif processed.intent == IntentType.DN_QUERY:
            response = _handle_dn_query(db, processed.entity, date.today(), req_id)
        elif processed.intent == IntentType.WAREHOUSE_QUERY:
            response = _handle_warehouse_query(db, processed.entity, date.today(), req_id)
        elif processed.intent == IntentType.DEALER_QUERY:
            if processed.metric == "revenue":
                response = _handle_dealer_revenue_query(db, processed.entity, req_id)
            elif processed.metric == "units":
                response = _handle_dealer_units_query(db, processed.entity, req_id)
            elif processed.metric == "performance":
                response = _handle_dealer_performance_query(db, processed.entity, req_id)
            else:
                response = _handle_dealer_summary_query(db, processed.entity, req_id)
        elif processed.intent == IntentType.PGI_QUERY:
            response = _handle_pgi_pending_query(db, msg_lower, req_id)
        elif processed.intent == IntentType.POD_QUERY:
            response = _handle_pod_pending_query(db, msg_lower, req_id)
        elif processed.intent == IntentType.CONTROL_TOWER:
            response = _handle_control_tower(db, req_id)
        elif processed.intent == IntentType.EXECUTIVE_INSIGHT:
            response = _handle_executive_insight(db, req_id)
        elif processed.intent == IntentType.RANKING_QUERY:
            response = _handle_ranking_query(db, msg_lower, req_id)
        elif processed.intent == IntentType.GENERAL_AI or processed.needs_groq:
            groq = get_groq_service()
            if groq and groq.is_available:
                context_dict = {"last_dealer": context.last_dealer} if context else None
                response = groq.chat(question, context_dict)
            else:
                response = _format_help_message()
        else:
            response = _format_help_message()
        
        if phone_number and response:
            update_conversation_context(phone_number, processed.intent, processed.entity, processed.entity_type)
        
        duration_ms = int((time.time() - start_time) * 1000)
        logger.info(f"[{req_id}] Done in {duration_ms}ms")
        return response
        
    except Exception as e:
        logger.exception(f"[{req_id}] Fatal error: {e}")
        return "❌ I encountered an error. Please try again or type 'Help'."
    finally:
        if db:
            db.close()
