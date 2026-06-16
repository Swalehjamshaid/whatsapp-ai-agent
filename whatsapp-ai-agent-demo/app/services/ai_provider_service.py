# ==========================================================
# FILE: app/services/ai_provider_service.py (v7.0 - ENTERPRISE ORCHESTRATOR)
# PURPOSE: AI Provider Service - Natural Language Query Processing
#
# ENTERPRISE FEATURES v7.0:
# - ✅ CLEAN ARCHITECTURE: Pure orchestrator layer
# - ✅ ROUTING ENGINE: Intelligent query routing
# - ✅ HANDLER REGISTRY: Modular handler system
# - ✅ ENHANCED CONTEXT: Rich conversation tracking
# - ✅ PERFORMANCE: Sub-second response times
# - ✅ CACHING: Multi-level cache with metrics
# - ✅ OBSERVABILITY: Structured logging with correlation
# - ✅ ERROR HANDLING: Graceful degradation
# - ✅ BACKWARD COMPATIBLE: No WhatsApp changes
# ==========================================================

import re
import time
import uuid
import hashlib
from datetime import datetime, date, timedelta
from typing import Optional, Callable, Any, Dict, List, Tuple, Union
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
DEALER_CACHE_TTL = 3600
EXECUTIVE_CACHE_TTL = 600
RANKING_CACHE_TTL = 1800
PROCESSING_TIMEOUT_SECONDS = 20
GROQ_ENABLED = bool(GROQ_API_KEY)


# ==========================================================
# ENHANCED INTENT TYPES
# ==========================================================

class IntentType(Enum):
    """Enterprise intent classification"""
    HELP = "help"
    DN_LOOKUP = "dn_lookup"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_REVENUE = "dealer_revenue"
    DEALER_UNITS = "dealer_units"
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_AGING = "dealer_aging"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    PGI_AGING = "pgi_aging"
    POD_AGING = "pod_aging"
    TOP_DEALERS = "top_dealers"
    TOP_WAREHOUSES = "top_warehouses"
    EXECUTIVE_INSIGHT = "executive_insight"
    CONTROL_TOWER = "control_tower"
    ROOT_CAUSE = "root_cause"
    GENERAL_AI = "general_ai"
    UNKNOWN = "unknown"


# ==========================================================
# ENHANCED CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    """Rich conversation context for intelligent follow-ups"""
    phone_number: str
    last_intent: Optional[str] = None
    last_entity: Optional[str] = None
    last_entity_type: Optional[str] = None
    last_metric: Optional[str] = None
    last_question: Optional[str] = None
    last_response_type: Optional[str] = None
    last_date_filter: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    last_dn: Optional[str] = None
    conversation_state: Optional[str] = None
    message_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


# ==========================================================
# HANDLER REGISTRY
# ==========================================================

@dataclass
class HandlerResult:
    """Standardized handler result"""
    response: str
    context_updates: Optional[Dict[str, Any]] = None
    cache_key: Optional[str] = None
    cache_ttl: Optional[int] = None


class QueryHandler:
    """Base class for all query handlers"""
    
    def __init__(self, db: Session, request_id: str):
        self.db = db
        self.request_id = request_id
        self.today = date.today()
    
    def handle(self, processed_query: 'ProcessedQuery', context: Optional[ConversationContext] = None) -> HandlerResult:
        raise NotImplementedError


# ==========================================================
# PROCESSED QUERY
# ==========================================================

@dataclass
class ProcessedQuery:
    """Enhanced processed query with routing information"""
    intent: IntentType
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    metric: Optional[str] = None
    date_range: Optional[Tuple[date, date]] = None
    limit: int = 10
    confidence: float = 0.0
    needs_groq: bool = False
    raw_question: Optional[str] = None
    context_updates: Dict[str, Any] = field(default_factory=dict)


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
        context_note = f"\n[Context: Previous conversation was about dealer '{context.get('last_dealer')}']" if context and context.get("last_dealer") else ""
        result = self._call_groq([{"role": "system", "content": system_prompt}, {"role": "user", "content": f"{user_message}{context_note}"}])
        return result if result else self._get_fallback_response(user_message)
    
    def generate_executive_summary(self, insights: Dict) -> str:
        result = self._call_groq([
            {"role": "system", "content": "You are an Executive Logistics Analyst. Provide executive summary."},
            {"role": "user", "content": f"Metrics: {insights}"}
        ])
        return f"📊 *Executive Summary*\n\n{result}" if result and len(result) > 50 else None
    
    def analyze_root_cause(self, issue: str, data: Dict) -> str:
        result = self._call_groq([
            {"role": "system", "content": "You are a Root Cause Analyst. Identify key issues."},
            {"role": "user", "content": f"Issue: {issue}\nData: {data}"}
        ])
        return f"🔍 *Root Cause Analysis*\n\n{result}" if result else "Unable to perform root cause analysis."
    
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
# ENHANCED CACHE SYSTEM
# ==========================================================

