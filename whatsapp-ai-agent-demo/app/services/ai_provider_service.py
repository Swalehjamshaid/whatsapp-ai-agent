
# ==========================================================
# FILE: app/services/ai_provider_service.py (WORKING v3.0 - COMPLETE)
# PURPOSE: AI Provider Service - Natural Language Query Processing
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
from sqlalchemy import func, and_, or_, desc
from sqlalchemy.orm import Session

from app.models import DeliveryReport
from app.database import SessionLocal
from app.config import config

# ==========================================================
# CONFIGURATION
# ==========================================================

GROQ_API_KEY = getattr(config, 'GROQ_API_KEY', '')
CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
PROCESSING_TIMEOUT_SECONDS = 20

# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType(Enum):
    HELP = "help"
    DN_QUERY = "dn_query"
    DEALER_QUERY = "dealer_query"
    WAREHOUSE_QUERY = "warehouse_query"
    PGI_QUERY = "pgi_query"
    POD_QUERY = "pod_query"
    CONTROL_TOWER = "control_tower"
    ANALYTICS = "analytics"
    EXECUTIVE_INSIGHT = "executive_insight"
    RANKING_QUERY = "ranking_query"
    TREND_QUERY = "trend_query"
    GENERAL_AI = "general_ai"
    UNKNOWN = "unknown"


@dataclass
class ProcessedQuery:
    intent: IntentType
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    metric: Optional[str] = None
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
# CACHES
# ==========================================================

_conversation_cache: Dict[str, ConversationContext] = {}
_query_cache = TTLCache(maxsize=1000, ttl=CACHE_TTL_SECONDS)


def get_conversation_context(phone_number: str) -> ConversationContext:
    if phone_number not in _conversation_cache:
        _conversation_cache[phone_number] = ConversationContext(phone_number=phone_number)
    
    context = _conversation_cache[phone_number]
    if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
        context = ConversationContext(phone_number=phone_number)
        _conversation_cache[phone_number] = context
    
    return context


def update_conversation_context(phone_number: str, intent: IntentType = None, 
                                entity: str = None, entity_type: str = None):
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
# FORMAT HELP MESSAGE
# ==========================================================

def _format_help_message() -> str:
    return """📋 *AI Logistics Assistant - Help*

*DN Tracking:*
• Send any 10+ digit DN number

*Dealer Queries:*
• "Show dealer ABC Traders"
• "ABC Traders revenue"
• "ABC Traders pending deliveries"

*Warehouse Queries:*
• "Lahore warehouse summary"
• "Karachi pending PGI"

*Executive Insights:*
• "What is the key issue?"

*Rankings:*
• "Top 10 dealers by revenue"

Need help? Just ask! 🤖"""


def _get_warehouse_list(db: Session) -> List[str]:
    try:
        warehouses = db.query(DeliveryReport.warehouse).filter(
            DeliveryReport.warehouse.isnot(None)
        ).distinct().limit(50).all()
        return [w[0] for w in warehouses if w[0]]
    except Exception:
        return ['lahore', 'karachi', 'rawalpindi', 'islamabad', 'multan', 'faisalabad']


# ==========================================================
# DN QUERY HANDLER
# ==========================================================

