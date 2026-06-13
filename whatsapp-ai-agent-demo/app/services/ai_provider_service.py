# ==========================================================
# FILE: app/services/ai_provider_service.py
# PURPOSE: AI Provider Service - Natural Language Query Processing
# ==========================================================

import re
import time
import uuid  # <-- CRITICAL FIX: Added missing uuid import
from datetime import datetime, date
from typing import Optional, Callable, Any, Dict, List, Tuple
from loguru import logger
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import Session

from app.models import DeliveryReport
from app.database import SessionLocal
from app.config import config


def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    """
    Enterprise-grade WhatsApp query processor for logistics.
    
    Answers:
    - Dealer queries (summary, revenue, units, DNs, pending, aging)
    - Warehouse queries (summary, revenue, backlog, aging, PGI, POD)
    - DN queries (status, aging, timeline)
    - Control Tower queries (critical delays, top performers)
    - PGI/POD analytics
    """
    
    # ==========================================================
    # Request Timing and Logging
    # ==========================================================
    start_time = time.time()
    req_id = request_id or str(uuid.uuid4())[:8]
    
    logger.info(f"[{req_id}] User={phone_number} Question={question[:200]}")
    
    db = None
    
    try:
        # ==========================================================
        # Fix Session Factory
        # ==========================================================
        if session_factory:
            db = session_factory()
        else:
            db = SessionLocal()
        
        msg_lower = question.lower().strip()
        today = date.today()
        
        # ==========================================================
        # 1. HELP COMMAND
        # ==========================================================
        if msg_lower in ['help', '/help', 'menu', '?', 'commands']:
            return _format_help_message()
        
        # ==========================================================
        # 2. DN NUMBER QUERY
        # ==========================================================
        dn_match = re.search(r'\b(\d{8,12})\b', question)
        if dn_match:
            dn_number = dn_match.group(1)
            return _handle_dn_query(db, dn_number, today, req_id)
        
        # ==========================================================
        # 3. WAREHOUSE QUERY
        # ==========================================================
        warehouses = _get_warehouse_list(db)
        for wh in warehouses:
            if wh.lower() in msg_lower:
                return _handle_warehouse_query(db, wh, today, req_id)
        
        # ==========================================================
        # 4. CONTROL TOWER QUERIES
        # ==========================================================
        if any(word in msg_lower for word in ['critical', 'alert', 'urgent', 'control tower', 'control-tower']):
            return _handle_control_tower(db, today, req_id)
        
        # ==========================================================
        # 5. PENDING DELIVERY QUERIES
        # ==========================================================
        if 'pending' in msg_lower and ('delivery' in msg_lower or 'pgi' in msg_lower):
            return _handle_pending_delivery_query(db, msg_lower, today, req_id)
        
        # ==========================================================
        # 6. PGI QUERIES
        # ==========================================================
        if 'pgi' in msg_lower:
            if 'pending' in msg_lower:
                return _handle_pgi_pending_query(db, msg_lower, today, req_id)
            elif 'aging' in msg_lower:
                return _handle_pgi_aging_query(db, msg_lower, today, req_id)
            elif 'rate' in msg_lower or 'percentage' in msg_lower or 'completion' in msg_lower:
                return _handle_pgi_rate_query(db, msg_lower, req_id)
        
        # ==========================================================
        # 7. POD QUERIES
        # ==========================================================
        if 'pod' in msg_lower:
            if 'pending' in msg_lower:
                return _handle_pod_pending_query(db, msg_lower, today, req_id)
            elif 'aging' in msg_lower:
                return _handle_pod_aging_query(db, msg_lower, today, req_id)
            elif 'rate' in msg_lower or 'percentage' in msg_lower or 'completion' in msg_lower:
                return _handle_pod_rate_query(db, msg_lower, req_id)
        
        # ==========================================================
        # 8. DEALER KPI QUERIES
        # ==========================================================
        if any(word in msg_lower for word in ['revenue', 'sales', 'amount', 'value']):
            return _handle_dealer_revenue_query(db, question, msg_lower, req_id)
        
        if any(word in msg_lower for word in ['units', 'quantity', 'qty', 'pieces']):
            return _handle_dealer_units_query(db, question, msg_lower, req_id)
        
        if 'dn count' in msg_lower or 'number of dns' in msg_lower or 'total dns' in msg_lower:
            return _handle_dealer_dn_count_query(db, question, msg_lower, req_id)
        
        if 'delivered' in msg_lower:
            return _handle_delivered_units_query(db, question, msg_lower, req_id)
        
        if 'transit' in msg_lower:
            return _handle_transit_units_query(db, question, msg_lower, req_id)
        
        if 'aging' in msg_lower:
            if 'delivery' in msg_lower or 'pgi' in msg_lower:
                return _handle_delivery_aging_query(db, question, msg_lower, today, req_id)
            elif 'pod' in msg_lower:
                return _handle_pod_aging_query(db, question, msg_lower, today, req_id)
        
        if 'performance' in msg_lower or 'kpi' in msg_lower:
            return _handle_dealer_performance_query(db, question, msg_lower, today, req_id)
        
        # ==========================================================
        # 9. DEALER SUMMARY (Default)
        # ==========================================================
        return _handle_dealer_summary_query(db, question, msg_lower, req_id)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Query Processing Failed: {question[:100]}")
        return f"❌ I encountered an error processing your request. Please try again or type 'Help'."
    
    finally:
        if db:
            db.close()
        
        elapsed = time.time() - start_time
        logger.info(f"[{req_id}] Query processed in {elapsed:.2f}s")


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def _format_help_message() -> str:
    """Format help message"""
    return """📋 *AI Logistics Assistant - Help*

*DN Tracking:*
• Send any 10+ digit DN number

*Dealer Queries:*
• "Show dealer ABC Traders"
• "ABC Traders revenue"
• "ABC Traders pending deliveries"
• "ABC Traders PGI aging"

*Warehouse Queries:*
• "Lahore warehouse summary"
• "Karachi pending PGI"
• "Islamabad POD rate"

*PGI/POD Analytics:*
• "Pending PGI by warehouse"
• "POD aging > 15 days"
• "PGI completion rate"

*Control Tower:*
• "Critical delays"
• "Control tower report"

*Examples:*
• "Top 5 dealers by revenue"
• "Warehouses with highest aging"

Need help? Just ask! 🤖"""


