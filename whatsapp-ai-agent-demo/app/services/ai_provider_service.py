# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v6.0)
# ==========================================================
# AI-Powered WhatsApp Logistics Intelligence Assistant
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from loguru import logger

from app.config import config
from app.models import DeliveryReport, AIResponseLog


# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType(str, Enum):
    # Menu & Navigation
    HELP = "help"
    MENU = "menu"
    DASHBOARD = "dashboard"
    
    # Dealer Intelligence
    DEALER_LOOKUP = "dealer_lookup"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_RISK = "dealer_risk"
    DEALER_PENDING = "dealer_pending"
    DEALER_POD_PENDING = "dealer_pod_pending"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    
    # DN Intelligence
    DN_LOOKUP = "dn_lookup"
    DN_DETAILS = "dn_details"
    DN_DELAYED = "dn_delayed"
    DN_POD_STATUS = "dn_pod_status"
    
    # Operational Analytics
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    CITY_PERFORMANCE = "city_performance"
    NETWORK_HEALTH = "network_health"
    
    # Financial Analytics
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    
    # Executive Intelligence
    EXECUTIVE_SUMMARY = "executive_summary"
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    
    # Advanced AI
    PREDICT_RISK = "predict_risk"
    POD_COMPLIANCE = "pod_compliance"
    DELAY_ANALYSIS = "delay_analysis"
    INVENTORY_RISK = "inventory_risk"
    DEALER_RANKING = "dealer_ranking"
    CITY_RANKING = "city_ranking"
    WAREHOUSE_RANKING = "warehouse_ranking"
    
    # General
    GENERAL_QUERY = "general_query"
    UNKNOWN = "unknown"