def _handle_dn_query(db: Session, dn_number: str, today: date, req_id: str) -> str:
    try:
        record = db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_number
        ).first()
        
        if not record and dn_number.isdigit():
            record = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == f"{dn_number}.0"
            ).first()
        
        if not record:
            record = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.like(f"%{dn_number}%")
            ).first()
        
        if not record:
            return f"❌ DN {dn_number} not found in our system."
        
        delivery_aging = None
        pending_delivery_aging = None
        pod_aging = None
        pending_pod_aging = None
        
        if record.dn_create_date and record.good_issue_date:
            delivery_aging = (record.good_issue_date - record.dn_create_date).days
        elif record.dn_create_date and not record.good_issue_date:
            pending_delivery_aging = (today - record.dn_create_date).days
        
        if record.good_issue_date and record.pod_date:
            pod_aging = (record.pod_date - record.good_issue_date).days
        elif record.good_issue_date and not record.pod_date:
            pending_pod_aging = (today - record.good_issue_date).days
        
        lines = [f"📄 *DN: {dn_number}*", ""]
        lines.append(f"🏪 *Dealer:* {record.customer_name or 'N/A'}")
        lines.append(f"🏭 *Warehouse:* {record.warehouse or 'N/A'}")
        lines.append(f"🌆 *City:* {record.ship_to_city or 'N/A'}")
        lines.append("")
        lines.append(f"📦 *Units:* {int(record.dn_qty or 0):,}")
        lines.append(f"💰 *Amount:* PKR {float(record.dn_amount or 0):,.0f}")
        lines.append("")
        lines.append(f"📅 *DN Date:* {record.dn_create_date.strftime('%Y-%m-%d') if record.dn_create_date else 'N/A'}")
        lines.append(f"🚚 *PGI Date:* {record.good_issue_date.strftime('%Y-%m-%d') if record.good_issue_date else 'Pending'}")
        lines.append(f"📎 *POD Date:* {record.pod_date.strftime('%Y-%m-%d') if record.pod_date else 'Pending'}")
        lines.append("")
        
        if delivery_aging is not None:
            emoji = "✅" if delivery_aging <= 7 else "⚠️" if delivery_aging <= 15 else "🔴"
            lines.append(f"{emoji} *Delivery Aging:* {delivery_aging} days")
        if pending_delivery_aging is not None:
            emoji = "⚠️" if pending_delivery_aging <= 15 else "🔴"
            lines.append(f"{emoji} *Pending Delivery:* {pending_delivery_aging} days (No PGI)")
        if pod_aging is not None:
            emoji = "✅" if pod_aging <= 7 else "⚠️" if pod_aging <= 15 else "🔴"
            lines.append(f"{emoji} *POD Aging:* {pod_aging} days")
        if pending_pod_aging is not None:
            emoji = "⚠️" if pending_pod_aging <= 15 else "🔴"
            lines.append(f"{emoji} *Pending POD:* {pending_pod_aging} days (PGI Done)")
        
        lines.append("")
        lines.append(f"📊 *Status:* {record.delivery_status or 'Unknown'}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] DN query error: {e}")
        return f"❌ Error looking up DN {dn_number}"


# ==========================================================
# WAREHOUSE QUERY HANDLER
# ==========================================================

def _handle_warehouse_query(db: Session, warehouse_name: str, today: date, req_id: str) -> str:
    try:
        result = db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(DeliveryReport.dn_qty).label('total_units'),
            func.sum(DeliveryReport.dn_amount).label('total_revenue')
        ).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
        ).first()
        
        pending_delivery = db.query(DeliveryReport).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
            DeliveryReport.good_issue_date.is_(None)
        ).count()
        
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        ).count()
        
        pgi_completed = db.query(DeliveryReport).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
            DeliveryReport.good_issue_date.isnot(None)
        ).count()
        
        lines = [f"🏭 *Warehouse: {warehouse_name.title()}*", ""]
        lines.append(f"📄 *Total DNs:* {result.total_dns or 0:,}")
        lines.append(f"📦 *Total Units:* {int(result.total_units or 0):,}")
        lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
        lines.append("")
        lines.append(f"✅ *PGI Completed:* {pgi_completed}")
        lines.append(f"⏳ *Pending Delivery:* {pending_delivery}")
        lines.append(f"📎 *Pending POD:* {pending_pod}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Warehouse query error: {e}")
        return f"❌ Error fetching {warehouse_name} warehouse data"


# ==========================================================
# DEALER SUMMARY QUERY HANDLER
# ==========================================================

def _extract_dealer_name(question: str, msg_lower: str) -> Optional[str]:
    dealer_match = re.search(r'dealer\s+([a-z0-9\s&]+)', msg_lower)
    if dealer_match:
        return dealer_match.group(1).strip()
    
    show_match = re.search(r'show\s+([a-z0-9\s&]+)', msg_lower)
    if show_match:
        return show_match.group(1).strip()
    
    if len(msg_lower.split()) <= 5:
        return msg_lower
    
    return None


