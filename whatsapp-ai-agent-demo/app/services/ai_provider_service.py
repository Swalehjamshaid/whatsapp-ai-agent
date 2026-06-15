# ==========================================================
# FILE: app/services/ai_provider_service.py (MINIMAL - NO EXTRA DEPS)
# ==========================================================

import re
import time
import uuid
from datetime import datetime, date
from typing import Optional, Callable
from loguru import logger
from sqlalchemy import func
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
        
        # HELP
        if msg_lower in ['help', '/help', 'menu', '?', 'commands']:
            return _format_help_message()
        
        # DN NUMBER
        dn_match = re.search(r'\b(\d{8,12})\b', question)
        if dn_match:
            dn_number = dn_match.group(1)
            return _handle_dn_query(db, dn_number, today, req_id)
        
        # WAREHOUSE
        warehouses = _get_warehouse_list(db)
        for wh in warehouses:
            if wh.lower() in msg_lower:
                return _handle_warehouse_query(db, wh, today, req_id)
        
        # CONTROL TOWER
        if any(word in msg_lower for word in ['critical', 'alert', 'urgent', 'control tower']):
            return _handle_control_tower(db, today, req_id)
        
        # PENDING DELIVERY
        if 'pending' in msg_lower and ('delivery' in msg_lower or 'pgi' in msg_lower):
            return _handle_pending_delivery_query(db, msg_lower, today, req_id)
        
        # PGI QUERIES
        if 'pgi' in msg_lower:
            if 'pending' in msg_lower:
                return _handle_pgi_pending_query(db, msg_lower, today, req_id)
            elif 'aging' in msg_lower:
                return _handle_pgi_aging_query(db, msg_lower, today, req_id)
            elif 'rate' in msg_lower:
                return _handle_pgi_rate_query(db, msg_lower, req_id)
        
        # POD QUERIES
        if 'pod' in msg_lower:
            if 'pending' in msg_lower:
                return _handle_pod_pending_query(db, msg_lower, today, req_id)
            elif 'aging' in msg_lower:
                return _handle_pod_aging_query(db, msg_lower, today, req_id)
            elif 'rate' in msg_lower:
                return _handle_pod_rate_query(db, msg_lower, req_id)
        
        # DEALER KPI QUERIES
        if any(word in msg_lower for word in ['revenue', 'sales', 'amount']):
            return _handle_dealer_revenue_query(db, question, msg_lower, req_id)
        
        if any(word in msg_lower for word in ['units', 'quantity', 'qty']):
            return _handle_dealer_units_query(db, question, msg_lower, req_id)
        
        if 'performance' in msg_lower or 'kpi' in msg_lower:
            return _handle_dealer_performance_query(db, question, msg_lower, today, req_id)
        
        # DEALER SUMMARY (Default)
        return _handle_dealer_summary_query(db, question, msg_lower, req_id)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Error: {e}")
        return "❌ I encountered an error. Please try again or type 'Help'."
    
    finally:
        if db:
            db.close()
        
        elapsed = time.time() - start_time
        logger.info(f"[{req_id}] Done in {elapsed:.2f}s")


def _format_help_message() -> str:
    return """📋 *AI Logistics Assistant - Help*

*DN Tracking:* Send any 10+ digit DN number
*Dealer:* "Show dealer ABC Traders"
*Warehouse:* "Lahore warehouse summary"
*Revenue:* "ABC Traders revenue"
*Units:* "ABC Traders units"
*Pending:* "Pending deliveries"
*PGI:* "Pending PGI"
*POD:* "Pending POD"
*Performance:* "ABC Traders performance"
*Control Tower:* "Critical delays"

Need help? Just ask! 🤖"""


def _get_warehouse_list(db: Session):
    try:
        warehouses = db.query(DeliveryReport.warehouse).filter(
            DeliveryReport.warehouse.isnot(None)
        ).distinct().limit(50).all()
        return [w[0] for w in warehouses if w[0]]
    except Exception:
        return ['lahore', 'karachi', 'islamabad', 'rawalpindi', 'multan', 'faisalabad']


def _extract_dealer_name(question: str, msg_lower: str):
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
            return f"❌ DN {dn_number} not found."
        
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
        logger.exception(f"[{req_id}] DN error: {e}")
        return f"❌ Error looking up DN {dn_number}"


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
        logger.exception(f"[{req_id}] Warehouse error: {e}")
        return f"❌ Error fetching {warehouse_name} data"


def _handle_dealer_summary_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        
        if not dealer_name:
            return "❌ Please specify a dealer name. Example: 'Show dealer ABC Traders'"
        
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
                return f"❌ No dealer found matching '{dealer_name}'."
            
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
        logger.exception(f"[{req_id}] Dealer error: {e}")
        return f"❌ Error fetching dealer data"