class CacheSystem:
    """Multi-level cache system with metrics"""
    
    def __init__(self):
        self.dealer_cache = TTLCache(maxsize=500, ttl=DEALER_CACHE_TTL)
        self.warehouse_cache = TTLCache(maxsize=100, ttl=WAREHOUSE_CACHE_TTL)
        self.executive_cache = TTLCache(maxsize=50, ttl=EXECUTIVE_CACHE_TTL)
        self.ranking_cache = TTLCache(maxsize=50, ttl=RANKING_CACHE_TTL)
        self.query_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        
        self.metrics = {
            "dealer_hits": 0, "dealer_misses": 0,
            "warehouse_hits": 0, "warehouse_misses": 0,
            "executive_hits": 0, "executive_misses": 0,
            "ranking_hits": 0, "ranking_misses": 0,
            "query_hits": 0, "query_misses": 0
        }
    
    def get(self, cache_type: str, key: str) -> Optional[Any]:
        cache_map = {
            "dealer": self.dealer_cache,
            "warehouse": self.warehouse_cache,
            "executive": self.executive_cache,
            "ranking": self.ranking_cache,
            "query": self.query_cache
        }
        cache = cache_map.get(cache_type)
        if not cache:
            return None
        
        value = cache.get(key)
        if value is not None:
            self.metrics[f"{cache_type}_hits"] += 1
            return value
        self.metrics[f"{cache_type}_misses"] += 1
        return None
    
    def set(self, cache_type: str, key: str, value: Any):
        cache_map = {
            "dealer": self.dealer_cache,
            "warehouse": self.warehouse_cache,
            "executive": self.executive_cache,
            "ranking": self.ranking_cache,
            "query": self.query_cache
        }
        cache = cache_map.get(cache_type)
        if cache:
            cache[key] = value
    
    def get_metrics(self) -> Dict[str, Any]:
        total_hits = sum(v for k, v in self.metrics.items() if k.endswith("_hits"))
        total_misses = sum(v for k, v in self.metrics.items() if k.endswith("_misses"))
        total = total_hits + total_misses
        return {
            **self.metrics,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "hit_rate": total_hits / total if total > 0 else 0
        }


_cache_system = CacheSystem()


# ==========================================================
# LOGISTICS KEYWORDS (REJECT LIST)
# ==========================================================

LOGISTICS_KEYWORDS = {
    'pending', 'pgi', 'pod', 'aging', 'delivery', 'revenue', 'units',
    'performance', 'critical', 'alert', 'control', 'tower', 'top',
    'help', 'menu', 'status', 'what', 'how', 'why', 'when', 'where',
    'who', 'which', 'can', 'could', 'would', 'should', 'is', 'are',
    'show', 'display', 'get', 'tell', 'warehouse', 'summary', 'report',
    'kpi', 'dashboard', 'insight', 'issue', 'problem', 'bottleneck',
    'transit', 'delivered', 'aging', 'rate', 'completion'
}


# ==========================================================
# DEALER RESOLUTION ENGINE
# ==========================================================

def resolve_dealer_name(db: Session, dealer_input: str) -> Optional[str]:
    """Resolve dealer name from input - single source of truth"""
    if not dealer_input or dealer_input.lower() in LOGISTICS_KEYWORDS:
        return None
    
    # Check cache first
    cache_key = f"dealer_resolve_{dealer_input.lower()}"
    cached = _cache_system.get("dealer", cache_key)
    if cached:
        return cached
    
    # Exact match
    exact = db.query(DeliveryReport).filter(
        func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
    ).first()
    if exact:
        _cache_system.set("dealer", cache_key, exact.customer_name)
        return exact.customer_name
    
    # Partial match
    partial = db.query(DeliveryReport).filter(
        DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
    ).first()
    if partial:
        _cache_system.set("dealer", cache_key, partial.customer_name)
        return partial.customer_name
    
    _cache_system.set("dealer", cache_key, None)
    return None


def extract_dealer_from_query(msg_lower: str, db: Session, context: Optional[ConversationContext] = None) -> Optional[str]:
    """Extract dealer name - rejects logistics keywords"""
    # Strategy 1: Explicit dealer pattern
    dealer_match = re.search(r'(?:dealer|show|display|get)\s+([a-z0-9\s&\-\.]+)', msg_lower)
    if dealer_match:
        candidate = dealer_match.group(1).strip()
        if len(candidate) > 2 and candidate not in LOGISTICS_KEYWORDS:
            resolved = resolve_dealer_name(db, candidate)
            if resolved:
                return resolved
    
    # Strategy 2: Short message
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
# HANDLER IMPLEMENTATIONS
# ==========================================================

class HelpHandler(QueryHandler):
    """Handler for help requests"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        return HandlerResult(response=_format_help_message())


class DNLookupHandler(QueryHandler):
    """Handler for DN lookup requests"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        dn_number = processed_query.entity
        if not dn_number:
            return HandlerResult(response="❌ Please provide a valid DN number.")
        
        try:
            record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
            if not record and dn_number.isdigit():
                record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == f"{dn_number}.0").first()
            if not record:
                return HandlerResult(response=f"❌ DN {dn_number} not found.")
            
            response = self._format_dn_response(record, dn_number)
            context_updates = {"last_dn": dn_number, "last_entity_type": "dn"}
            return HandlerResult(response=response, context_updates=context_updates)
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] DN error: {e}")
            return HandlerResult(response="❌ Error looking up DN. Please try again.")
    
    def _format_dn_response(self, record, dn_number: str) -> str:
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