def _handle_dealer_summary_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        if not dealer_name:
            return f"❌ Please specify a dealer name. Example: 'Show dealer ABC Traders'"
        
        exact_match = db.query(DeliveryReport).filter(
            func.lower(DeliveryReport.customer_name) == dealer_name.lower()
        ).first()
        
        if exact_match:
            dealer_name = exact_match.customer_name
        else:
            records = db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).limit(20).all()
            
            if not records:
                return f"❌ No dealer found matching '{dealer_name}'. Try a different name or type 'Help'."
            
            dealer_name = records[0].customer_name
        
        result = db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(DeliveryReport.dn_qty).label('total_units'),
            func.sum(DeliveryReport.dn_amount).label('total_revenue')
        ).filter(
            DeliveryReport.customer_name == dealer_name
        ).first()
        
        pending_delivery = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name == dealer_name,
            DeliveryReport.good_issue_date.is_(None)
        ).count()
        
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name == dealer_name,
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        ).count()
        
        pgi_completed = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name == dealer_name,
            DeliveryReport.good_issue_date.isnot(None)
        ).count()
        
        lines = [f"🏪 *Dealer: {dealer_name}*", ""]
        lines.append(f"📄 *Total DNs:* {result.total_dns or 0:,}")
        lines.append(f"📦 *Total Units:* {int(result.total_units or 0):,}")
        lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
        lines.append("")
        lines.append(f"✅ *PGI Completed:* {pgi_completed}")
        lines.append(f"⏳ *Pending Delivery:* {pending_delivery}")
        lines.append(f"📎 *Pending POD:* {pending_pod}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Dealer summary error: {e}")
        return f"❌ Error fetching dealer data for '{question}'"


# ==========================================================
# DEALER REVENUE QUERY HANDLER
# ==========================================================

def _handle_dealer_revenue_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name. Example: 'ABC Traders revenue'"
        
        result = db.query(
            func.sum(DeliveryReport.dn_amount).label('total_revenue')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        total_revenue = float(result.total_revenue or 0)
        
        return f"💰 *Revenue for {dealer_name.title()}:* PKR {total_revenue:,.0f}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Revenue query error: {e}")
        return f"❌ Error fetching revenue"


# ==========================================================
# DEALER UNITS QUERY HANDLER
# ==========================================================

def _handle_dealer_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name. Example: 'ABC Traders units'"
        
        result = db.query(
            func.sum(DeliveryReport.dn_qty).label('total_units')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        total_units = int(result.total_units or 0)
        
        return f"📦 *Units for {dealer_name.title()}:* {total_units:,}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Units query error: {e}")
        return f"❌ Error fetching units"


# ==========================================================
# DEALER DN COUNT QUERY HANDLER
# ==========================================================

def _handle_dealer_dn_count_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name. Example: 'ABC Traders DN count'"
        
        result = db.query(
            func.count(DeliveryReport.id).label('total_dns')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        total_dns = result.total_dns or 0
        
        return f"📄 *DN Count for {dealer_name.title()}:* {total_dns:,}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] DN count query error: {e}")
        return f"❌ Error fetching DN count"


# ==========================================================
# DEALER PERFORMANCE QUERY HANDLER
# ==========================================================

def _handle_dealer_performance_query(db: Session, question: str, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        if not dealer_name:
            return "❌ Please specify a dealer name. Example: 'ABC Traders performance'"
        
        result = db.query(
            func.count(DeliveryReport.id).label('total_dns'),
            func.sum(DeliveryReport.dn_qty).label('total_units'),
            func.sum(DeliveryReport.dn_amount).label('total_revenue')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        pending_delivery = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
            DeliveryReport.good_issue_date.is_(None)
        ).count()
        
        pgi_completed = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
            DeliveryReport.good_issue_date.isnot(None)
        ).count()
        
        pod_completed = db.query(DeliveryReport).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
            DeliveryReport.pod_date.isnot(None)
        ).count()
        
        total = result.total_dns or 1
        delivery_rate = (pgi_completed / total) * 100 if total > 0 else 0
        pod_rate = (pod_completed / pgi_completed) * 100 if pgi_completed > 0 else 0
        
        lines = [f"📊 *Performance Dashboard: {dealer_name.title()}*", ""]
        lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
        lines.append(f"📦 *Units:* {int(result.total_units or 0):,}")
        lines.append(f"📄 *Total DNs:* {result.total_dns or 0}")
        lines.append("")
        lines.append(f"🚚 *Delivery Rate:* {delivery_rate:.1f}%")
        lines.append(f"📎 *POD Rate:* {pod_rate:.1f}%")
        lines.append(f"⏳ *Pending Delivery:* {pending_delivery}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Dealer performance error: {e}")
        return f"❌ Error fetching performance data"


# ==========================================================
# PGI QUERY HANDLERS
# ==========================================================

