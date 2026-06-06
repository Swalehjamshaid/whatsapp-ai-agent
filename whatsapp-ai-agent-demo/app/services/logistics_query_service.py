# ==========================================================
# FILE: app/services/logistics_query_service.py (ENTERPRISE v2.1)
# ==========================================================

from sqlalchemy.orm import Session
from sqlalchemy import func, distinct
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, date, timedelta
import re
from collections import defaultdict

from loguru import logger

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

from app.models import DeliveryReport


class LogisticsQueryService:

    @staticmethod
    def calculate_dispatch_age(record) -> int:
        if not record.dn_create_date:
            return 0
        create_date = record.dn_create_date.date() if isinstance(record.dn_create_date, datetime) else record.dn_create_date
        today = datetime.now().date()
        return (today - create_date).days
    
    @staticmethod
    def calculate_pod_age(record) -> int:
        if not record.good_issue_date:
            return 0
        issue_date = record.good_issue_date.date() if isinstance(record.good_issue_date, datetime) else record.good_issue_date
        return (datetime.now().date() - issue_date).days
    
    @staticmethod
    def search_dn(db: Session, dn_no: str) -> List[Any]:
        try:
            dn_clean = re.sub(r'^DN\s*', '', str(dn_no), flags=re.IGNORECASE)
            dn_clean = re.sub(r'^Track\s*', '', dn_clean, flags=re.IGNORECASE)
            dn_clean = re.sub(r'^Status\s*', '', dn_clean, flags=re.IGNORECASE)
            dn_clean = re.sub(r'^POD\s*', '', dn_clean, flags=re.IGNORECASE)
            dn_clean = dn_clean.strip()
            
            logger.info(f"🔢 Searching for DN: {dn_clean}")
            
            records = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_clean
            ).all()
            return records
        except Exception as e:
            logger.error(f"DN search error: {e}")
            return []
    
    @staticmethod
    def get_dn_complete_dashboard(db: Session, dn_no: str) -> Dict[str, Any]:
        try:
            records = LogisticsQueryService.search_dn(db, dn_no)
            
            if not records:
                return {
                    "success": False,
                    "message": f"❌ DN '{dn_no}' not found in the system.",
                    "dn_no": dn_no
                }
            
            record = records[0]
            dispatch_age = LogisticsQueryService.calculate_dispatch_age(record)
            pod_age = LogisticsQueryService.calculate_pod_age(record) if record.pgi_status == "Completed" else 0
            
            if record.pgi_status == "Completed":
                if record.pod_status == "Pending":
                    status = "DELIVERED - POD PENDING"
                    status_icon = "📋"
                    status_color = "🟡"
                else:
                    status = "DELIVERED - POD RECEIVED"
                    status_icon = "✅"
                    status_color = "🟢"
            else:
                status = "IN TRANSIT - PENDING DISPATCH"
                status_icon = "🚚"
                status_color = "🔴"
            
            formatted_message = f"""
╔══════════════════════════════════════════╗
║           📦 DN TRACKING REPORT          ║
║              {dn_no}                      ║
╚══════════════════════════════════════════╝

{status_color} *Status:* {status_icon} {status}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Customer: {record.customer_name or 'N/A'}
• City: {record.ship_to_city or 'N/A'}
• Warehouse: {record.warehouse or 'N/A'}
• Product: {record.product or 'N/A'}
• Quantity: {float(record.dn_qty or 0):,.0f}
• Value: Rs {float(record.dn_amount or 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *TIMELINE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• DN Create Date: {record.dn_create_date.strftime('%Y-%m-%d') if record.dn_create_date else 'N/A'}
• Good Issue Date: {record.good_issue_date.strftime('%Y-%m-%d') if record.good_issue_date else 'N/A'}
• Dispatch Age: {dispatch_age} days
"""
            if record.pgi_status == "Completed":
                formatted_message += f"""• POD Status: {record.pod_status or 'Pending'}
• POD Age: {pod_age} days
"""
            
            formatted_message += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Need more?* Try:
• `POD {dn_no}` - Check POD status
• `Status {dn_no}` - Refresh tracking
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            
            return {
                "success": True,
                "dn_no": dn_no,
                "records": records,
                "status": status,
                "status_icon": status_icon,
                "dispatch_age": dispatch_age,
                "pod_age": pod_age if record.pgi_status == "Completed" else None,
                "formatted_message": formatted_message
            }
            
        except Exception as e:
            logger.error(f"DN dashboard error for {dn_no}: {e}")
            return {
                "success": False,
                "message": f"❌ Error retrieving DN {dn_no}: {str(e)}",
                "dn_no": dn_no
            }
    
    @staticmethod
    def get_dealer_complete_dashboard(db: Session, dealer_name: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        try:
            records = db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == dealer_name
            ).all()
            
            if not records:
                logger.warning(f"No records found for dealer: {dealer_name}")
                return {
                    "success": False,
                    "message": f"No records found for {dealer_name}",
                    "dealer_name": dealer_name,
                    "kpis": {
                        "total_dns": 0,
                        "delivered_dns": 0,
                        "pending_dns": 0,
                        "pod_pending_dns": 0,
                        "total_amount": 0,
                        "pending_amount": 0,
                        "pod_pending_amount": 0,
                        "outstanding_amount": 0
                    }
                }
            
            unique_dns = set()
            delivered_dns = set()
            pending_dns = set()
            pod_pending_dns = set()
            total_amount = 0
            pending_amount = 0
            pod_pending_amount = 0
            
            for r in records:
                dn_no = str(r.dn_no)
                amount = float(r.dn_amount or 0)
                
                unique_dns.add(dn_no)
                total_amount += amount
                
                if r.pgi_status == "Completed":
                    delivered_dns.add(dn_no)
                    if r.pod_status == "Pending":
                        pod_pending_dns.add(dn_no)
                        pod_pending_amount += amount
                else:
                    pending_dns.add(dn_no)
                    pending_amount += amount
            
            outstanding_amount = pending_amount + pod_pending_amount
            
            logger.info(f"📊 Dealer Dashboard Generated for {dealer_name}: {len(unique_dns)} DNs, Rs {total_amount:,.2f}")
            
            return {
                "success": True,
                "dealer_name": dealer_name,
                "kpis": {
                    "total_dns": len(unique_dns),
                    "delivered_dns": len(delivered_dns),
                    "pending_dns": len(pending_dns),
                    "pod_pending_dns": len(pod_pending_dns),
                    "total_amount": round(total_amount, 2),
                    "pending_amount": round(pending_amount, 2),
                    "pod_pending_amount": round(pod_pending_amount, 2),
                    "outstanding_amount": round(outstanding_amount, 2)
                },
                "page": page
            }
            
        except Exception as e:
            logger.error(f"Error in get_dealer_complete_dashboard for {dealer_name}: {e}")
            return {
                "success": False,
                "message": f"Error loading dealer data: {str(e)}",
                "dealer_name": dealer_name
            }


def search_dn(db: Session, dn_no: str) -> List[Any]:
    return LogisticsQueryService.search_dn(db, dn_no)

def get_dn_complete_dashboard(db: Session, dn_no: str) -> Dict[str, Any]:
    return LogisticsQueryService.get_dn_complete_dashboard(db, dn_no)