def _handle_dealer_revenue_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name."
        
        result = db.query(
            func.sum(DeliveryReport.dn_amount).label('total_revenue')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        return f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Revenue error: {e}")
        return "❌ Error fetching revenue"


def _handle_dealer_units_query(db: Session, question: str, msg_lower: str, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name."
        
        result = db.query(
            func.sum(DeliveryReport.dn_qty).label('total_units')
        ).filter(
            DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
        ).first()
        
        return f"📦 *Units:* {int(result.total_units or 0):,}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] Units error: {e}")
        return "❌ Error fetching units"


def _handle_dealer_performance_query(db: Session, question: str, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(question, msg_lower)
        if not dealer_name:
            return "❌ Please specify a dealer name."
        
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
        delivery_rate = (pgi_completed / total) * 100
        pod_rate = (pod_completed / pgi_completed) * 100 if pgi_completed > 0 else 0
        
        lines = [f"📊 *Performance Dashboard*", ""]
        lines.append(f"💰 *Revenue:* PKR {float(result.total_revenue or 0):,.0f}")
        lines.append(f"📦 *Units:* {int(result.total_units or 0):,}")
        lines.append(f"📄 *Total DNs:* {result.total_dns or 0}")
        lines.append(f"🚚 *Delivery Rate:* {delivery_rate:.1f}%")
        lines.append(f"📎 *POD Rate:* {pod_rate:.1f}%")
        lines.append(f"⏳ *Pending Delivery:* {pending_delivery}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Performance error: {e}")
        return "❌ Error fetching performance data"


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
        else:
            avg_aging = 0
        
        lines = ["⏳ *Pending Delivery Report*", ""]
        lines.append(f"📊 *Total Pending:* {pending_count}")
        lines.append(f"⏰ *Average Aging:* {avg_aging} days")
        
        if dealer_name:
            lines.append(f"🏪 *Dealer:* {dealer_name.title()}")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Pending error: {e}")
        return "❌ Error fetching pending data"


def _handle_pgi_pending_query(db: Session, msg_lower: str, today: date, req_id: str) -> str:
    try:
        dealer_name = _extract_dealer_name(msg_lower, msg_lower)
        
        query = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.is_(None))
        
        if dealer_name:
            query = query.filter(DeliveryReport.customer_name.ilike(f"%{dealer_name}%"))
        
        pending_count = query.count()
        
        return f"⏳ *PGI Pending:* {pending_count}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI pending error: {e}")
        return "❌ Error"


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
        else:
            avg_aging = 0
        
        return f"⏰ *PGI Aging:* {avg_aging} days avg"
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI aging error: {e}")
        return "❌ Error"


def _handle_pgi_rate_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        total = db.query(DeliveryReport).count()
        pgi_done = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.isnot(None)).count()
        
        rate = (pgi_done / total) * 100 if total > 0 else 0
        
        return f"📊 *PGI Rate:* {rate:.1f}%"
        
    except Exception as e:
        logger.exception(f"[{req_id}] PGI rate error: {e}")
        return "❌ Error"


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
        
        return f"📎 *POD Pending:* {pending_count}"
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD pending error: {e}")
        return "❌ Error"


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
        else:
            avg_aging = 0
        
        return f"⏰ *POD Aging:* {avg_aging} days avg"
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD aging error: {e}")
        return "❌ Error"


def _handle_pod_rate_query(db: Session, msg_lower: str, req_id: str) -> str:
    try:
        pgi_done = db.query(DeliveryReport).filter(DeliveryReport.good_issue_date.isnot(None)).count()
        pod_done = db.query(DeliveryReport).filter(DeliveryReport.pod_date.isnot(None)).count()
        
        rate = (pod_done / pgi_done) * 100 if pgi_done > 0 else 0
        
        return f"📊 *POD Rate:* {rate:.1f}%"
        
    except Exception as e:
        logger.exception(f"[{req_id}] POD rate error: {e}")
        return "❌ Error"


def _handle_control_tower(db: Session, today: date, req_id: str) -> str:
    try:
        critical_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.good_issue_date.is_(None),
            DeliveryReport.dn_create_date.isnot(None)
        ).all()
        
        critical_list = []
        for r in critical_deliveries[:20]:
            aging = (today - r.dn_create_date).days
            if aging > 15:
                critical_list.append(f"DN {r.dn_no}: {r.customer_name} - {aging} days")
        
        lines = ["🚨 *Control Tower*", ""]
        
        if critical_list:
            lines.append("🔴 *Critical Deliveries (>15 days)*")
            for item in critical_list[:5]:
                lines.append(f"   • {item}")
        else:
            lines.append("✅ No critical alerts")
        
        return "\n".join(lines)
        
    except Exception as e:
        logger.exception(f"[{req_id}] Control tower error: {e}")
        return "❌ Error"