class DealerDashboardHandler(QueryHandler):
    """Handler for dealer dashboard requests"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        dealer_name = processed_query.entity
        metric = processed_query.metric
        
        if not dealer_name:
            dealer_name = context.last_dealer if context else None
            if not dealer_name:
                return HandlerResult(response="❌ Please specify a dealer name.")
        
        try:
            resolved = resolve_dealer_name(self.db, dealer_name)
            if not resolved:
                return HandlerResult(response=f"❌ Dealer '{dealer_name}' not found.")
            
            # Check cache
            cache_key = f"dealer_dashboard_{resolved}"
            cached = _cache_system.get("dealer", cache_key)
            if cached and metric == "summary":
                return HandlerResult(response=cached)
            
            data = self._get_dealer_dashboard(resolved)
            response = self._format_response(data, metric)
            
            if metric == "summary":
                _cache_system.set("dealer", cache_key, response)
            
            context_updates = {"last_dealer": resolved, "last_entity": resolved, "last_entity_type": "dealer"}
            return HandlerResult(response=response, context_updates=context_updates)
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] Dealer error: {e}")
            return HandlerResult(response="❌ Error fetching dealer data. Please try again.")
    
    def _get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        result = self.db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(DeliveryReport.dn_qty).label('total_units'),
            func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('delivered_units'),
            func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
            func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
            func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('pod_completed'),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), 
                          func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging'),
            func.avg(case((DeliveryReport.pod_date.isnot(None),
                          func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)), else_=0)).label('avg_pod_aging'),
            func.min(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_create_date), else_=None)).label('oldest_pending_date'),
            func.max(DeliveryReport.warehouse).label('top_warehouse')
        ).filter(DeliveryReport.customer_name == dealer_name).first()
        
        total_dns = result.total_dns or 1
        delivery_rate = (result.delivered_units / total_dns) * 100 if total_dns > 0 else 0
        pod_rate = (result.pod_completed / result.delivered_units * 100) if result.delivered_units > 0 else 0
        
        # Get oldest pending DN
        oldest_pending = self.db.query(DeliveryReport.dn_no, DeliveryReport.dn_create_date).filter(
            DeliveryReport.customer_name == dealer_name,
            DeliveryReport.good_issue_date.is_(None)
        ).order_by(DeliveryReport.dn_create_date).first()
        
        return {
            "dealer_name": dealer_name,
            "total_dns": total_dns,
            "total_units": int(result.total_units or 0),
            "total_revenue": float(result.total_revenue or 0),
            "delivered_units": result.delivered_units or 0,
            "pending_delivery": result.pending_delivery or 0,
            "pending_pod": result.pending_pod or 0,
            "pod_completed": result.pod_completed or 0,
            "delivery_rate": round(delivery_rate, 1),
            "pod_rate": round(pod_rate, 1),
            "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
            "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
            "oldest_pending_dn": oldest_pending.dn_no if oldest_pending else None,
            "oldest_pending_days": (self.today - oldest_pending.dn_create_date).days if oldest_pending else 0,
            "top_warehouse": result.top_warehouse or "N/A"
        }
    
    def _format_response(self, data: Dict[str, Any], metric: str) -> str:
        if metric == "revenue":
            return f"💰 *Revenue for {data['dealer_name']}:* PKR {data['total_revenue']:,.0f}"
        
        if metric == "units":
            return f"📦 *Units for {data['dealer_name']}:* {data['total_units']:,}"
        
        if metric == "performance":
            lines = [f"📊 *Performance Dashboard: {data['dealer_name']}*", ""]
            lines.append(f"💰 *Revenue:* PKR {data['total_revenue']:,.0f}")
            lines.append(f"📦 *Units:* {data['total_units']:,}")
            lines.append(f"📄 *Total DNs:* {data['total_dns']}")
            lines.append("")
            lines.append(f"🚚 *Delivery Rate:* {data['delivery_rate']:.1f}%")
            lines.append(f"📎 *POD Rate:* {data['pod_rate']:.1f}%")
            lines.append(f"⏳ *Pending Delivery:* {data['pending_delivery']}")
            lines.append(f"🚚 *In Transit:* {data['pending_pod']}")
            return "\n".join(lines)
        
        if metric == "aging":
            lines = [f"⏰ *Aging Report: {data['dealer_name']}*", ""]
            lines.append(f"📦 *Avg Delivery Aging:* {data['avg_delivery_aging']} days")
            lines.append(f"📎 *Avg POD Aging:* {data['avg_pod_aging']} days")
            if data['oldest_pending_dn']:
                lines.append(f"🔴 *Oldest Pending:* DN {data['oldest_pending_dn']} ({data['oldest_pending_days']} days)")
            return "\n".join(lines)
        
        # Full dashboard (default)
        lines = [f"🏪 *Dealer: {data['dealer_name']}*", ""]
        lines.append(f"📄 *Total DNs:* {data['total_dns']:,}")
        lines.append(f"📦 *Total Units:* {data['total_units']:,}")
        lines.append(f"💰 *Revenue:* PKR {data['total_revenue']:,.0f}")
        lines.append("")
        lines.append("📊 *Delivery Status:*")
        lines.append(f"   ✅ Delivered: {data['delivered_units']}")
        lines.append(f"   🚚 In Transit: {data['pending_pod']}")
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


class WarehouseDashboardHandler(QueryHandler):
    """Handler for warehouse dashboard requests"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        warehouse_name = processed_query.entity
        
        if not warehouse_name:
            warehouse_name = context.last_warehouse if context else None
            if not warehouse_name:
                return HandlerResult(response="❌ Please specify a warehouse name.")
        
        try:
            # Check cache
            cache_key = f"warehouse_{warehouse_name.lower()}"
            cached = _cache_system.get("warehouse", cache_key)
            if cached:
                return HandlerResult(response=cached)
            
            result = self.db.query(
                func.count(DeliveryReport.id).label('total_dns'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_delivery'),
                func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
                func.sum(case((DeliveryReport.good_issue_date.isnot(None), 1), else_=0)).label('pgi_completed')
            ).filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")).first()
            
            lines = [f"🏭 *Warehouse: {warehouse_name.title()}*", ""]
            lines.append(f"📄 *Total DNs:* {result.total_dns or 0:,}")
            lines.append(f"📦 *Total Units:* {int(result.total_units or 0):,}")
            lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
            lines.append("")
            lines.append(f"✅ *PGI Completed:* {result.pgi_completed or 0}")
            lines.append(f"⏳ *Pending Delivery:* {result.pending_delivery or 0}")
            lines.append(f"📎 *Pending POD:* {result.pending_pod or 0}")
            
            response = "\n".join(lines)
            _cache_system.set("warehouse", cache_key, response)
            
            context_updates = {"last_warehouse": warehouse_name, "last_entity": warehouse_name, "last_entity_type": "warehouse"}
            return HandlerResult(response=response, context_updates=context_updates)
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] Warehouse error: {e}")
            return HandlerResult(response="❌ Error fetching warehouse data. Please try again.")


