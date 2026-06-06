# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v10.0)
# ==========================================================
# AI LOGISTICS INTELLIGENCE ASSISTANT
# - Complete dealer intelligence (1-5)
# - Complete DN intelligence (6-9)
# - Complete operational analytics (10-12)
# - Complete financial analytics (13-14)
# - Complete executive intelligence (15)
# - Natural language support
# - GROQ AI integration
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_
from loguru import logger

from app.config import config
from app.models import DeliveryReport

# ==========================================================
# IMPORT GROQ PROVIDER
# ==========================================================

try:
    from app.services.ai_provider_service import get_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False


# ==========================================================
# WELCOME MESSAGE - COMPLETE DASHBOARD
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
# INTENT TYPES
# ==========================================================

class IntentType(str, Enum):
    # Navigation
    HELP = "help"
    MENU = "menu"
    WELCOME = "welcome"
    
    # Dealer Intelligence (1-5)
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_RISK = "dealer_risk"
    DEALER_PENDING = "dealer_pending"
    DEALER_POD_PENDING = "dealer_pod_pending"
    DEALER_LOOKUP = "dealer_lookup"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    
    # DN Intelligence (6-9)
    DN_LOOKUP = "dn_lookup"
    DN_DETAILS = "dn_details"
    DN_STATUS = "dn_status"
    DN_DELAYED = "dn_delayed"
    DN_POD_STATUS = "dn_pod_status"
    
    # Operational Analytics (10-12)
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    CITY_PERFORMANCE = "city_performance"
    NETWORK_HEALTH = "network_health"
    
    # Financial Analytics (13-14)
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    
    # Executive Intelligence (15)
    EXECUTIVE_SUMMARY = "executive_summary"
    
    # General
    GENERAL_QUERY = "general_query"


# ==========================================================
# INTENT DETECTION ENGINE
# ==========================================================