def _get_warehouse_list(db: Session) -> List[str]:
    """Get dynamic warehouse list from database"""
    try:
        warehouses = db.query(DeliveryReport.warehouse).filter(
            DeliveryReport.warehouse.isnot(None)
        ).distinct().limit(50).all()
        return [w[0] for w in warehouses if w[0]]
    except Exception:
        return ['lahore', 'karachi', 'rawalpindi', 'islamabad', 'multan', 'faisalabad', 'sargodha', 'attock', 'sialkot']


def _extract_dealer_name(question: str, msg_lower: str) -> Optional[str]:
    """Extract dealer name from question"""
    dealer_match = re.search(r'dealer\s+([a-z0-9\s&]+)', msg_lower)
    if dealer_match:
        return dealer_match.group(1).strip()
    
    show_match = re.search(r'show\s+([a-z0-9\s&]+)', msg_lower)
    if show_match:
        return show_match.group(1).strip()
    
    if len(msg_lower.split()) <= 5:
        return msg_lower
    
    return None


def _handle_dn_query(db: Session, dn_number: str, today: date, req_id: str) -> str:
    """Handle DN query"""
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


def _handle_warehouse_query(db: Session, warehouse_name: str, today: date, req_id: str) -> str:
    """Handle warehouse query"""
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
        
        aging_records = db.query(DeliveryReport).filter(
            DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"),
            DeliveryReport.dn_create_date.isnot(None),
            DeliveryReport.good_issue_date.isnot(None)
        ).limit(1000).all()
        
        if aging_records:
            avg_aging = sum((r.good_issue_date - r.dn_create_date).days for r in aging_records) / len(aging_records)
            avg_aging = round(avg_aging, 1)
        else:
            avg_aging = 0
        
        lines = [f"🏭 *Warehouse: {warehouse_name.title()}*", ""]
        lines.append(f"📄 *Total DNs:* {result.total_dns or 0:,}")
        lines.append(f"📦 *Total Units:* {int(result.total_units or 0):,}")
        lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
        lines.append("")
        lines.append(f"✅ *PGI Completed:* {pgi_completed}")
        lines.append(f"⏳ *Pending Delivery:* {pending_delivery}")
        lines.append(f"📎 *Pending POD:* {pending_pod}")
        lines.append(f"⏰ *Avg Delivery Aging:* {avg_aging} days")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Warehouse query error: {e}")
        return f"❌ Error fetching {warehouse_name} warehouse data"


def _handle_dealer_summary_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle dealer summary query"""
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


def _handle_pending_delivery_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    """Handle pending delivery query"""
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


def _handle_control_tower(db: Session, today: date, req_id: str) -> str:
    """Handle control tower query"""
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


def _handle_dealer_revenue_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle dealer revenue query"""
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


def _handle_dealer_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle dealer units query"""
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


def _handle_dealer_dn_count_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle dealer DN count query"""
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


def _handle_pgi_pending_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    """Handle PGI pending query"""
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
    """Handle PGI aging query"""
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
    """Handle PGI rate query"""
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


def _handle_pod_pending_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    """Handle POD pending query"""
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
    """Handle POD aging query"""
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
    """Handle POD rate query"""
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


def _handle_delivered_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle delivered units query"""
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


def _handle_transit_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    """Handle transit units query"""
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


def _handle_delivery_aging_query(db: Session, question: str, msg_lower: str, today: date, req_id: str) -> str:
    """Handle delivery aging query"""
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


def _handle_dealer_performance_query(db: Session, question: str, msg_lower: str, today: date, req_id: str) -> str:
    """Handle dealer performance query"""
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