def _handle_pgi_pending_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.is_(None))
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        pending_count = query.count()
        
        lines = ["⏳ *PGI Pending Report*", ""]
        lines.append(f"📊 *Total Pending PGI:* {pending_count}")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI pending error: {e}")
        return "❌ Error fetching PGI pending data"


def _handle_pgi_aging_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        records = query.limit(100).all()
        
        if records:
            avg_aging = sum((today - r.good_issue_date).days for r in records) / len(records)
            avg_aging = round(avg_aging, 1)
            max_aging = max((today - r.good_issue_date).days for r in records)
        else:
            avg_aging = 0
            max_aging = 0
        
        lines = ["⏰ *PGI Aging Report*", ""]
        lines.append(f"📊 *Pending POD after PGI:* {len(records)}")
        lines.append(f"⏰ *Average PGI Aging:* {avg_aging} days")
        lines.append(f"🔴 *Maximum PGI Aging:* {max_aging} days")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI aging error: {e}")
        return "❌ Error fetching PGI aging data"


def _handle_pgi_rate_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        total = db.query(DeliveryReport).count()
        pgi_done = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.isnot(None)).count()
        
        if total > 0:
            rate = (pgi_done / total) * 100
        else:
            rate = 0
        
        return f"📊 *PGI Completion Rate:* {rate:.1f}% ({pgi_done:,}/{total:,})"
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI rate error: {e}")
        return "❌ Error fetching PGI rate"


# ==========================================================
# POD QUERY HANDLERS
# ==========================================================

def _handle_pod_pending_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        pending_count = query.count()
        
        lines = ["📎 *POD Pending Report*", ""]
        lines.append(f"📊 *Total Pending POD:* {pending_count}")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD pending error: {e}")
        return "❌ Error fetching POD pending data"


def _handle_pod_aging_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        records = query.limit(100).all()
        
        if records:
            avg_aging = sum((today - r.good_issue_date).days for r in records) / len(records)
            avg_aging = round(avg_aging, 1)
            max_aging = max((today - r.good_issue_date).days for r in records)
        else:
            avg_aging = 0
            max_aging = 0
        
        lines = ["⏰ *POD Aging Report*", ""]
        lines.append(f"📊 *Pending POD after PGI:* {len(records)}")
        lines.append(f"⏰ *Average POD Aging:* {avg_aging} days")
        lines.append(f"🔴 *Maximum POD Aging:* {max_aging} days")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD aging error: {e}")
        return "❌ Error fetching POD aging data"


def _handle_pod_rate_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        pgi_done = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.isnot(None)).count()
        pod_done = db.query(DeliveryReport).filter(DeliveryReport.pod_date.isnot(None)).count()
        
        if pgi_done > 0:
            rate = (pod_done / pgi_done) * 100
        else:
            rate = 0
        
        return f"📊 *POD Completion Rate:* {rate:.1f}% ({pod_done:,}/{pgi_done:,})"
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD rate error: {e}")
        return "❌ Error fetching POD rate"


# ==========================================================
# DELIVERED UNITS QUERY HANDLER
# ==========================================================

def _handle_delivered_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        query = db.query(func.sum(DeliveryReport.dn_qty)).filter(
            DeliveryReport.delivery_status == "Delivered"
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        delivered_units = int(query.scalar() or 0)
        
        if dealer_name:
            return f"✅ *Delivered Units for {dealer_name.title()}:* {delivered_units:,}"
        else:
            return f"✅ *Total Delivered Units:* {delivered_units:,}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Delivered units error: {e}")
        return "❌ Error fetching delivered units"


# ==========================================================
# TRANSIT UNITS QUERY HANDLER
# ==========================================================

def _handle_transit_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        query = db.query(func.sum(DeliveryReport.dn_qty)).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        transit_units = int(query.scalar() or 0)
        
        if dealer_name:
            return f"🚚 *Transit Units for {dealer_name.title()}:* {transit_units:,}"
        else:
            return f"🚚 *Total Transit Units:* {transit_units:,}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Transit units error: {e}")
        return "❌ Error fetching transit units"


# ==========================================================
# DELIVERY AGING QUERY HANDLER
# ==========================================================