# ==========================================================
# WELCOME MESSAGE / DASHBOARD
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, and Executive KPIs in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Dealer Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣ Dealer Dashboard
2️⃣ Dealer Performance Score
3️⃣ Dealer Risk Analysis
4️⃣ Dealer Pending DNs
5️⃣ Dealer POD Pending Status

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *DN Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
6️⃣ DN Status Lookup
7️⃣ DN Complete Details
8️⃣ Delayed DN Analysis
9️⃣ POD Status by DN

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏢 *Operational Analytics*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔟 Warehouse Performance
1️⃣1️⃣ City Performance Analysis
1️⃣2️⃣ Network Health Score

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *Financial Analytics*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣3️⃣ Revenue Analysis
1️⃣4️⃣ Outstanding & Pending Value Analysis

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👑 *Executive Intelligence*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1️⃣5️⃣ Executive Summary Dashboard

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *You can also ask naturally:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Bhatti Electronics-BWP
• DN 6243611920
• Show top risk dealers
• Show top performing dealers
• Show pending DNs
• Which city has maximum delays?
• Which warehouse is underperforming?
• Show dealer outstanding value
• Show network health score
• Give executive summary

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚀 *Advanced AI Commands*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Top 20 Risk Dealers
• Top 20 Performing Dealers
• Revenue at Risk
• POD Compliance Analysis
• Delivery Delay Analysis
• Inventory Risk Analysis
• Dealer Ranking
• City Ranking
• Warehouse Ranking
• Predict Future Risks

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💬 *Ask any question in natural language and receive instant data-driven insights.*"""


# ==========================================================
# INTENT DETECTION ENGINE
# ==========================================================

class IntentDetector:
    """Advanced intent detection for all commands"""
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        """Detect DN number (10 digits)"""
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_numbered_command(message: str) -> Tuple[bool, Optional[str]]:
        """Detect numbered commands like 1, 2, 3, etc."""
        match = re.match(r'^\s*(\d{1,2})\s*$', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        """Detect intent from natural language"""
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        # ==========================================================
        # Check for numbered commands (1, 2, 3, etc.)
        # ==========================================================
        is_num, num_val = IntentDetector.detect_numbered_command(msg_original)
        if is_num:
            num = int(num_val)
            if num == 1:
                return IntentType.DEALER_DASHBOARD, None
            elif num == 2:
                return IntentType.DEALER_PERFORMANCE, None
            elif num == 3:
                return IntentType.DEALER_RISK, None
            elif num == 4:
                return IntentType.DEALER_PENDING, None
            elif num == 5:
                return IntentType.DEALER_POD_PENDING, None
            elif num == 6:
                return IntentType.DN_STATUS_LOOKUP, None
            elif num == 7:
                return IntentType.DN_DETAILS, None
            elif num == 8:
                return IntentType.DN_DELAYED, None
            elif num == 9:
                return IntentType.DN_POD_STATUS, None
            elif num == 10:
                return IntentType.WAREHOUSE_PERFORMANCE, None
            elif num == 11:
                return IntentType.CITY_PERFORMANCE, None
            elif num == 12:
                return IntentType.NETWORK_HEALTH, None
            elif num == 13:
                return IntentType.REVENUE_ANALYSIS, None
            elif num == 14:
                return IntentType.OUTSTANDING_ANALYSIS, None
            elif num == 15:
                return IntentType.EXECUTIVE_SUMMARY, None
        
        # ==========================================================
        # Menu & Help
        # ==========================================================
        if any(word in msg_lower for word in ["help", "menu", "commands", "what can you do", "hello", "hi", "hey", "salam", "start"]):
            return IntentType.HELP, None
        
        # Dashboard
        if any(word in msg_lower for word in ["dashboard", "main menu", "welcome"]):
            return IntentType.DASHBOARD, None
        
        # ==========================================================
        # DN Lookup (10 digits)
        # ==========================================================
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # ==========================================================
        # Executive Intelligence
        # ==========================================================
        if any(word in msg_lower for word in ["executive summary", "executive dashboard", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # ==========================================================
        # Network Health
        # ==========================================================
        if any(word in msg_lower for word in ["network health", "health score", "overall health"]):
            return IntentType.NETWORK_HEALTH, None
        
        # ==========================================================
        # Top Dealers & Risk Dealers
        # ==========================================================
        if "top 20 risk" in msg_lower or "top risk dealers" in msg_lower:
            return IntentType.TOP_RISK_DEALERS, None
        
        if "top 20 performing" in msg_lower or "top performing dealers" in msg_lower or "top dealers" in msg_lower:
            return IntentType.TOP_DEALERS, None
        
        # ==========================================================
        # Dealer Intelligence
        # ==========================================================
        if any(word in msg_lower for word in ["dealer dashboard", "dealer summary"]):
            return IntentType.DEALER_DASHBOARD, None
        
        if any(word in msg_lower for word in ["dealer performance", "performance score"]):
            return IntentType.DEALER_PERFORMANCE, None
        
        if any(word in msg_lower for word in ["dealer risk", "risk analysis", "dealer risk analysis"]):
            return IntentType.DEALER_RISK, None
        
        if any(word in msg_lower for word in ["dealer pending", "pending dns", "dealer pending dns"]):
            return IntentType.DEALER_PENDING, None
        
        if any(word in msg_lower for word in ["dealer pod pending", "pod pending status"]):
            return IntentType.DEALER_POD_PENDING, None
        
        # Dealer lookup by name
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit() and not any(c.isdigit() for c in msg_lower):
            return IntentType.DEALER_LOOKUP, msg_original
        
        # ==========================================================
        # DN Intelligence
        # ==========================================================
        if any(word in msg_lower for word in ["dn status", "dn lookup", "check dn"]):
            return IntentType.DN_STATUS_LOOKUP, None
        
        if any(word in msg_lower for word in ["dn details", "complete details"]):
            return IntentType.DN_DETAILS, None
        
        if any(word in msg_lower for word in ["delayed dn", "dn delay", "delayed delivery"]):
            return IntentType.DN_DELAYED, None
        
        if any(word in msg_lower for word in ["pod status by dn", "dn pod"]):
            return IntentType.DN_POD_STATUS, None
        
        # ==========================================================
        # Operational Analytics
        # ==========================================================
        if any(word in msg_lower for word in ["warehouse performance", "warehouse analytics"]):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        if any(word in msg_lower for word in ["city performance", "city analysis", "regional performance"]):
            return IntentType.CITY_PERFORMANCE, None
        
        # ==========================================================
        # Financial Analytics
        # ==========================================================
        if any(word in msg_lower for word in ["revenue analysis", "total revenue"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        if any(word in msg_lower for word in ["outstanding", "pending value", "value at risk"]):
            return IntentType.OUTSTANDING_ANALYSIS, None
        
        if "revenue at risk" in msg_lower:
            return IntentType.REVENUE_AT_RISK, None
        
        # ==========================================================
        # Advanced AI Commands
        # ==========================================================
        if "predict" in msg_lower:
            return IntentType.PREDICT_RISK, None
        
        if "pod compliance" in msg_lower:
            return IntentType.POD_COMPLIANCE, None
        
        if "delay analysis" in msg_lower:
            return IntentType.DELAY_ANALYSIS, None
        
        if "inventory risk" in msg_lower:
            return IntentType.INVENTORY_RISK, None
        
        if "dealer ranking" in msg_lower:
            return IntentType.DEALER_RANKING, None
        
        if "city ranking" in msg_lower:
            return IntentType.CITY_RANKING, None
        
        if "warehouse ranking" in msg_lower:
            return IntentType.WAREHOUSE_RANKING, None
        
        # ==========================================================
        # City specific
        # ==========================================================
        cities = ["karachi", "lahore", "islamabad", "rawalpindi", "multan", "faisalabad", "gujranwala"]
        for city in cities:
            if city in msg_lower:
                return IntentType.CITY_PERFORMANCE, city
        
        # ==========================================================
        # Default: General Query
        # ==========================================================
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# DATABASE QUERY SERVICE
# ==========================================================

class DatabaseQueryService:
    """Handle all database queries for logistics data"""
    
    def __init__(self, db: Session):
        self.db = db
    
    # ==========================================================
    # DEALER INTELLIGENCE
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete dealer dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
            
            # Calculate metrics
            unique_dns = set()
            delivered_dns = set()
            pending_dns = set()
            pod_pending_dns = set()
            total_value = 0
            pending_value = 0
            pod_pending_value = 0
            
            for r in records:
                dn_no = str(r.dn_no)
                amount = float(r.dn_amount or 0)
                
                unique_dns.add(dn_no)
                total_value += amount
                
                if r.pgi_status == "Completed":
                    delivered_dns.add(dn_no)
                    if r.pod_status == "Pending":
                        pod_pending_dns.add(dn_no)
                        pod_pending_value += amount
                else:
                    pending_dns.add(dn_no)
                    pending_value += amount
            
            total_dns = len(unique_dns)
            delivered = len(delivered_dns)
            pending = len(pending_dns)
            pod_pending = len(pod_pending_dns)
            
            delivery_rate = (delivered / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((delivered - pod_pending) / delivered) * 100 if delivered > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            risk_score = 100 - health_score
            
            if risk_score > 60:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score > 40:
                risk_level = "HIGH"
                risk_icon = "🚨"
            elif risk_score > 20:
                risk_level = "MEDIUM"
                risk_icon = "⚠️"
            else:
                risk_level = "LOW"
                risk_icon = "✅"
            
            response = f"""╔══════════════════════════════════════════╗