class PendingHandler(QueryHandler):
    """Handler for pending queries (PGI and POD)"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        intent = processed_query.intent
        dealer_name = None
        
        # Try to extract dealer from query or context
        if processed_query.entity:
            dealer_name = processed_query.entity
        elif context and context.last_dealer:
            dealer_name = context.last_dealer
        
        try:
            if intent == IntentType.PENDING_PGI:
                query = self.db.query(func.count(DeliveryReport.id)).filter(DeliveryReport.good_issue_date.is_(None))
                if dealer_name:
                    resolved = resolve_dealer_name(self.db, dealer_name)
                    if resolved:
                        query = query.filter(DeliveryReport.customer_name == resolved)
                        response = f"⏳ *PGI Pending for {resolved}:* {query.scalar() or 0}"
                    else:
                        response = f"⏳ *Total PGI Pending:* {query.scalar() or 0}"
                else:
                    response = f"⏳ *Total PGI Pending:* {query.scalar() or 0}"
                return HandlerResult(response=response)
            
            elif intent == IntentType.PENDING_POD:
                query = self.db.query(func.count(DeliveryReport.id)).filter(
                    DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)
                )
                if dealer_name:
                    resolved = resolve_dealer_name(self.db, dealer_name)
                    if resolved:
                        query = query.filter(DeliveryReport.customer_name == resolved)
                        response = f"📎 *POD Pending for {resolved}:* {query.scalar() or 0}"
                    else:
                        response = f"📎 *Total POD Pending:* {query.scalar() or 0}"
                else:
                    response = f"📎 *Total POD Pending:* {query.scalar() or 0}"
                return HandlerResult(response=response)
            
            return HandlerResult(response="❌ Invalid pending query type.")
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] Pending error: {e}")
            return HandlerResult(response="❌ Error fetching pending data. Please try again.")


class RankingHandler(QueryHandler):
    """Handler for ranking queries"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        msg_lower = processed_query.raw_question or ""
        
        try:
            if 'revenue' in msg_lower:
                cache_key = "ranking_revenue"
                cached = _cache_system.get("ranking", cache_key)
                if cached:
                    return HandlerResult(response=cached)
                
                top = self._get_top_dealers_by_revenue(processed_query.limit)
                lines = [f"🏆 *Top {processed_query.limit} Dealers by Revenue*", ""]
                for i, d in enumerate(top, 1):
                    lines.append(f"{i}. {d['name']}: PKR {d['revenue']:,.0f}")
                response = "\n".join(lines)
                _cache_system.set("ranking", cache_key, response)
                return HandlerResult(response=response)
            
            elif 'units' in msg_lower:
                cache_key = "ranking_units"
                cached = _cache_system.get("ranking", cache_key)
                if cached:
                    return HandlerResult(response=cached)
                
                top = self._get_top_dealers_by_units(processed_query.limit)
                lines = [f"🏆 *Top {processed_query.limit} Dealers by Units*", ""]
                for i, d in enumerate(top, 1):
                    lines.append(f"{i}. {d['name']}: {d['units']:,} units")
                response = "\n".join(lines)
                _cache_system.set("ranking", cache_key, response)
                return HandlerResult(response=response)
            
            elif 'pod aging' in msg_lower or 'worst' in msg_lower:
                worst = self._get_worst_dealers_by_pod_aging(10)
                lines = ["📋 *Worst Dealers by POD Aging*", ""]
                for i, d in enumerate(worst[:5], 1):
                    lines.append(f"{i}. {d['name']}: {d['avg_pod_aging']} days")
                return HandlerResult(response="\n".join(lines))
            
            elif 'warehouse' in msg_lower and 'pending' in msg_lower:
                top = self._get_top_warehouses_by_pending(10)
                lines = ["🏭 *Warehouses with Most Pending*", ""]
                for i, w in enumerate(top[:5], 1):
                    lines.append(f"{i}. {w['name']}: {w['pending']} pending")
                return HandlerResult(response="\n".join(lines))
            
            return HandlerResult(response="📊 Please specify: 'Top 10 dealers by revenue' or 'Top warehouses by pending'")
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] Ranking error: {e}")
            return HandlerResult(response="❌ Error fetching rankings. Please try again.")
    
    def _get_top_dealers_by_revenue(self, limit: int) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.customer_name, 
            func.sum(DeliveryReport.dn_amount).label('revenue')
        ).filter(
            DeliveryReport.customer_name.isnot(None), 
            DeliveryReport.dn_amount.isnot(None)
        ).group_by(DeliveryReport.customer_name).order_by(desc('revenue')).limit(limit).all()
        return [{"name": r[0], "revenue": float(r[1] or 0)} for r in results]
    
    def _get_top_dealers_by_units(self, limit: int) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.customer_name, 
            func.sum(DeliveryReport.dn_qty).label('units')
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(DeliveryReport.customer_name).order_by(desc('units')).limit(limit).all()
        return [{"name": r[0], "units": int(r[1] or 0)} for r in results]
    
    def _get_worst_dealers_by_pod_aging(self, limit: int) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.customer_name,
            func.avg(func.datediff(DeliveryReport.pod_date, DeliveryReport.good_issue_date)).label('avg_pod_aging')
        ).filter(
            DeliveryReport.customer_name.isnot(None),
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.isnot(None)
        ).group_by(DeliveryReport.customer_name).order_by(desc('avg_pod_aging')).limit(limit).all()
        return [{"name": r[0], "avg_pod_aging": round(r[1] or 0, 1)} for r in results]
    
    def _get_top_warehouses_by_pending(self, limit: int) -> List[Dict]:
        results = self.db.query(
            DeliveryReport.warehouse, 
            func.count(DeliveryReport.id).label('pending')
        ).filter(
            DeliveryReport.warehouse.isnot(None), 
            DeliveryReport.good_issue_date.is_(None)
        ).group_by(DeliveryReport.warehouse).order_by(desc('pending')).limit(limit).all()
        return [{"name": r[0], "pending": r[1]} for r in results]