def _handle_delivery_aging_query(db: Session, question: str, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        query = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.dn_create_date.isnot(None)
        )
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        records = query.limit(100).all()
        
        if records:
            avg_aging = sum((r.good_issue_date - r.dn_create_date).days for r in records) / len(records)
            avg_aging = round(avg_aging, 1)
            max_aging = max((r.good_issue_date - r.dn_create_date).days for r in records)
        else:
            avg_aging = 0
            max_aging = 0
        
        if dealer_name:
            return f"⏰ *Delivery Aging for {dealer_name.title()}:* Avg {avg_aging} days, Max {max_aging} days"
        else:
            return f"⏰ *Overall Delivery Aging:* Avg {avg_aging} days, Max {max_aging} days"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Delivery aging error: {e}")
        return "❌ Error fetching delivery aging data"


# ==========================================================
# PENDING DELIVERY QUERY HANDLER
# ==========================================================

def _handle_pending_delivery_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.is_(None))
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        pending_count = query.count()
        
        aging_records = query.filter(DeliveryReport.dn_create_date.isnot(None)).limit(100).all()
        if aging_records:
            avg_aging = sum((today - r.dn_create_date).days for r in aging_records) / len(aging_records)
            avg_aging = round(avg_aging, 1)
            oldest = max((today - r.dn_create_date).days for r in aging_records)
        else:
            avg_aging = 0
            oldest = 0
        
        lines = ["⏳ *Pending Delivery Report*", ""]
        lines.append(f"📊 *Total Pending:* {pending_count}")
        lines.append(f"⏰ *Average Aging:* {avg_aging} days")
        lines.append(f"🔴 *Oldest Pending:* {oldest} days")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Pending delivery error: {e}")
        return "❌ Error fetching pending delivery data"


# ==========================================================
# CONTROL TOWER QUERY HANDLER
# ==========================================================

def _handle_control_tower(db: Session, today: date, req_id: str) -> str:
    try:
        critical_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.is_(None),
            DeliveryReport.dn_create_date.isnot(None)
        ).all()
        
        critical_list = []
        for r in critical_deliveries:
            aging = (today - r.dn_create_date).days
            if aging > 15:
                critical_list.append({
                    'dn': r.dn_no,
                    'dealer': r.customer_name,
                    'aging': aging
                })
        
        critical_list = sorted(critical_list, key=lambda x: x['aging'], reverse=True)[:5]
        
        dealer_delays = db.query(
            DeliveryReport.customer_name,
            func.count(DeliveryReport.id).label('pending_count')
        ).filter(
            DeliveryReport.good_issue_date.is_(None),
            DeliveryReport.customer_name.isnot(None)
        ).group_by(DeliveryReport.customer_name).order_by(func.count(DeliveryReport.id).desc()).limit(5).all()
        
        lines = ["🚨 *Control Tower - Critical Alerts*", ""]
        
        if critical_list:
            lines.append("🔴 *Critical Deliveries (>15 days)*")
            for item in critical_list[:3]:
                lines.append(f"   • DN {item['dn']}: {item['dealer']} - {item['aging']} days")
        else:
            lines.append("✅ No critical delivery alerts")
        
        lines.append("")
        lines.append("📊 *Top 5 Dealers with Most Pending*")
        for dealer, count in dealer_delays[:3]:
            lines.append(f"   • {dealer}: {count} pending")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Control tower error: {e}")
        return "❌ Error generating control tower report"


# ==========================================================
# RANKING QUERY HANDLER
# ==========================================================

def _handle_ranking_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        if 'dealer' in msg_lower and 'revenue' in msg_lower:
            limit = 10
            if 'top 5' in msg_lower:
                limit = 5
            elif 'top 3' in msg_lower:
                limit = 3
            
            results = db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('total_revenue')
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.dn_amount.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(
                desc('total_revenue')
            ).limit(limit).all()
            
            lines = [f"🏆 *Top {limit} Dealers by Revenue*", ""]
            for i, (name, revenue) in enumerate(results, 1):
                rev = float(revenue or 0)
                lines.append(f"{i}. {name}: PKR {rev:,.0f}")
            return "\n".join(lines)
        
        elif 'dealer' in msg_lower and 'units' in msg_lower:
            limit = 10
            if 'top 5' in msg_lower:
                limit = 5
            
            results = db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_qty).label('total_units')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(DeliveryReport.customer_name).order_by(
                desc('total_units')
            ).limit(limit).all()
            
            lines = [f"🏆 *Top {limit} Dealers by Units*", ""]
            for i, (name, units) in enumerate(results, 1):
                lines.append(f"{i}. {name}: {int(units or 0):,} units")
            return "\n".join(lines)
        
        elif 'warehouse' in msg_lower and 'pending' in msg_lower:
            results = db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.id).label('pending_count')
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.good_issue_date.is_(None)
            ).group_by(DeliveryReport.warehouse).order_by(
                desc('pending_count')
            ).limit(10).all()
            
            lines = ["🏭 *Warehouses with Most Pending*", ""]
            for i, (name, count) in enumerate(results, 1):
                lines.append(f"{i}. {name}: {count} pending")
            return "\n".join(lines)
        
        return "📊 Please specify: 'Top 10 dealers by revenue' or 'Top warehouses by pending'"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Ranking error: {e}")
        return "❌ Error fetching rankings"