║         📊 DEALER DASHBOARD            ║
║      {dealer_name[:25]}                  ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns}
• Delivered: {delivered} ✅
• Pending: {pending} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• POD Pending Value: Rs {pod_pending_value:,.2f}
• Outstanding: Rs {pending_value + pod_pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Health Score: {health_score:.1f}/100
• Risk Score: {risk_score:.1f}/100
• Risk Level: {risk_level}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if pending > 0:
                response += f"• Clear {pending} pending deliveries\n"
            if pod_pending > 0:
                response += f"• Collect POD for {pod_pending} delivered DNs\n"
            if delivery_rate < 80:
                response += "• Review delivery process\n"
            if not response:
                response += "• All metrics are healthy\n"
            
            return {
                "success": True,
                "dealer_name": dealer_name,
                "data": {
                    "total_dns": total_dns,
                    "delivered": delivered,
                    "pending": pending,
                    "pod_pending": pod_pending,
                    "total_value": total_value,
                    "pending_value": pending_value,
                    "health_score": health_score,
                    "risk_level": risk_level
                },
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_top_dealers(self, limit: int = 20) -> List[Dict]:
        """Get top dealers by value"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.sum(DeliveryReport.dn_qty).label("total_qty")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            dealers = []
            for r in results:
                dealers.append({
                    "name": r.customer_name,
                    "total_dns": r.total_dns,
                    "total_value": float(r.total_value or 0),
                    "total_qty": float(r.total_qty or 0)
                })
            
            return dealers
            
        except Exception as e:
            logger.error(f"Top dealers error: {e}")
            return []
    
    def get_top_risk_dealers(self, limit: int = 20) -> List[Dict]:
        """Get dealers with highest risk"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("pending_dns"),
                func.sum(DeliveryReport.dn_amount).label("pending_value")
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            dealers = []
            for r in results:
                dealers.append({
                    "name": r.customer_name,
                    "pending_dns": r.pending_dns,
                    "pending_value": float(r.pending_value or 0)
                })
            
            return dealers
            
        except Exception as e:
            logger.error(f"Top risk dealers error: {e}")
            return []
    
    # ==========================================================
    # DN INTELLIGENCE
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN details"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).all()
            
            if not records:
                return {"success": False, "message": f"DN {dn_number} not found"}
            
            record = records[0]
            
            # Safe date calculations
            dispatch_age = 0
            pod_age = 0
            
            if record.dn_create_date:
                if isinstance(record.dn_create_date, datetime):
                    create_date = record.dn_create_date.date()
                else:
                    create_date = record.dn_create_date
                dispatch_age = (datetime.now().date() - create_date).days
            
            if record.good_issue_date and record.pod_status == "Pending":
                if isinstance(record.good_issue_date, datetime):
                    issue_date = record.good_issue_date.date()
                else:
                    issue_date = record.good_issue_date
                pod_age = (datetime.now().date() - issue_date).days
            
            # Risk calculation
            risk_score = 0
            risk_level = "LOW"
            if dispatch_age > 30:
                risk_score = 90
                risk_level = "CRITICAL"
            elif dispatch_age > 15:
                risk_score = 70
                risk_level = "HIGH"
            elif dispatch_age > 7:
                risk_score = 50
                risk_level = "MEDIUM"
            
            response = f"""╔══════════════════════════════════════════╗
║           📦 DN COMPLETE DETAILS          ║
║              {dn_number}                    ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER INFORMATION*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dealer Name: {record.customer_name or 'N/A'}
• Dealer City: {record.ship_to_city or 'N/A'}
• Warehouse: {record.warehouse or 'N/A'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Product: {record.product or 'N/A'}
• Quantity: {float(record.dn_qty or 0):,.0f} units
• Value: Rs {float(record.dn_amount or 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 *TIMELINE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• DN Create Date: {record.dn_create_date.strftime('%Y-%m-%d') if record.dn_create_date else 'N/A'}
• Good Issue Date: {record.good_issue_date.strftime('%Y-%m-%d') if record.good_issue_date else 'N/A'}
• Dispatch Age: {dispatch_age} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery Status: {'✅ DELIVERED' if record.pgi_status == 'Completed' else '⏳ PENDING'}
• POD Status: {'✅ RECEIVED' if record.pod_status == 'Received' else '📋 PENDING'}
• POD Age: {pod_age} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Risk Score: {risk_score}/100
• Risk Level: {risk_level}
{'🚨 IMMEDIATE ACTION REQUIRED' if risk_level == 'CRITICAL' else '📌 Monitor regularly'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if dispatch_age > 15:
                response += "• Escalate to warehouse manager immediately\n"
            if record.pod_status == "Pending" and pod_age > 7:
                response += "• Follow up with dealer for POD acknowledgement\n"
            if not response:
                response += "• No action needed - delivery on track\n"
            
            return {
                "success": True,
                "dn_number": dn_number,
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # OPERATIONAL ANALYTICS
    # ==========================================================
    
    def get_network_health(self) -> Dict[str, Any]:
        """Calculate overall network health"""
        try:
            total_dns = self.db.query(DeliveryReport.dn_no).distinct().count()
            delivered_dns = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed"
            ).distinct().count()
            
            delivered_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            
            pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            
            pod_received = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).distinct().count()
            
            pod_rate = (pod_received / delivered_dns) * 100 if delivered_dns > 0 else 0
            
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            
            if health_score >= 80:
                status = "Excellent"
                status_icon = "💎"
            elif health_score >= 70:
                status = "Good"
                status_icon = "✅"
            elif health_score >= 60:
                status = "Fair"
                status_icon = "⚠️"
            elif health_score >= 50:
                status = "Poor"
                status_icon = "🚨"
            else:
                status = "Critical"
                status_icon = "💀"
            
            response = f"""╔══════════════════════════════════════════╗
║         📊 NETWORK HEALTH SCORE          ║
╚══════════════════════════════════════════╝

{status_icon} *Overall Health: {health_score:.1f}/100 ({status})*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
• Delivered: {delivered_dns:,} ✅
• Delivery Rate: {delivery_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• POD Received: {pod_received:,}
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {delivered_value + pending_value:,.2f}
• Revenue at Risk: Rs {pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if health_score < 70:
                response += "• Immediate intervention required\n"
            if pod_rate < 80:
                response += "• Accelerate POD collection process\n"
            if pending_value > 0:
                response += f"• Clear Rs {pending_value:,.2f} pending value\n"
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1),
                "revenue_at_risk": round(float(pending_value), 2),
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"Network health error: {e}")
            return {"error": str(e)}
    
    def get_city_performance(self) -> List[Dict]:
        """Get city-wise performance"""
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                cities.append({
                    "city": r.ship_to_city,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0)
                })
            
            cities.sort(key=lambda x: x["pending_rate"], reverse=True)
            return cities[:20]
            
        except Exception as e:
            logger.error(f"City performance error: {e}")
            return []
    
    def get_warehouse_performance(self) -> List[Dict]:
        """Get warehouse-wise performance"""
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            warehouses = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                warehouses.append({
                    "warehouse": r.warehouse,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0)
                })
            
            warehouses.sort(key=lambda x: x["pending_rate"], reverse=True)
            return warehouses[:20]
            
        except Exception as e:
            logger.error(f"Warehouse performance error: {e}")
            return []
    
    # ==========================================================
    # FINANCIAL ANALYTICS
    # ==========================================================
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        """Get revenue analysis"""
        try:
            total_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 0
            pending_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            pod_pending_revenue = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return {
                "total_revenue": float(total_revenue),
                "delivered_revenue": float(delivered_revenue),
                "pending_revenue": float(pending_revenue),
                "pod_pending_revenue": float(pod_pending_revenue),
                "realized_revenue": float(delivered_revenue - pod_pending_revenue)
            }
            
        except Exception as e:
            logger.error(f"Revenue analysis error: {e}")
            return {}


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    """Format all responses for WhatsApp"""
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def help() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def executive_summary(health: Dict, top_dealers: List, risk_dealers: List, cities: List) -> str:
        """Format executive summary"""
        
        response = f"""╔══════════════════════════════════════════╗
║         👑 EXECUTIVE SUMMARY            ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• POD Compliance: {health.get('pod_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 PERFORMING DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(top_dealers[:5], 1):
            response += f"{i}. {d['name'][:25]}\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n\n"
        
        response += """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 5 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk_dealers[:5], 1):
            response += f"{i}. {d['name'][:25]}\n"
            response += f"   ⏳ {d['pending_dns']} pending | Rs {d['pending_value']:,.2f}\n\n"
        
        response += """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌆 *TOP 5 RISK CITIES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, c in enumerate(cities[:5], 1):
            response += f"{i}. {c['city'][:20]}\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n\n"
        
        response += """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Escalate top 5 risk dealers immediately