class ExecutiveInsightHandler(QueryHandler):
    """Handler for executive insights and control tower"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        intent = processed_query.intent
        
        try:
            if intent == IntentType.EXECUTIVE_INSIGHT:
                cache_key = "executive_insights"
                cached = _cache_system.get("executive", cache_key)
                if cached:
                    return HandlerResult(response=cached)
                
                insights = self._get_executive_insights()
                
                # Try Groq for enhanced summary
                groq = get_groq_service()
                if groq and groq.is_available:
                    groq_summary = groq.generate_executive_summary(insights)
                    if groq_summary:
                        _cache_system.set("executive", cache_key, groq_summary)
                        return HandlerResult(response=groq_summary)
                
                response = self._format_executive_response(insights)
                _cache_system.set("executive", cache_key, response)
                return HandlerResult(response=response)
            
            elif intent == IntentType.CONTROL_TOWER:
                critical = self._get_critical_deliveries(threshold_days=15, limit=10)
                if not critical:
                    return HandlerResult(response="✅ No critical deliveries (>15 days) found.")
                
                lines = ["🚨 *Control Tower - Critical Alerts*", ""]
                lines.append(f"🔴 *{len(critical)} deliveries exceed 15 days*")
                for item in critical[:5]:
                    lines.append(f"   • DN {item['dn']}: {item['dealer']} - {item['aging']} days ({item['warehouse']})")
                return HandlerResult(response="\n".join(lines))
            
            elif intent == IntentType.ROOT_CAUSE:
                insights = self._get_executive_insights()
                groq = get_groq_service()
                if groq and groq.is_available:
                    issue = "High pending PGI and POD rates"
                    result = groq.analyze_root_cause(issue, insights)
                    return HandlerResult(response=result)
                return HandlerResult(response="🔍 Root cause analysis requires Groq integration.")
            
            return HandlerResult(response="❌ Invalid executive query type.")
            
        except Exception as e:
            logger.exception(f"[{self.request_id}] Executive error: {e}")
            return HandlerResult(response="❌ Error generating executive insights. Please try again.")
    
    def _get_executive_insights(self) -> Dict[str, Any]:
        result = self.db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(case((DeliveryReport.good_issue_date.is_(None), 1), else_=0)).label('pending_pgi'),
            func.sum(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label('pending_pod'),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), 
                          func.datediff(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date)), else_=0)).label('avg_delivery_aging')
        ).first()
        
        worst_warehouse = self.db.query(
            DeliveryReport.warehouse, 
            func.count(DeliveryReport.id).label('pending')
        ).filter(
            DeliveryReport.good_issue_date.is_(None), 
            DeliveryReport.warehouse.isnot(None)
        ).group_by(DeliveryReport.warehouse).order_by(desc('pending')).first()
        
        oldest = self.db.query(
            DeliveryReport.dn_no, 
            DeliveryReport.customer_name, 
            DeliveryReport.dn_create_date
        ).filter(
            DeliveryReport.good_issue_date.is_(None), 
            DeliveryReport.dn_create_date.isnot(None)
        ).order_by(DeliveryReport.dn_create_date).first()
        
        insights = {
            "pending_pgi": result.pending_pgi or 0,
            "pending_pod": result.pending_pod or 0,
            "avg_delivery_aging": round(result.avg_delivery_aging or 0, 1),
            "worst_warehouse": worst_warehouse[0] if worst_warehouse else None,
            "oldest_dn": oldest.dn_no if oldest else None,
            "oldest_aging": (self.today - oldest.dn_create_date).days if oldest else 0
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
    
    def _format_executive_response(self, insights: Dict[str, Any]) -> str:
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
    
    def _get_critical_deliveries(self, threshold_days: int = 15, limit: int = 10) -> List[Dict]:
        today = self.today
        results = self.db.query(
            DeliveryReport.dn_no, 
            DeliveryReport.customer_name, 
            DeliveryReport.warehouse,
            func.datediff(today, DeliveryReport.dn_create_date).label('aging')
        ).filter(
            DeliveryReport.good_issue_date.is_(None),
            DeliveryReport.dn_create_date.isnot(None),
            func.datediff(today, DeliveryReport.dn_create_date) > threshold_days
        ).order_by(desc('aging')).limit(limit).all()
        return [{"dn": r[0], "dealer": r[1], "warehouse": r[2], "aging": r[3]} for r in results]


class GeneralAIHandler(QueryHandler):
    """Handler for general AI queries (uses Groq)"""
    
    def handle(self, processed_query: ProcessedQuery, context: Optional[ConversationContext] = None) -> HandlerResult:
        question = processed_query.raw_question or ""
        
        groq = get_groq_service()
        if groq and groq.is_available:
            context_dict = {"last_dealer": context.last_dealer} if context else None
            response = groq.chat(question, context_dict)
            return HandlerResult(response=response)
        
        # Fallback to help
        return HandlerResult(response=_format_help_message())


# ==========================================================
# ROUTER ENGINE
# ==========================================================

class RouterEngine:
    """Intelligent query router"""
    
    def __init__(self):
        self.handler_map = {
            IntentType.HELP: HelpHandler,
            IntentType.DN_LOOKUP: DNLookupHandler,
            IntentType.DEALER_DASHBOARD: DealerDashboardHandler,
            IntentType.DEALER_REVENUE: DealerDashboardHandler,
            IntentType.DEALER_UNITS: DealerDashboardHandler,
            IntentType.DEALER_PERFORMANCE: DealerDashboardHandler,
            IntentType.DEALER_AGING: DealerDashboardHandler,
            IntentType.WAREHOUSE_DASHBOARD: WarehouseDashboardHandler,
            IntentType.WAREHOUSE_PERFORMANCE: WarehouseDashboardHandler,
            IntentType.PENDING_PGI: PendingHandler,
            IntentType.PENDING_POD: PendingHandler,
            IntentType.PGI_AGING: PendingHandler,
            IntentType.POD_AGING: PendingHandler,
            IntentType.TOP_DEALERS: RankingHandler,
            IntentType.TOP_WAREHOUSES: RankingHandler,
            IntentType.EXECUTIVE_INSIGHT: ExecutiveInsightHandler,
            IntentType.CONTROL_TOWER: ExecutiveInsightHandler,
            IntentType.ROOT_CAUSE: ExecutiveInsightHandler,
            IntentType.GENERAL_AI: GeneralAIHandler,
        }
    
    def route(self, processed_query: ProcessedQuery, db: Session, request_id: str, 
              context: Optional[ConversationContext] = None) -> HandlerResult:
        handler_class = self.handler_map.get(processed_query.intent, GeneralAIHandler)
        handler = handler_class(db, request_id)
        return handler.handle(processed_query, context)


_router = RouterEngine()


# ==========================================================
# INTENT CLASSIFICATION ENGINE
# ==========================================================

def _classify_intent(question: str, msg_lower: str, db: Session, 
                     context: Optional[ConversationContext] = None) -> ProcessedQuery:
    """Intelligent intent classification with context awareness"""
    
    # 1. HELP - Highest priority
    if msg_lower in ['help', '/help', 'menu', '?', 'commands', 'what can you do']:
        return ProcessedQuery(intent=IntentType.HELP, confidence=1.0, raw_question=question)
    
    # 2. DN LOOKUP
    dn_match = re.search(r'\b(\d{8,12})\b', question)
    if dn_match:
        return ProcessedQuery(
            intent=IntentType.DN_LOOKUP, 
            entity=dn_match.group(1), 
            entity_type="dn", 
            confidence=1.0,
            raw_question=question
        )
    
    # 3. EXECUTIVE INSIGHT
    if any(kw in msg_lower for kw in ['key issue', 'biggest problem', 'bottleneck', 'executive insight', 'root cause']):
        if 'root cause' in msg_lower or 'why' in msg_lower:
            return ProcessedQuery(intent=IntentType.ROOT_CAUSE, confidence=0.95, raw_question=question)
        return ProcessedQuery(intent=IntentType.EXECUTIVE_INSIGHT, confidence=0.95, raw_question=question)
    
    # 4. CONTROL TOWER
    if any(kw in msg_lower for kw in ['critical', 'alert', 'urgent', 'control tower']):
        return ProcessedQuery(intent=IntentType.CONTROL_TOWER, confidence=0.95, raw_question=question)
    
    # 5. RANKING QUERIES
    if ('top' in msg_lower or 'best' in msg_lower or 'worst' in msg_lower):
        if 'dealer' in msg_lower and ('revenue' in msg_lower or 'units' in msg_lower):
            limit = 5 if 'top 5' in msg_lower else 10
            return ProcessedQuery(
                intent=IntentType.TOP_DEALERS, 
                limit=limit, 
                confidence=0.9,
                raw_question=question
            )
        if 'warehouse' in msg_lower and 'pending' in msg_lower:
            return ProcessedQuery(
                intent=IntentType.TOP_WAREHOUSES, 
                limit=10, 
                confidence=0.9,
                raw_question=question
            )
    
    # 6. PGI QUERIES
    if 'pgi' in msg_lower:
        if 'pending' in msg_lower:
            dealer_name = extract_dealer_from_query(msg_lower, db, context)
            return ProcessedQuery(
                intent=IntentType.PENDING_PGI, 
                entity=dealer_name, 
                entity_type="dealer",
                confidence=0.9,
                raw_question=question
            )
        if 'aging' in msg_lower:
            return ProcessedQuery(intent=IntentType.PGI_AGING, confidence=0.9, raw_question=question)
    
    # 7. POD QUERIES
    if 'pod' in msg_lower:
        if 'pending' in msg_lower:
            dealer_name = extract_dealer_from_query(msg_lower, db, context)
            return ProcessedQuery(
                intent=IntentType.PENDING_POD, 
                entity=dealer_name, 
                entity_type="dealer",
                confidence=0.9,
                raw_question=question
            )
        if 'aging' in msg_lower:
            return ProcessedQuery(intent=IntentType.POD_AGING, confidence=0.9, raw_question=question)
    
    # 8. DEALER QUERIES (with context awareness)
    dealer_name = extract_dealer_from_query(msg_lower, db, context)
    if dealer_name:
        if any(kw in msg_lower for kw in ['revenue', 'sales', 'amount']):
            return ProcessedQuery(
                intent=IntentType.DEALER_REVENUE, 
                entity=dealer_name, 
                entity_type="dealer", 
                metric="revenue",
                confidence=0.9,
                raw_question=question
            )
        elif any(kw in msg_lower for kw in ['units', 'quantity', 'qty']):
            return ProcessedQuery(
                intent=IntentType.DEALER_UNITS, 
                entity=dealer_name, 
                entity_type="dealer", 
                metric="units",
                confidence=0.9,
                raw_question=question
            )
        elif 'performance' in msg_lower or 'kpi' in msg_lower:
            return ProcessedQuery(
                intent=IntentType.DEALER_PERFORMANCE, 
                entity=dealer_name, 
                entity_type="dealer", 
                metric="performance",
                confidence=0.9,
                raw_question=question
            )
        elif 'aging' in msg_lower:
            return ProcessedQuery(
                intent=IntentType.DEALER_AGING, 
                entity=dealer_name, 
                entity_type="dealer", 
                metric="aging",
                confidence=0.9,
                raw_question=question
            )
        else:
            return ProcessedQuery(
                intent=IntentType.DEALER_DASHBOARD, 
                entity=dealer_name, 
                entity_type="dealer", 
                metric="summary",
                confidence=0.85,
                raw_question=question
            )
    
    # 9. WAREHOUSE QUERY
    warehouses = _get_warehouse_list(db)
    for wh in warehouses:
        if wh.lower() in msg_lower:
            if 'performance' in msg_lower or 'kpi' in msg_lower:
                return ProcessedQuery(
                    intent=IntentType.WAREHOUSE_PERFORMANCE, 
                    entity=wh, 
                    entity_type="warehouse",
                    confidence=0.8,
                    raw_question=question
                )
            return ProcessedQuery(
                intent=IntentType.WAREHOUSE_DASHBOARD, 
                entity=wh, 
                entity_type="warehouse",
                confidence=0.8,
                raw_question=question
            )
    
    # 10. GENERAL AI (with context)
    return ProcessedQuery(intent=IntentType.GENERAL_AI, needs_groq=True, confidence=0.5, raw_question=question)


# ==========================================================
# WAREHOUSE LIST (CACHED)
# ==========================================================

_warehouse_cache_list: List[str] = []
_warehouse_cache_time = 0

def _get_warehouse_list(db: Session, force_refresh: bool = False) -> List[str]:
    global _warehouse_cache_list, _warehouse_cache_time
    now = time.time()
    if not force_refresh and _warehouse_cache_list and (now - _warehouse_cache_time) < WAREHOUSE_CACHE_TTL:
        return _warehouse_cache_list
    try:
        warehouses = db.query(DeliveryReport.warehouse).filter(DeliveryReport.warehouse.isnot(None)).distinct().limit(50).all()
        _warehouse_cache_list = [w[0] for w in warehouses if w[0]]
        _warehouse_cache_time = now
        return _warehouse_cache_list
    except Exception:
        return ['lahore', 'karachi', 'islamabad', 'rawalpindi', 'multan', 'faisalabad']


# ==========================================================
# HELP MESSAGE
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


# ==========================================================
# CONVERSATION CONTEXT MANAGEMENT
# ==========================================================

_conversation_cache: Dict[str, ConversationContext] = {}

def get_conversation_context(phone_number: str) -> ConversationContext:
    if phone_number not in _conversation_cache:
        _conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
    context = _conversation_cache[phone_number]
    if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
        context = ConversationContext(phone_number=phone_number)
        _conversation_cache[phone_number] = context
    return context


def update_conversation_context(phone_number: str, intent: IntentType = None, 
                                entity: str = None, entity_type: str = None,
                                context_updates: Dict[str, Any] = None):
    context = get_conversation_context(phone_number)
    if intent:
        context.last_intent = intent.value
    if entity_type == "dealer" and entity:
        context.last_dealer = entity
        context.last_entity = entity
        context.last_entity_type = "dealer"
    elif entity_type == "warehouse" and entity:
        context.last_warehouse = entity
        context.last_entity = entity
        context.last_entity_type = "warehouse"
    elif entity_type == "dn" and entity:
        context.last_dn = entity
        context.last_entity = entity
        context.last_entity_type = "dn"
    
    if context_updates:
        for key, value in context_updates.items():
            if hasattr(context, key):
                setattr(context, key, value)
    
    context.message_count += 1
    context.last_updated = time.time()
    _conversation_cache[phone_number] = context


# ==========================================================
# MAIN ENTRY POINT (PRESERVED SIGNATURE - CRITICAL)
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    """
    MAIN ENTRY POINT - PRESERVED SIGNATURE
    DO NOT CHANGE PARAMETERS OR RETURN TYPE
    
    This is called by webhook.py and MUST remain 100% compatible.
    """
    start_time = time.time()
    req_id = request_id or str(uuid.uuid4())[:8]
    
    # Structured logging
    logger.bind(
        request_id=req_id,
        phone=phone_number[:4] + "****" if phone_number else None
    ).info(f"Processing: {question[:100]}")
    
    db = None
    try:
        db = session_factory() if session_factory else SessionLocal()
        msg_lower = question.lower().strip()
        
        # Load conversation context
        context = get_conversation_context(phone_number) if phone_number else None
        
        # Check query cache
        cache_key = hashlib.md5(f"{phone_number}:{question}".lower().encode()).hexdigest()
        cached = _cache_system.get("query", cache_key)
        if cached:
            logger.bind(request_id=req_id).info(f"Cache hit: {question[:50]}")
            return cached
        
        # Classify intent
        processed = _classify_intent(question, msg_lower, db, context)
        logger.bind(request_id=req_id).info(f"Intent: {processed.intent.value}, Confidence: {processed.confidence}")
        
        # Route to handler
        result = _router.route(processed, db, req_id, context)
        response = result.response
        
        # Cache if applicable
        if result.cache_key:
            _cache_system.set("query", result.cache_key, response)
        else:
            _cache_system.set("query", cache_key, response)
        
        # Update conversation context
        if phone_number and response:
            update_conversation_context(
                phone_number, 
                processed.intent, 
                processed.entity, 
                processed.entity_type,
                result.context_updates
            )
        
        duration_ms = int((time.time() - start_time) * 1000)
        logger.bind(request_id=req_id).info(f"Done in {duration_ms}ms")
        return response
        
    except Exception as e:
        logger.exception(f"[{req_id}] Fatal error: {e}")
        return "❌ I encountered an error. Please try again or type 'Help'."
    finally:
        if db:
            db.close()


# ==========================================================
# ADMIN / MONITORING FUNCTIONS
# ==========================================================

def get_ai_service_metrics() -> Dict[str, Any]:
    """Get AI service performance metrics"""
    return {
        "cache_metrics": _cache_system.get_metrics(),
        "conversation_count": len(_conversation_cache),
        "groq_enabled": GROQ_ENABLED,
        "groq_available": bool(_groq_service and _groq_service.is_available),
        "version": "7.0"
    }


def clear_ai_cache():
    """Clear all caches (admin function)"""
    _cache_system.dealer_cache.clear()
    _cache_system.warehouse_cache.clear()
    _cache_system.executive_cache.clear()
    _cache_system.ranking_cache.clear()
    _cache_system.query_cache.clear()
    _conversation_cache.clear()
    return {"status": "cleared", "version": "7.0"}


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("AI Provider Service v7.0 - Enterprise Orchestrator")
logger.info("=" * 60)
logger.info(f"  Groq: {'✅' if GROQ_ENABLED else '❌'}")
logger.info(f"  Cache TTL: {CACHE_TTL_SECONDS}s")
logger.info(f"  Context TTL: {CONTEXT_TTL_SECONDS}s")
logger.info(f"  Handlers: {len(_router.handler_map)}")
logger.info("=" * 60)