# ==========================================================
# EXECUTIVE INSIGHT HANDLER
# ==========================================================

def _generate_executive_insight(db: Session) -> str:
    try:
        today = date.today()
        
        total_pending_pgi = db.query(func.count(DeliveryReport.id)).filter(
            DeliveryReport.good_issue_date.is_(None)
        ).scalar() or 0
        
        total_pending_pod = db.query(func.count(DeliveryReport.id)).filter(
            DeliveryReport.good_issue_date.isnot(None),
            DeliveryReport.pod_date.is_(None)
        ).scalar() or 0
        
        total = db.query(func.count(DeliveryReport.id)).scalar() or 1
        pgi_done = db.query(func.count(DeliveryReport.id)).filter(
            DeliveryReport.good_issue_date.isnot(None)
        ).scalar() or 0
        pgi_rate = (pgi_done / total) * 100
        
        lines = ["🚨 *Executive Insight*", ""]
        lines.append(f"📊 *PGI Rate:* {pgi_rate:.1f}% ({pgi_done:,}/{total:,})")
        lines.append(f"⏳ *Pending PGI:* {total_pending_pgi}")
        lines.append(f"📎 *Pending POD:* {total_pending_pod}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.error(f"Executive insight error: {e}")
        return "📊 I'm analyzing the data. Please check back shortly."


# ==========================================================
# INTENT CLASSIFICATION
# ==========================================================

def _classify_intent(question: str, msg_lower: str, db: Session, 
                     context: ConversationContext) -> ProcessedQuery:
    
    if msg_lower in ['help', '/help', 'menu', '?', 'commands']:
        return ProcessedQuery(intent=IntentType.HELP, confidence=1.0)
    
    dn_match = re.search(r'\b(\d{8,12})\b', question)
    if dn_match:
        return ProcessedQuery(intent=IntentType.DN_QUERY, entity=dn_match.group(1),
                            entity_type="dn", confidence=1.0)
    
    if any(kw in msg_lower for kw in ['key issue', 'biggest problem', 'bottleneck', 'executive insight']):
        return ProcessedQuery(intent=IntentType.EXECUTIVE_INSIGHT, confidence=0.95)
    
    if ('top' in msg_lower or 'best' in msg_lower) and ('dealer' in msg_lower or 'warehouse' in msg_lower):
        return ProcessedQuery(intent=IntentType.RANKING_QUERY, confidence=0.9)
    
    if any(kw in msg_lower for kw in ['critical', 'alert', 'urgent', 'control tower']):
        return ProcessedQuery(intent=IntentType.CONTROL_TOWER, confidence=0.95)
    
    warehouses = _get_warehouse_list(db)
    for wh in warehouses:
        if wh.lower() in msg_lower:
            return ProcessedQuery(intent=IntentType.WAREHOUSE_QUERY, entity=wh,
                                entity_type="warehouse", confidence=0.85)
    
    if 'pgi' in msg_lower:
        if 'pending' in msg_lower:
            return ProcessedQuery(intent=IntentType.PGI_QUERY, confidence=0.9,
                                context_updates={"pgi_type": "pending"})
        elif 'aging' in msg_lower:
            return ProcessedQuery(intent=IntentType.PGI_QUERY, confidence=0.9,
                                context_updates={"pgi_type": "aging"})
    
    if 'pod' in msg_lower:
        if 'pending' in msg_lower:
            return ProcessedQuery(intent=IntentType.POD_QUERY, confidence=0.9,
                                context_updates={"pod_type": "pending"})
        elif 'aging' in msg_lower:
            return ProcessedQuery(intent=IntentType.POD_QUERY, confidence=0.9,
                                context_updates={"pod_type": "aging"})
    
    if any(kw in msg_lower for kw in ['revenue', 'sales', 'amount']):
        return ProcessedQuery(intent=IntentType.DEALER_QUERY, metric="revenue", confidence=0.85)
    
    if any(kw in msg_lower for kw in ['units', 'quantity', 'qty']):
        return ProcessedQuery(intent=IntentType.DEALER_QUERY, metric="units", confidence=0.85)
    
    if 'performance' in msg_lower or 'kpi' in msg_lower:
        return ProcessedQuery(intent=IntentType.DEALER_QUERY, metric="performance", confidence=0.85)
    
    return ProcessedQuery(intent=IntentType.DEALER_QUERY, confidence=0.7)


# ==========================================================
# MAIN ENTRY POINT
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
    
    logger.info(f"[{req_id}] User={phone_number} Question={question[:200]}")
    
    db = None
    
    try:
        if session_factory:
            db = session_factory()
        else:
            db = SessionLocal()
        
        msg_lower = question.lower().strip()
        today = date.today()
        
        context = get_conversation_context(phone_number) if phone_number else None
        
        cache_key = get_cache_key(question, phone_number)
        if cache_key in _query_cache:
            logger.info(f"[{req_id}] Cache hit")
            return _query_cache[cache_key]
        
        processed = _classify_intent(question, msg_lower, db, context)
        logger.info(f"[{req_id}] Intent: {processed.intent.value}")
        
        response = None
        
        if processed.intent == IntentType.HELP:
            response = _format_help_message()
        
        elif processed.intent == IntentType.DN_QUERY:
            response = _handle_dn_query(db, processed.entity, today, req_id)
        
        elif processed.intent == IntentType.WAREHOUSE_QUERY:
            response = _handle_warehouse_query(db, processed.entity, today, req_id)
        
        elif processed.intent == IntentType.DEALER_QUERY:
            if processed.metric == "revenue":
                response = _handle_dealer_revenue_query(db, question, msg_lower, req_id)
            elif processed.metric == "units":
                response = _handle_dealer_units_query(db, question, msg_lower, req_id)
            elif processed.metric == "performance":
                response = _handle_dealer_performance_query(db, question, msg_lower, today, req_id)
            else:
                response = _handle_dealer_summary_query(db, question, msg_lower, req_id)
        
        elif processed.intent == IntentType.PGI_QUERY:
            pgi_type = processed.context_updates.get("pgi_type", "pending")
            if pgi_type == "pending":
                response = _handle_pgi_pending_query(db, msg_lower, today, req_id)
            elif pgi_type == "aging":
                response = _handle_pgi_aging_query(db, msg_lower, today, req_id)
            else:
                response = _handle_pgi_rate_query(db, msg_lower, req_id)
        
        elif processed.intent == IntentType.POD_QUERY:
            pod_type = processed.context_updates.get("pod_type", "pending")
            if pod_type == "pending":
                response = _handle_pod_pending_query(db, msg_lower, today, req_id)
            elif pod_type == "aging":
                response = _handle_pod_aging_query(db, msg_lower, today, req_id)
            else:
                response = _handle_pod_rate_query(db, msg_lower, req_id)
        
        elif processed.intent == IntentType.CONTROL_TOWER:
            response = _handle_control_tower(db, today, req_id)
        
        elif processed.intent == IntentType.EXECUTIVE_INSIGHT:
            response = _generate_executive_insight(db)
        
        elif processed.intent == IntentType.RANKING_QUERY:
            response = _handle_ranking_query(db, msg_lower, req_id)
        
        else:
            response = _format_help_message()
        
        if phone_number and response:
            update_conversation_context(phone_number, processed.intent, 
                                       processed.entity, processed.entity_type)
        
        if processed.intent not in [IntentType.PGI_QUERY, IntentType.POD_QUERY, 
                                     IntentType.CONTROL_TOWER, IntentType.EXECUTIVE_INSIGHT]:
            _query_cache[cache_key] = response
        
        return response
        
    except Exception as e:
        logger.exception(f"[{req_id}] Query Processing Failed: {question[:100]}")
        return f"❌ I encountered an error processing your request. Please try again or type 'Help'."
    
    finally:
        if db:
            db.close()
        
        elapsed = time.time() - start_time
        logger.info(f"[{req_id}] Query processed in {elapsed:.2f}s")