2. Focus POD collection on pending cities
3. Review warehouse processes for delays

Type "Help" for complete command menu."""
        
        return response
    
    @staticmethod
    def top_dealers_response(dealers: List, title: str = "TOP PERFORMING DEALERS") -> str:
        """Format top dealers response"""
        if not dealers:
            return "No dealer data available."
        
        response = f"📊 *{title}*\n\n"
        for i, d in enumerate(dealers[:20], 1):
            response += f"{i}. *{d['name'][:30]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f}\n"
            response += f"   📦 {d['total_dns']} DNs\n\n"
        
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += "💡 Type a dealer name for detailed dashboard"
        
        return response
    
    @staticmethod
    def top_risk_dealers_response(dealers: List) -> str:
        """Format top risk dealers response"""
        if not dealers:
            return "No risk data available."
        
        response = "🚨 *TOP RISK DEALERS*\n\n"
        for i, d in enumerate(dealers[:20], 1):
            response += f"{i}. *{d['name'][:30]}*\n"
            response += f"   ⏳ {d['pending_dns']} pending DNs\n"
            response += f"   💰 Rs {d['pending_value']:,.2f} at risk\n\n"
        
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += "💡 Escalate these dealers immediately"
        
        return response
    
    @staticmethod
    def city_performance_response(cities: List) -> str:
        """Format city performance response"""
        if not cities:
            return "No city data available."
        
        response = "🌆 *CITY PERFORMANCE ANALYSIS*\n\n"
        for c in cities[:15]:
            if c['pending_rate'] > 30:
                status = "🔴"
            elif c['pending_rate'] > 15:
                status = "🟡"
            else:
                status = "🟢"
            
            response += f"{status} *{c['city'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += "💡 Type 'Help' for more commands"
        
        return response
    
    @staticmethod
    def warehouse_performance_response(warehouses: List) -> str:
        """Format warehouse performance response"""
        if not warehouses:
            return "No warehouse data available."
        
        response = "🏭 *WAREHOUSE PERFORMANCE ANALYSIS*\n\n"
        for w in warehouses[:15]:
            if w['pending_rate'] > 30:
                status = "🔴"
            elif w['pending_rate'] > 15:
                status = "🟡"
            else:
                status = "🟢"
            
            response += f"{status} *{w['warehouse'][:25]}*\n"
            response += f"   📦 {w['total_dns']} DNs | ⏳ {w['pending_dns']} pending ({w['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {w['total_value']:,.2f}\n\n"
        
        response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        response += "💡 Review underperforming warehouses"
        
        return response
    
    @staticmethod
    def revenue_analysis_response(revenue: Dict) -> str:
        """Format revenue analysis response"""
        return f"""╔══════════════════════════════════════════╗