class IntentDetector:
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        """Detect 10-digit DN number"""
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_numbered_command(message: str) -> Tuple[bool, Optional[int]]:
        """Detect numbered commands (1-15)"""
        msg_clean = message.strip()
        if msg_clean.isdigit():
            num = int(msg_clean)
            if 1 <= num <= 15:
                return True, num
        return False, None
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        """Detect intent from message"""
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        # Check for numbered commands (1-15)
        is_num, num = IntentDetector.detect_numbered_command(msg_original)
        if is_num:
            command_map = {
                1: IntentType.DEALER_DASHBOARD,
                2: IntentType.DEALER_PERFORMANCE,
                3: IntentType.DEALER_RISK,
                4: IntentType.DEALER_PENDING,
                5: IntentType.DEALER_POD_PENDING,
                6: IntentType.DN_STATUS,
                7: IntentType.DN_DETAILS,
                8: IntentType.DN_DELAYED,
                9: IntentType.DN_POD_STATUS,
                10: IntentType.WAREHOUSE_PERFORMANCE,
                11: IntentType.CITY_PERFORMANCE,
                12: IntentType.NETWORK_HEALTH,
                13: IntentType.REVENUE_ANALYSIS,
                14: IntentType.OUTSTANDING_ANALYSIS,
                15: IntentType.EXECUTIVE_SUMMARY,
            }
            return command_map.get(num, IntentType.HELP), None
        
        # Help / Menu / Welcome
        if any(word in msg_lower for word in ["help", "menu", "commands", "welcome", "start"]):
            return IntentType.HELP, None
        
        if any(word in msg_lower for word in ["hello", "hi", "hey", "salam"]):
            return IntentType.WELCOME, None
        
        # DN Lookup (10 digits)
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # Executive Summary
        if any(word in msg_lower for word in ["executive summary", "executive dashboard", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Network Health
        if any(word in msg_lower for word in ["network health", "health score", "overall health"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Top Risk Dealers
        if any(word in msg_lower for word in ["top risk", "risk dealers", "top 20 risk"]):
            return IntentType.TOP_RISK_DEALERS, None
        
        # Top Dealers
        if any(word in msg_lower for word in ["top dealer", "best dealer", "top performing", "top 20"]):
            return IntentType.TOP_DEALERS, None
        
        # City Analysis
        if any(word in msg_lower for word in ["city", "karachi", "lahore", "islamabad", "city performance"]):
            return IntentType.CITY_PERFORMANCE, None
        
        # Warehouse Performance
        if any(word in msg_lower for word in ["warehouse", "godown", "warehouse performance"]):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        # Revenue Analysis
        if any(word in msg_lower for word in ["revenue", "financial", "revenue analysis"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        # Outstanding Analysis
        if any(word in msg_lower for word in ["outstanding", "pending value", "value at risk"]):
            return IntentType.OUTSTANDING_ANALYSIS, None
        
        # Dealer lookup by name
        if len(msg_lower.split()) <= 6 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, msg_original
        
        # Default to general query (will use GROQ AI)
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# DATABASE SERVICE
# ==========================================================

class DatabaseService:
    
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
                return {"success": False, "message": f"❌ Dealer '{dealer_name}' not found"}
            
            # Calculate metrics
            total_dns = len(set(str(r.dn_no) for r in records))
            delivered = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed"))
            pending = total_dns - delivered
            pod_pending = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
            pod_pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending")
            
            delivery_rate = (delivered / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((delivered - pod_pending) / delivered) * 100 if delivered > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            
            if health_score >= 80:
                health_status = "Excellent"
                health_icon = "💎"
            elif health_score >= 60:
                health_status = "Good"
                health_icon = "✅"
            elif health_score >= 40:
                health_status = "Fair"
                health_icon = "⚠️"
            else:
                health_status = "Critical"
                health_icon = "🚨"
            
            response = f"""╔══════════════════════════════════════════╗
║         📊 DEALER DASHBOARD            ║
║      {dealer_name[:25]}                  ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *HEALTH ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{health_icon} Health Score: {health_score:.1f}/100 ({health_status})
• Risk Level: {'LOW' if health_score >= 70 else 'MEDIUM' if health_score >= 50 else 'HIGH'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Help" for more commands"""
            
            return {"success": True, "formatted_response": response, "data": {
                "total_dns": total_dns, "delivered": delivered, "pending": pending,
                "pod_pending": pod_pending, "total_value": total_value,
                "pending_value": pending_value, "health_score": health_score
            }}
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_dealer_performance_score(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer performance score"""
        result = self.get_dealer_dashboard(dealer_name)
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        response = f"""📊 *DEALER PERFORMANCE SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *Dealer:* {dealer_name[:30]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 *Performance Breakdown:*
• Delivery Score: {data.get('delivery_rate', 0):.1f}/100
• POD Score: {((data.get('delivered', 1) - data.get('pod_pending', 0)) / data.get('delivered', 1) * 100) if data.get('delivered', 0) > 0 else 0:.1f}/100
• Financial Score: {((data.get('total_value', 1) - data.get('pending_value', 0)) / data.get('total_value', 1) * 100) if data.get('total_value', 0) > 0 else 100:.1f}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *Overall Score: {data.get('health_score', 0):.1f}/100*"""
        
        return {"success": True, "formatted_response": response}
    
    def get_dealer_risk_analysis(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer risk analysis"""
        result = self.get_dealer_dashboard(dealer_name)
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        pending = data.get('pending', 0)
        pod_pending = data.get('pod_pending', 0)
        pending_value = data.get('pending_value', 0)
        
        risk_score = (pending / data.get('total_dns', 1) * 40) + (pod_pending / data.get('delivered', 1) * 30) if data.get('delivered', 0) > 0 else 0
        risk_score = min(100, risk_score + (pending_value / data.get('total_value', 1) * 30) if data.get('total_value', 0) > 0 else 0)
        
        if risk_score >= 70:
            risk_level = "🔴 CRITICAL"
            action = "Immediate escalation required"
        elif risk_score >= 40:
            risk_level = "🟡 HIGH"
            action = "Monitor closely"
        else:
            risk_level = "🟢 LOW"
            action = "Regular monitoring sufficient"
        
        response = f"""⚠️ *DEALER RISK ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *Dealer:* {dealer_name[:30]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Risk Breakdown:*
• Pending DNs: {pending} ({ (pending / data.get('total_dns', 1) * 100):.1f}%)
• POD Pending: {pod_pending}
• Financial Exposure: Rs {pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 *Risk Score: {risk_score:.1f}/100*
{risk_level}

💡 *Action:* {action}"""
        
        return {"success": True, "formatted_response": response}
    
    def get_dealer_pending_dns(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer pending DNs"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
                DeliveryReport.pgi_status != "Completed"
            ).all()
            
            if not records:
                return {"success": True, "formatted_response": f"✅ No pending DNs for {dealer_name}"}
            
            response = f"⏳ *PENDING DNS FOR {dealer_name[:30]}*\n\n"
            for r in records[:10]:
                response += f"• DN {r.dn_no}: Rs {float(r.dn_amount or 0):,.2f}\n"
            
            if len(records) > 10:
                response += f"\n... and {len(records) - 10} more"
            
            return {"success": True, "formatted_response": response}
            
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    def get_dealer_pod_pending(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer POD pending status"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%"),
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).all()
            
            if not records:
                return {"success": True, "formatted_response": f"✅ No POD pending for {dealer_name}"}
            
            response = f"📋 *POD PENDING FOR {dealer_name[:30]}*\n\n"
            for r in records[:10]:
                response += f"• DN {r.dn_no}: Rs {float(r.dn_amount or 0):,.2f}\n"
            
            return {"success": True, "formatted_response": response}
            
        except Exception as e:
            return {"success": False, "message": str(e)}
    
    def get_top_dealers(self, limit: int = 20) -> List[Dict]:
        """Get top dealers by value"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{"name": r.customer_name, "total_dns": r.total_dns, "total_value": float(r.total_value or 0)} for r in results]
        except Exception as e:
            logger.error(f"Top dealers error: {e}")
            return []
    
    def get_top_risk_dealers(self, limit: int = 20) -> List[Dict]:
        """Get top risk dealers"""
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
            
            return [{"name": r.customer_name, "pending_dns": r.pending_dns, "pending_value": float(r.pending_value or 0)} for r in results]
        except Exception as e:
            logger.error(f"Top risk dealers error: {e}")
            return []
    
    # ==========================================================
    # DN INTELLIGENCE
    # ==========================================================
    
    def get_dn_details(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN details"""
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not record:
                return {"success": False, "message": f"❌ DN {dn_number} not found"}
            
            # Calculate ages
            dispatch_age = 0
            if record.dn_create_date:
                if isinstance(record.dn_create_date, datetime):
                    create_date = record.dn_create_date.date()
                else:
                    create_date = record.dn_create_date
                dispatch_age = (datetime.now().date() - create_date).days
            
            pod_age = 0
            if record.good_issue_date and record.pod_status == "Pending":
                if isinstance(record.good_issue_date, datetime):
                    issue_date = record.good_issue_date.date()
                else:
                    issue_date = record.good_issue_date
                pod_age = (datetime.now().date() - issue_date).days
            
            # Risk level
            if dispatch_age > 30:
                risk = "🔴 CRITICAL"
            elif dispatch_age > 15:
                risk = "🟡 HIGH"
            elif dispatch_age > 7:
                risk = "🟠 MEDIUM"
            else:
                risk = "🟢 LOW"
            
            response = f"""╔══════════════════════════════════════════╗
║           📦 DN COMPLETE DETAILS          ║
║              {dn_number}                    ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER INFORMATION*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dealer: {record.customer_name or 'N/A'}
• City: {record.ship_to_city or 'N/A'}
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
• Create Date: {record.dn_create_date.strftime('%Y-%m-%d') if record.dn_create_date else 'N/A'}
• Dispatch Age: {dispatch_age} days
• POD Age: {pod_age} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery: {'✅ DELIVERED' if record.pgi_status == 'Completed' else '⏳ PENDING'}
• POD: {'✅ RECEIVED' if record.pod_status == 'Received' else '📋 PENDING'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ASSESSMENT: {risk}*

💡 Type "Help" for more commands"""
            
            return {"success": True, "formatted_response": response}
            
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_dn_status(self, dn_number: str) -> Dict[str, Any]:
        """Get DN status only"""
        record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
        if not record:
            return {"success": False, "message": f"❌ DN {dn_number} not found"}
        
        status = "✅ DELIVERED" if record.pgi_status == "Completed" else "⏳ PENDING"
        pod = "✅ RECEIVED" if record.pod_status == "Received" else "📋 PENDING"
        
        response = f"""🔢 *DN {dn_number} STATUS*
• Delivery: {status}
• POD: {pod}"""
        
        return {"success": True, "formatted_response": response}
    
    def get_dn_pod_status(self, dn_number: str) -> Dict[str, Any]:
        """Get DN POD status"""
        record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn_number).first()
        if not record:
            return {"success": False, "message": f"❌ DN {dn_number} not found"}
        
        if record.pgi_status != "Completed":
            return {"success": True, "formatted_response": f"📋 *POD STATUS*\nDN {dn_number} is not yet delivered. POD will be available after delivery."}
        
        pod_status = "✅ RECEIVED" if record.pod_status == "Received" else "📋 PENDING"
        response = f"""📋 *POD STATUS - DN {dn_number}*
• Status: {pod_status}
• Value: Rs {float(record.dn_amount or 0):,.2f}"""
        
        return {"success": True, "formatted_response": response}
    
    def get_delayed_dns(self, days_threshold: int = 7) -> List[Dict]:
        """Get delayed DNs"""
        try:
            cutoff = datetime.now().date() - timedelta(days=days_threshold)
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_create_date <= cutoff,
                DeliveryReport.pgi_status != "Completed"
            ).all()
            
            return [{"dn_no": r.dn_no, "customer": r.customer_name, "age": (datetime.now().date() - r.dn_create_date.date()).days, "value": float(r.dn_amount or 0)} for r in records]
        except Exception as e:
            return []
    
    # ==========================================================
    # OPERATIONAL ANALYTICS
    # ==========================================================
    
    def get_warehouse_performance(self) -> List[Dict]:
        """Get warehouse performance"""
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
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                warehouses.append({
                    "warehouse": r.warehouse,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            warehouses.sort(key=lambda x: x["pending_rate"], reverse=True)
            return warehouses[:20]
        except Exception as e:
            return []
    
    def get_city_performance(self) -> List[Dict]:
        """Get city performance"""
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
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                cities.append({
                    "city": r.ship_to_city,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            cities.sort(key=lambda x: x["pending_rate"], reverse=True)
            return cities[:20]
        except Exception as e:
            return []
    
    def get_network_health(self) -> Dict[str, Any]:
        """Get network health"""
        try:
            total_dns = self.db.query(DeliveryReport.dn_no).distinct().count()
            delivered_dns = self.db.query(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").distinct().count()
            pod_received = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).distinct().count()
            pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status != "Completed").scalar() or 0
            
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = (pod_received / delivered_dns) * 100 if delivered_dns > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            
            if health_score >= 80:
                status = "💎 EXCELLENT"
            elif health_score >= 60:
                status = "✅ GOOD"
            elif health_score >= 40:
                status = "⚠️ FAIR"
            else:
                status = "🚨 CRITICAL"
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1),
                "revenue_at_risk": round(float(pending_value), 2),
                "status": status
            }
        except Exception as e:
            return {}
    
    # ==========================================================
    # FINANCIAL ANALYTICS
    # ==========================================================
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        """Get revenue analysis"""
        try:
            total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
            pending = total - delivered
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return {
                "total_revenue": float(total),
                "delivered_revenue": float(delivered),
                "pending_revenue": float(pending),
                "pod_pending_revenue": float(pod_pending),
                "realized_revenue": float(delivered - pod_pending),
                "realization_rate": ((delivered - pod_pending) / total * 100) if total > 0 else 0,
                "revenue_at_risk": float(pending + pod_pending)
            }
        except Exception as e:
            return {}
    
    def get_outstanding_analysis(self) -> Dict[str, Any]:
        """Get outstanding value analysis"""
        revenue = self.get_revenue_analysis()
        return {
            "outstanding_value": revenue.get("pending_revenue", 0) + revenue.get("pod_pending_revenue", 0),
            "pending_delivery": revenue.get("pending_revenue", 0),
            "pod_pending": revenue.get("pod_pending_revenue", 0),
            "percentage_of_revenue": ((revenue.get("pending_revenue", 0) + revenue.get("pod_pending_revenue", 0)) / revenue.get("total_revenue", 1) * 100) if revenue.get("total_revenue", 0) > 0 else 0
        }


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def help() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def dealer_dashboard_prompt() -> str:
        return "🏪 *Dealer Dashboard*\n\nPlease type the dealer name you want to analyze.\n\n📝 *Example:* `Bhatti Electronics`"
    
    @staticmethod
    def dealer_performance_prompt() -> str:
        return "📊 *Dealer Performance Score*\n\nPlease type the dealer name to see their performance score.\n\n📝 *Example:* `Bhatti Electronics`"
    
    @staticmethod
    def dealer_risk_prompt() -> str:
        return "⚠️ *Dealer Risk Analysis*\n\nPlease type the dealer name to analyze risks.\n\n📝 *Example:* `Bhatti Electronics`"
    
    @staticmethod
    def dealer_pending_prompt() -> str:
        return "⏳ *Dealer Pending DNs*\n\nPlease type the dealer name to see pending deliveries.\n\n📝 *Example:* `Bhatti Electronics`"
    
    @staticmethod
    def dealer_pod_pending_prompt() -> str:
        return "📋 *Dealer POD Pending Status*\n\nPlease type the dealer name to see POD status.\n\n📝 *Example:* `Bhatti Electronics`"
    
    @staticmethod
    def dn_status_prompt() -> str:
        return "🔢 *DN Status Lookup*\n\nPlease send a 10-digit DN number to check status.\n\n📝 *Example:* `6243611920`"
    
    @staticmethod
    def dn_details_prompt() -> str:
        return "📦 *DN Complete Details*\n\nPlease send a 10-digit DN number for complete details.\n\n📝 *Example:* `6243611920`"
    
    @staticmethod
    def dn_pod_prompt() -> str:
        return "📋 *POD Status by DN*\n\nPlease send a 10-digit DN number to check POD status.\n\n📝 *Example:* `6243611920`"
    
    @staticmethod
    def top_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "📊 No dealer data available."
        
        response = "🏆 *TOP 20 PERFORMING DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n\n"
        return response
    
    @staticmethod
    def top_risk_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "🚨 No risk data available."
        
        response = "🚨 *TOP 20 RISK DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   ⏳ {d['pending_dns']} pending | Rs {d['pending_value']:,.2f}\n\n"
        return response
    
    @staticmethod
    def warehouse_performance_response(warehouses: List) -> str:
        if not warehouses:
            return "🏭 No warehouse data available."
        
        response = "🏭 *WAREHOUSE PERFORMANCE*\n\n"
        for w in warehouses[:15]:
            response += f"{w['status']} *{w['warehouse'][:25]}*\n"
            response += f"   📦 {w['total_dns']} DNs | ⏳ {w['pending_dns']} pending ({w['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {w['total_value']:,.2f}\n\n"
        return response
    
    @staticmethod
    def city_performance_response(cities: List) -> str:
        if not cities:
            return "🌆 No city data available."
        
        response = "🌆 *CITY PERFORMANCE*\n\n"
        for c in cities[:15]:
            response += f"{c['status']} *{c['city'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        return response
    
    @staticmethod
    def network_health_response(health: Dict) -> str:
        return f"""📊 *NETWORK HEALTH SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100 {health.get('status', '')}
• Total DNs: {health.get('total_dns', 0):,}
• Delivered: {health.get('delivered_dns', 0):,}
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE: {health.get('pod_rate', 0)}%*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK: Rs {health.get('revenue_at_risk', 0):,.2f}*

💡 Type "Executive summary" for detailed analysis"""
    
    @staticmethod
    def revenue_analysis_response(revenue: Dict) -> str:
        return f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized: Rs {revenue.get('realized_revenue', 0):,.2f} ✅
• Pending Delivery: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳
• POD Pending: Rs {revenue.get('pod_pending_revenue', 0):,.2f} 📋

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *REALIZATION RATE: {revenue.get('realization_rate', 0):.1f}%*

💡 Revenue at Risk: Rs {revenue.get('revenue_at_risk', 0):,.2f}"""
    
    @staticmethod
    def outstanding_response(outstanding: Dict) -> str:
        return f"""💰 *OUTSTANDING & PENDING VALUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *VALUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Outstanding: Rs {outstanding.get('outstanding_value', 0):,.2f}
• Pending Delivery: Rs {outstanding.get('pending_delivery', 0):,.2f} ⏳
• POD Pending: Rs {outstanding.get('pod_pending', 0):,.2f} 📋

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *{outstanding.get('percentage_of_revenue', 0):.1f}% of total revenue*

💡 Type "Top risk dealers" for detailed list."""
    
    @staticmethod
    def executive_summary_response(health: Dict, top_dealers: List, risk_dealers: List, cities: List) -> str:
        response = f"""👑 *EXECUTIVE SUMMARY DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• POD Compliance: {health.get('pod_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(top_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - Rs {d['total_value']:,.2f}\n"
        
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 5 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - {d['pending_dns']} pending\n"
        
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌆 *TOP 5 RISK CITIES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, c in enumerate(cities[:5], 1):
            response += f"{i}. {c['city'][:25]} - {c['pending_rate']:.0f}% pending\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Escalate top 5 risk dealers
2. Focus POD collection on risk cities
3. Review warehouse performance

Type "Help" for all commands"""
        
        return response


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    
    def __init__(self, db: Session):
        self.db = db
        self.db_service = DatabaseService(db)
        self.formatter = ResponseFormatter()
        
        # Initialize AI Provider
        self.ai_provider = None
        self.ai_available = False
        
        if AI_PROVIDER_AVAILABLE:
            try:
                self.ai_provider = get_ai_provider_service(db)
                if self.ai_provider:
                    self.ai_available = self.ai_provider.is_available
                    logger.info(f"✅ AI Provider: {'AVAILABLE' if self.ai_available else 'NOT AVAILABLE'}")
            except Exception as e:
                logger.error(f"Failed to get AI provider: {e}")
        
        logger.info("=" * 50)
        logger.info("🚀 AI LOGISTICS INTELLIGENCE ASSISTANT v10.0")
        logger.info(f"GROQ Available: {self.ai_available}")
        logger.info("=" * 50)
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            # Route based on intent
            if intent == IntentType.HELP or intent == IntentType.WELCOME:
                result = self._handle_welcome()
            
            # Dealer Intelligence (1-5)
            elif intent == IntentType.DEALER_DASHBOARD:
                result = self._handle_dealer_dashboard_prompt()
            elif intent == IntentType.DEALER_PERFORMANCE:
                result = self._handle_dealer_performance_prompt()
            elif intent == IntentType.DEALER_RISK:
                result = self._handle_dealer_risk_prompt()
            elif intent == IntentType.DEALER_PENDING:
                result = self._handle_dealer_pending_prompt()
            elif intent == IntentType.DEALER_POD_PENDING:
                result = self._handle_dealer_pod_pending_prompt()
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            
            # DN Intelligence (6-9)
            elif intent == IntentType.DN_STATUS:
                result = self._handle_dn_status_prompt()
            elif intent == IntentType.DN_DETAILS:
                result = self._handle_dn_details_prompt()
            elif intent == IntentType.DN_POD_STATUS:
                result = self._handle_dn_pod_prompt()
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            
            # Operational Analytics (10-12)
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            
            # Financial Analytics (13-14)
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            
            # Executive Intelligence (15)
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            
            # General Query (AI-powered)
            else:
                result = self._handle_general_query(question, user_phone, user_role)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    # ==========================================================
    # WELCOME & HELP
    # ==========================================================
    
    def _handle_welcome(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.welcome()}
    
    # ==========================================================
    # DEALER INTELLIGENCE HANDLERS
    # ==========================================================
    
    def _handle_dealer_dashboard_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dealer_dashboard_prompt()}
    
    def _handle_dealer_performance_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dealer_performance_prompt()}
    
    def _handle_dealer_risk_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dealer_risk_prompt()}
    
    def _handle_dealer_pending_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dealer_pending_prompt()}
    
    def _handle_dealer_pod_pending_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dealer_pod_pending_prompt()}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        result = self.db_service.get_dealer_dashboard(dealer_name)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Dealer not found"))}
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_dealers(20)
        response = self.formatter.top_dealers_response(dealers)
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_risk_dealers(20)
        response = self.formatter.top_risk_dealers_response(dealers)
        return {"success": True, "response": response}
    
    # ==========================================================
    # DN INTELLIGENCE HANDLERS
    # ==========================================================
    
    def _handle_dn_status_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dn_status_prompt()}
    
    def _handle_dn_details_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dn_details_prompt()}
    
    def _handle_dn_pod_prompt(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.dn_pod_prompt()}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        result = self.db_service.get_dn_details(dn_number)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "DN not found"))}
    
    # ==========================================================
    # OPERATIONAL ANALYTICS HANDLERS
    # ==========================================================
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        warehouses = self.db_service.get_warehouse_performance()
        response = self.formatter.warehouse_performance_response(warehouses)
        return {"success": True, "response": response}
    
    def _handle_city_performance(self) -> Dict[str, Any]:
        cities = self.db_service.get_city_performance()
        response = self.formatter.city_performance_response(cities)
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        response = self.formatter.network_health_response(health)
        return {"success": True, "response": response}
    
    # ==========================================================
    # FINANCIAL ANALYTICS HANDLERS
    # ==========================================================
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        revenue = self.db_service.get_revenue_analysis()
        response = self.formatter.revenue_analysis_response(revenue)
        return {"success": True, "response": response}
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        outstanding = self.db_service.get_outstanding_analysis()
        response = self.formatter.outstanding_response(outstanding)
        return {"success": True, "response": response}
    
    # ==========================================================
    # EXECUTIVE INTELLIGENCE HANDLERS
    # ==========================================================
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        top_dealers = self.db_service.get_top_dealers(10)
        risk_dealers = self.db_service.get_top_risk_dealers(10)
        cities = self.db_service.get_city_performance()
        response = self.formatter.executive_summary_response(health, top_dealers, risk_dealers, cities)
        return {"success": True, "response": response}
    
    # ==========================================================
    # GENERAL QUERY (GROQ AI)
    # ==========================================================
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str) -> Dict[str, Any]:
        """Use GROQ AI for general questions"""
        
        logger.info(f"🤖 Processing general query with GROQ: {question[:100]}")
        
        # Try GROQ
        try:
            from app.services.ai_provider_service import get_ai_provider_service
            
            ai_provider = get_ai_provider_service(self.db)
            
            if ai_provider and ai_provider.is_available:
                logger.info("🚀 Calling GROQ API...")
                
                result = ai_provider.answer_question(
                    question=question,
                    user_phone=user_phone,
                    user_role=user_role or "guest"
                )
                
                if result.get("success"):
                    content = result.get("content", "")
                    logger.info(f"✅ GROQ success - Response length: {len(content)}")
                    return {"success": True, "response": content}
                else:
                    logger.warning(f"⚠️ GROQ failed: {result.get('error')}")
            else:
                logger.warning("⚠️ AI Provider not available")
                
        except Exception as e:
            logger.error(f"❌ GROQ error: {e}")
        
        # Fallback response
        response = f"""🤖 *AI LOGISTICS ASSISTANT*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Dealer Intelligence* - Type dealer name, "Top dealers", "Top risk dealers"
🔢 *DN Tracking* - Send 10-digit DN number
👑 *Executive Reports* - "Executive summary", "Network health"
🏭 *Warehouse* - "Warehouse performance"
🌆 *Cities* - "City performance"
💰 *Financial* - "Revenue analysis", "Outstanding analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type "Help" for complete menu."""
        
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