║         💰 REVENUE ANALYSIS             ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *REVENUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized Revenue: Rs {revenue.get('realized_revenue', 0):,.2f} ✅
• Pending Revenue: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳
• POD Pending Revenue: Rs {revenue.get('pod_pending_revenue', 0):,.2f} 📋

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERCENTAGES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Realization Rate: {(revenue.get('realized_revenue', 0) / revenue.get('total_revenue', 1)) * 100:.1f}%
• Revenue at Risk: {(revenue.get('pending_revenue', 0) / revenue.get('total_revenue', 1)) * 100:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        if revenue.get('pod_pending_revenue', 0) > 0:
            response += "• Collect POD to recognize pending revenue\n"
        if revenue.get('pending_revenue', 0) > 0:
            response += "• Expedite pending deliveries\n"
    
    @staticmethod
    def general_response(content: str) -> str:
        """Format general AI response"""
        return content


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """Main service for processing WhatsApp queries"""
    
    def __init__(self, db: Session):
        self.db = db
        self.db_service = DatabaseQueryService(db)
        self.formatter = ResponseFormatter()
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        """Process user query and return response"""
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        # Detect intent
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            # Route to appropriate handler
            if intent == IntentType.HELP or intent == IntentType.MENU:
                result = self._handle_help()
            elif intent == IntentType.DASHBOARD:
                result = self._handle_dashboard()
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.DEALER_DASHBOARD:
                result = self._handle_dealer_dashboard_prompt()
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance(entity)
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            else:
                result = self._handle_general_query(question)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again.",
                "processing_time_ms": int((time.time() - start_time) * 1000)
            }
    
    def _handle_help(self) -> Dict[str, Any]:
        """Handle help request"""
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dashboard(self) -> Dict[str, Any]:
        """Handle dashboard request"""
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        """Handle DN lookup"""
        result = self.db_service.get_dn_details(dn_number)
        return {
            "success": result["success"],
            "response": result.get("formatted_response", result.get("message", "DN not found"))
        }
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        """Handle dealer lookup"""
        result = self.db_service.get_dealer_dashboard(dealer_name)
        return {
            "success": result["success"],
            "response": result.get("formatted_response", result.get("message", "Dealer not found"))
        }
    
    def _handle_dealer_dashboard_prompt(self) -> Dict[str, Any]:
        """Prompt for dealer name"""
        return {
            "success": True,
            "response": "🏪 *Dealer Dashboard*\n\nPlease type the dealer name you want to analyze.\n\n📝 *Example:* `Bhatti Electronics`"
        }
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        """Handle top dealers request"""
        dealers = self.db_service.get_top_dealers(20)
        response = self.formatter.top_dealers_response(dealers, "TOP PERFORMING DEALERS")
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        """Handle top risk dealers request"""
        dealers = self.db_service.get_top_risk_dealers(20)
        response = self.formatter.top_risk_dealers_response(dealers)
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        """Handle executive summary request"""
        health = self.db_service.get_network_health()
        top_dealers = self.db_service.get_top_dealers(10)
        risk_dealers = self.db_service.get_top_risk_dealers(10)
        cities = self.db_service.get_city_performance()
        
        response = self.formatter.executive_summary(health, top_dealers, risk_dealers, cities)
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        """Handle network health request"""
        health = self.db_service.get_network_health()
        response = health.get("formatted_response", "Network health data unavailable")
        return {"success": True, "response": response}
    
    def _handle_city_performance(self, city_name: str = None) -> Dict[str, Any]:
        """Handle city performance request"""
        cities = self.db_service.get_city_performance()
        
        if city_name:
            for c in cities:
                if city_name.lower() in c['city'].lower():
                    response = f"""🌆 *CITY PERFORMANCE: {c['city'].upper()}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {c['total_dns']:,}
• Pending DNs: {c['pending_dns']}
• Pending Rate: {c['pending_rate']:.1f}%
• Total Value: Rs {c['total_value']:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'⚠️ This city requires immediate attention' if c['pending_rate'] > 30 else '✅ Performance is satisfactory'}"""
                    return {"success": True, "response": response}
        
        response = self.formatter.city_performance_response(cities)
        return {"success": True, "response": response}
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        """Handle warehouse performance request"""
        warehouses = self.db_service.get_warehouse_performance()
        response = self.formatter.warehouse_performance_response(warehouses)
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        """Handle revenue analysis request"""
        revenue = self.db_service.get_revenue_analysis()
        response = self.formatter.revenue_analysis_response(revenue)
        return {"success": True, "response": response}
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        """Handle outstanding analysis request"""
        revenue = self.db_service.get_revenue_analysis()
        response = f"""💰 *OUTSTANDING & PENDING VALUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *VALUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Outstanding: Rs {revenue.get('pending_revenue', 0) + revenue.get('pod_pending_revenue', 0):,.2f}
• Pending Delivery: Rs {revenue.get('pending_revenue', 0):,.2f}
• POD Pending: Rs {revenue.get('pod_pending_revenue', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Escalate pending deliveries
• Accelerate POD collection
• Review top risk dealers

Type "Top risk dealers" for detailed list."""
        return {"success": True, "response": response}
    
    def _handle_general_query(self, question: str) -> Dict[str, Any]:
        """Handle general queries"""
        response = f"""🤖 *I understand you're asking about: "{question[:50]}"*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands for instant insights:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Dealer Analytics*
• Type any dealer name
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number

👑 *Executive Views*
• "Executive summary" - Leadership view
• "Network health" - Overall status

🌆 *Regional Analysis*
• "City performance" - City-wise breakdown
• "Warehouse performance" - Hub efficiency

💰 *Financial Insights*
• "Revenue analysis" - Total revenue
• "Outstanding analysis" - Pending value

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type "Help" for complete command menu."""
        
        return {"success": True, "response": response}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."
