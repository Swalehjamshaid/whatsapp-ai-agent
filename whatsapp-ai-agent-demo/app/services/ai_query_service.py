# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v12.0)
# ==========================================================
# ENHANCED WITH:
# - Complete DN Intelligence Dashboard
# - Proper Aging Calculations (PGI Date - DN Create Date)
# - Dealer Summary with DN Search
# - Executive Dealer Dashboard with Rankings
# - Full GROQ Integration with Health Check
# - AI Context with Real Database Data
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
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, and Executive KPIs in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *What You Can Ask:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealers*
• Type a dealer name (e.g., "Exact Trading Co")
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🏭 *Warehouse*
• "Warehouse performance"

🌆 *Cities*
• "City performance"

💰 *Financial*
• "Revenue analysis"
• "Outstanding analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples: "Exact Trading Co", "6243611920", "Executive summary"*"""


# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType(str, Enum):
    HELP = "help"
    WELCOME = "welcome"
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_PERFORMANCE = "city_performance"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    GENERAL_QUERY = "general_query"


# ==========================================================
# ENHANCED INTENT DETECTION
# ==========================================================

class IntentDetector:
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_numbered_command(message: str) -> Tuple[bool, Optional[int]]:
        msg_clean = message.strip()
        if msg_clean.isdigit():
            num = int(msg_clean)
            if 1 <= num <= 15:
                return True, num
        return False, None
    
    @staticmethod
    def is_business_question(message: str) -> bool:
        """Enhanced question detection for business queries"""
        msg_lower = message.lower().strip()
        
        # Question words
        question_words = ["how", "what", "why", "when", "where", "who", "which", "can you", "could you", "please", "tell me"]
        if any(msg_lower.startswith(q) for q in question_words):
            return True
        
        # Ends with question mark
        if msg_lower.endswith("?"):
            return True
        
        # Business analysis keywords
        analysis_keywords = [
            "analysis", "analyze", "improvement", "recommend", "suggest", "advice", "help me",
            "root cause", "why is", "risk", "trend", "forecast", "delay", "performance",
            "issue", "problem", "solution", "fix", "resolve", "optimize", "enhance"
        ]
        if any(word in msg_lower for word in analysis_keywords):
            return True
        
        return False
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        # Check for numbered commands
        is_num, num = IntentDetector.detect_numbered_command(msg_original)
        if is_num:
            command_map = {
                1: IntentType.TOP_DEALERS,
                2: IntentType.TOP_RISK_DEALERS,
                3: IntentType.EXECUTIVE_SUMMARY,
                4: IntentType.NETWORK_HEALTH,
                5: IntentType.CITY_PERFORMANCE,
                6: IntentType.WAREHOUSE_PERFORMANCE,
                7: IntentType.REVENUE_ANALYSIS,
                8: IntentType.OUTSTANDING_ANALYSIS,
            }
            return command_map.get(num, IntentType.HELP), None
        
        # Business questions - Send to AI
        if IntentDetector.is_business_question(msg_original):
            logger.info(f"Business question detected, sending to AI: {msg_original[:50]}")
            return IntentType.GENERAL_QUERY, None
        
        # Help / Welcome
        if any(word in msg_lower for word in ["help", "menu", "commands", "welcome", "start", "hello", "hi", "hey"]):
            return IntentType.HELP, None
        
        # DN Lookup (10 digits)
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # Executive Summary
        if any(word in msg_lower for word in ["executive summary", "executive dashboard", "ceo summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Network Health
        if any(word in msg_lower for word in ["network health", "health score"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Top Risk Dealers
        if any(word in msg_lower for word in ["top risk", "risk dealers", "top 20 risk"]):
            return IntentType.TOP_RISK_DEALERS, None
        
        # Top Dealers
        if any(word in msg_lower for word in ["top dealer", "best dealer", "top performing", "top 20"]):
            return IntentType.TOP_DEALERS, None
        
        # City Performance
        if any(word in msg_lower for word in ["city", "city performance"]):
            return IntentType.CITY_PERFORMANCE, None
        
        # Warehouse Performance
        if any(word in msg_lower for word in ["warehouse", "warehouse performance"]):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        # Revenue Analysis
        if any(word in msg_lower for word in ["revenue", "revenue analysis"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        # Outstanding Analysis
        if any(word in msg_lower for word in ["outstanding", "pending value"]):
            return IntentType.OUTSTANDING_ANALYSIS, None
        
        # Dealer lookup by name
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, msg_original
        
        # Default to general query (AI)
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# ENHANCED DATABASE SERVICE
# ==========================================================

class DatabaseService:
    
    def __init__(self, db: Session):
        self.db = db
    
    # ==========================================================
    # P1: ENHANCED DN INTELLIGENCE DASHBOARD
    # ==========================================================
    
    def get_dn_intelligence_dashboard(self, dn_number: str) -> Dict[str, Any]:
        """
        Complete DN Intelligence Dashboard with:
        - Proper aging calculations (PGI Date - DN Create Date)
        - Delivery aging (Delivery Date - PGI Date)
        - POD aging (POD Date - PGI Date)
        - Dealer summary
        - Risk analysis
        - AI-ready context
        """
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not record:
                return {"success": False, "message": f"❌ DN {dn_number} not found"}
            
            # ==========================================================
            # PROPER AGING CALCULATIONS (P1 FIX)
            # ==========================================================
            
            # 1. Dispatch Aging = PGI Date - DN Creation Date
            dispatch_aging_days = 0
            pgi_date = None
            dn_create_date = None
            
            if record.dn_create_date:
                if isinstance(record.dn_create_date, datetime):
                    dn_create_date = record.dn_create_date.date()
                else:
                    dn_create_date = record.dn_create_date
            
            if hasattr(record, 'good_issue_date') and record.good_issue_date:
                if isinstance(record.good_issue_date, datetime):
                    pgi_date = record.good_issue_date.date()
                else:
                    pgi_date = record.good_issue_date
                
                if dn_create_date and pgi_date:
                    dispatch_aging_days = (pgi_date - dn_create_date).days
            
            # 2. Delivery Aging = Delivery Date - PGI Date (if delivered)
            delivery_aging_days = 0
            delivery_date = None
            
            if record.pgi_status == "Completed":
                if hasattr(record, 'delivery_date') and record.delivery_date:
                    if isinstance(record.delivery_date, datetime):
                        delivery_date = record.delivery_date.date()
                    else:
                        delivery_date = record.delivery_date
                    
                    if pgi_date and delivery_date:
                        delivery_aging_days = (delivery_date - pgi_date).days
            
            # 3. POD Aging = POD Date - PGI Date (if POD received)
            pod_aging_days = 0
            pod_date = None
            
            if record.pod_status == "Received":
                if hasattr(record, 'pod_date') and record.pod_date:
                    if isinstance(record.pod_date, datetime):
                        pod_date = record.pod_date.date()
                    else:
                        pod_date = record.pod_date
                    
                    if pgi_date and pod_date:
                        pod_aging_days = (pod_date - pgi_date).days
            
            # Total Age from creation to today
            total_age_days = 0
            if dn_create_date:
                total_age_days = (datetime.now().date() - dn_create_date).days
            
            # ==========================================================
            # RISK ASSESSMENT
            # ==========================================================
            risk_score = 0
            risk_level = "LOW"
            risk_icon = "🟢"
            
            if dispatch_aging_days > 15:
                risk_score = 90
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif dispatch_aging_days > 10:
                risk_score = 70
                risk_level = "HIGH"
                risk_icon = "🔴"
            elif dispatch_aging_days > 5:
                risk_score = 50
                risk_level = "MEDIUM"
                risk_icon = "🟡"
            elif dispatch_aging_days > 0:
                risk_score = 30
                risk_level = "LOW"
                risk_icon = "🟢"
            
            # ==========================================================
            # DEALER SUMMARY (P1 - Related Dealer Data)
            # ==========================================================
            dealer_records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == record.customer_name
            ).all()
            
            dealer_total_dns = len(set(str(r.dn_no) for r in dealer_records))
            dealer_delivered = len(set(str(r.dn_no) for r in dealer_records if r.pgi_status == "Completed"))
            dealer_pending = dealer_total_dns - dealer_delivered
            dealer_pod_pending = len(set(str(r.dn_no) for r in dealer_records if r.pgi_status == "Completed" and r.pod_status == "Pending"))
            dealer_total_value = sum(float(r.dn_amount or 0) for r in dealer_records)
            dealer_pending_value = sum(float(r.dn_amount or 0) for r in dealer_records if r.pgi_status != "Completed")
            
            dealer_delivery_rate = (dealer_delivered / dealer_total_dns) * 100 if dealer_total_dns > 0 else 0
            dealer_health_score = dealer_delivery_rate
            
            # ==========================================================
            # FORMATTED RESPONSE
            # ==========================================================
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📦 DN COMPLETE INTELLIGENCE REPORT                    ║
║                         {dn_number}                                   ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• DN Number: {dn_number}
• Dealer: {record.customer_name or 'N/A'}
• City: {record.ship_to_city or 'N/A'}
• Warehouse: {record.warehouse or 'N/A'}
• Quantity: {float(record.dn_qty or 0):,.0f} units
• Value: Rs {float(record.dn_amount or 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 *DATES & AGING ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• DN Creation Date: {dn_create_date if dn_create_date else 'N/A'}
• PGI (Goods Issue) Date: {pgi_date if pgi_date else 'N/A'}
• Delivery Date: {delivery_date if delivery_date else 'N/A'}
• POD Date: {pod_date if pod_date else 'N/A'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 🚚 Dispatch Aging (PGI - DN Create): {dispatch_aging_days} days
• 🚛 Transit Aging (Delivery - PGI): {delivery_aging_days} days
• 📋 POD Aging (POD - PGI): {pod_aging_days} days
• 📅 Total Age (Today - Create): {total_age_days} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *CURRENT STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery Status: {'✅ DELIVERED' if record.pgi_status == 'Completed' else '⏳ PENDING'}
• POD Status: {'✅ RECEIVED' if record.pod_status == 'Received' else '📋 PENDING'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Risk Score: {risk_score}/100
• Risk Level: {risk_level}
{'🚨 IMMEDIATE ACTION REQUIRED' if risk_level == 'CRITICAL' else '📌 Monitor regularly' if risk_level == 'HIGH' else '✅ On track'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER SUMMARY - {record.customer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {dealer_total_dns}
• Delivered: {dealer_delivered} ✅
• Pending: {dealer_pending} ⏳
• POD Pending: {dealer_pod_pending} 📋
• Total Value: Rs {dealer_total_value:,.2f}
• Pending Value: Rs {dealer_pending_value:,.2f}
• Health Score: {dealer_health_score:.1f}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if dispatch_aging_days > 10:
                response += "• 🚨 Escalate to warehouse - dispatch delay detected\n"
            if delivery_aging_days > 5:
                response += "• 🚛 Follow up with transporter for delivery\n"
            if record.pod_status == "Pending" and pod_aging_days > 7:
                response += "• 📋 Urgent: Collect POD acknowledgement from dealer\n"
            if dealer_pending > 0:
                response += f"• 📦 Dealer has {dealer_pending} other pending DNs\n"
            if not response:
                response += "• ✅ No action needed - delivery on track\n"
            
            return {
                "success": True,
                "dn_number": dn_number,
                "dealer_name": record.customer_name,
                "dispatch_aging_days": dispatch_aging_days,
                "delivery_aging_days": delivery_aging_days,
                "pod_aging_days": pod_aging_days,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "dealer_summary": {
                    "total_dns": dealer_total_dns,
                    "delivered": dealer_delivered,
                    "pending": dealer_pending,
                    "total_value": dealer_total_value,
                    "health_score": dealer_health_score
                },
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # P2: ENHANCED DEALER EXECUTIVE DASHBOARD
    # ==========================================================
    
    def get_dealer_executive_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Enhanced dealer dashboard with:
        - Rankings
        - Health Score
        - Revenue Contribution
        - Risk Score
        - Top Delayed DNs
        - Average Aging
        - Monthly Trend
        """
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return {"success": False, "message": f"❌ Dealer '{dealer_name}' not found"}
            
            # Basic metrics
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
            risk_score = 100 - health_score
            
            # P2: Dealer Ranking (among all dealers)
            all_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).all()
            
            ranking = 1
            for i, d in enumerate(all_dealers, 1):
                if d.customer_name == dealer_name:
                    ranking = i
                    break
            
            total_all_dealers_value = sum(float(d.total_value or 0) for d in all_dealers)
            revenue_contribution = (total_value / total_all_dealers_value) * 100 if total_all_dealers_value > 0 else 0
            
            # P2: Top Delayed DNs
            delayed_dns = []
            for r in records:
                if r.pgi_status != "Completed":
                    age = 0
                    if r.dn_create_date:
                        if isinstance(r.dn_create_date, datetime):
                            create_date = r.dn_create_date.date()
                        else:
                            create_date = r.dn_create_date
                        age = (datetime.now().date() - create_date).days
                    delayed_dns.append({"dn_no": r.dn_no, "age": age, "value": float(r.dn_amount or 0)})
            
            delayed_dns.sort(key=lambda x: x["age"], reverse=True)
            top_delayed = delayed_dns[:5]
            
            # P2: Average Aging
            ages = []
            for r in records:
                if r.dn_create_date:
                    if isinstance(r.dn_create_date, datetime):
                        create_date = r.dn_create_date.date()
                    else:
                        create_date = r.dn_create_date
                    age = (datetime.now().date() - create_date).days
                    ages.append(age)
            
            avg_aging = sum(ages) / len(ages) if ages else 0
            
            # P2: POD Aging
            pod_ages = []
            for r in records:
                if r.pgi_status == "Completed" and r.pod_status == "Pending" and r.good_issue_date:
                    if isinstance(r.good_issue_date, datetime):
                        issue_date = r.good_issue_date.date()
                    else:
                        issue_date = r.good_issue_date
                    pod_age = (datetime.now().date() - issue_date).days
                    pod_ages.append(pod_age)
            
            avg_pod_aging = sum(pod_ages) / len(pod_ages) if pod_ages else 0
            
            # Risk level
            if risk_score >= 70:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "HIGH"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "MEDIUM"
                risk_icon = "⚠️"
            else:
                risk_level = "LOW"
                risk_icon = "✅"
            
            # Health status
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
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📊 EXECUTIVE DEALER DASHBOARD                       ║
║                    {dealer_name[:30]}                               ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
• Delivered: {delivered} ✅
• Pending: {pending} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• POD Pending Value: Rs {pod_pending_value:,.2f}
• Revenue Contribution: {revenue_contribution:.1f}% of total
• Rank: #{ranking} of {len(all_dealers)} dealers

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK & HEALTH ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{health_icon} Health Score: {health_score:.1f}/100 ({health_status})
{risk_icon} Risk Score: {risk_score:.1f}/100
• Risk Level: {risk_level}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Average Dispatch Aging: {avg_aging:.1f} days
• Average POD Aging: {avg_pod_aging:.1f} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP DELAYED DNS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for d in top_delayed[:3]:
                response += f"• DN {d['dn_no']} - {d['age']} days delayed - Rs {d['value']:,.2f}\n"
            
            response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if pending > 0:
                response += f"• Clear {pending} pending deliveries\n"
            if pod_pending > 0:
                response += f"• Collect POD for {pod_pending} delivered DNs\n"
            if avg_aging > 15:
                response += "• Review dispatch process for delays\n"
            if avg_pod_aging > 10:
                response += "• Implement daily POD follow-up\n"
            
            return {
                "success": True,
                "dealer_name": dealer_name,
                "ranking": ranking,
                "health_score": health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "revenue_contribution": revenue_contribution,
                "avg_aging": avg_aging,
                "avg_pod_aging": avg_pod_aging,
                "top_delayed": top_delayed,
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"Dealer executive dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # SUPPORTING METHODS
    # ==========================================================
    
    def get_top_dealers(self, limit: int = 20) -> List[Dict]:
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
            return []
    
    def get_top_risk_dealers(self, limit: int = 20) -> List[Dict]:
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
            return []
    
    def get_network_health(self) -> Dict[str, Any]:
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
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1),
                "revenue_at_risk": round(float(pending_value), 2)
            }
        except Exception as e:
            return {}
    
    def get_city_performance(self) -> List[Dict]:
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
    
    def get_warehouse_performance(self) -> List[Dict]:
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
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
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
        revenue = self.get_revenue_analysis()
        return {
            "outstanding_value": revenue.get("pending_revenue", 0) + revenue.get("pod_pending_revenue", 0),
            "pending_delivery": revenue.get("pending_revenue", 0),
            "pod_pending": revenue.get("pod_pending_revenue", 0)
        }
    
    def get_executive_context(self) -> Dict[str, Any]:
        """Get complete executive context for AI"""
        return {
            "network_health": self.get_network_health(),
            "top_dealers": self.get_top_dealers(10),
            "top_risk_dealers": self.get_top_risk_dealers(10),
            "city_performance": self.get_city_performance()[:5],
            "warehouse_performance": self.get_warehouse_performance()[:5],
            "revenue_analysis": self.get_revenue_analysis()
        }


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
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
    def network_health_response(health: Dict) -> str:
        return f"""📊 *NETWORK HEALTH SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Total DNs: {health.get('total_dns', 0):,}
• Delivered: {health.get('delivered_dns', 0):,}
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE: {health.get('pod_rate', 0)}%*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK: Rs {health.get('revenue_at_risk', 0):,.2f}*

💡 Type "Executive summary" for detailed analysis"""
    
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

💡 Type "Top risk dealers" for detailed list."""
    
    @staticmethod
    def executive_summary_response(health: Dict, top_dealers: List, risk_dealers: List) -> str:
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
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Escalate top 5 risk dealers
2. Focus POD collection
3. Review pending deliveries

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
                    # P3: GROQ Health Check
                    self.ai_available = self._check_groq_health()
                    logger.info(f"✅ AI Provider: {'AVAILABLE' if self.ai_available else 'NOT AVAILABLE'}")
            except Exception as e:
                logger.error(f"Failed to get AI provider: {e}")
        
        logger.info("=" * 50)
        logger.info("🚀 AI LOGISTICS INTELLIGENCE ASSISTANT v12.0")
        logger.info(f"GROQ Available: {self.ai_available}")
        logger.info("=" * 50)
    
    def _check_groq_health(self) -> bool:
        """P3: GROQ Health Check - Verify AI is working"""
        if not self.ai_provider:
            return False
        
        try:
            result = self.ai_provider.answer_question(
                question="Say 'GROQ is working' in one word.",
                user_role="test"
            )
            if result.get("success"):
                logger.info("✅ GROQ health check passed")
                return True
            else:
                logger.warning(f"⚠️ GROQ health check failed: {result.get('error')}")
                return False
        except Exception as e:
            logger.error(f"❌ GROQ health check error: {e}")
            return False
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            if intent == IntentType.HELP or intent == IntentType.WELCOME:
                result = self._handle_welcome()
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance()
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            else:
                result = self._handle_general_query(question, user_phone, user_role)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    def _handle_welcome(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        """P2: Enhanced dealer executive dashboard"""
        result = self.db_service.get_dealer_executive_dashboard(dealer_name)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Dealer not found"))}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        """P1: Enhanced DN intelligence dashboard"""
        result = self.db_service.get_dn_intelligence_dashboard(dn_number)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "DN not found"))}
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_dealers(20)
        response = self.formatter.top_dealers_response(dealers)
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_risk_dealers(20)
        response = self.formatter.top_risk_dealers_response(dealers)
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        top_dealers = self.db_service.get_top_dealers(10)
        risk_dealers = self.db_service.get_top_risk_dealers(10)
        response = self.formatter.executive_summary_response(health, top_dealers, risk_dealers)
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        response = self.formatter.network_health_response(health)
        return {"success": True, "response": response}
    
    def _handle_city_performance(self) -> Dict[str, Any]:
        cities = self.db_service.get_city_performance()
        response = self.formatter.city_performance_response(cities)
        return {"success": True, "response": response}
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        warehouses = self.db_service.get_warehouse_performance()
        response = self.formatter.warehouse_performance_response(warehouses)
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        revenue = self.db_service.get_revenue_analysis()
        response = self.formatter.revenue_analysis_response(revenue)
        return {"success": True, "response": response}
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        outstanding = self.db_service.get_outstanding_analysis()
        response = self.formatter.outstanding_response(outstanding)
        return {"success": True, "response": response}
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str) -> Dict[str, Any]:
        """P3: Enhanced GROQ with full business context"""
        
        logger.info(f"🤖 Processing general query with GROQ: {question[:100]}")
        
        # Build comprehensive context for AI
        executive_context = self.db_service.get_executive_context()
        
        context_prompt = f"""
BUSINESS CONTEXT:
- Network Health Score: {executive_context.get('network_health', {}).get('health_score', 0)}/100
- Revenue at Risk: Rs {executive_context.get('network_health', {}).get('revenue_at_risk', 0):,.2f}
- Top Dealers: {executive_context.get('top_dealers', [])[:3]}
- Top Risk Dealers: {executive_context.get('top_risk_dealers', [])[:3]}
- City Performance: {executive_context.get('city_performance', [])[:3]}
- Warehouse Performance: {executive_context.get('warehouse_performance', [])[:3]}
"""
        
        # Try GROQ
        try:
            from app.services.ai_provider_service import get_ai_provider_service
            
            ai_provider = get_ai_provider_service(self.db)
            
            if ai_provider and ai_provider.is_available:
                logger.info("🚀 Calling GROQ API with business context...")
                
                result = ai_provider.answer_question(
                    question=f"{context_prompt}\n\nUSER QUESTION: {question}\n\nProvide a helpful, data-driven response for WhatsApp.",
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
            logger.exception(f"❌ GROQ FULL ERROR: {e}")  # P3: Full exception logging
        
        # Fallback response
        response = f"""🤖 *AI LOGISTICS ASSISTANT*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands for instant data:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Dealer Analytics*
• Type a dealer name (e.g., "Exact Trading Co")
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number (complete dashboard with aging)

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🏭 *Warehouse & Cities*
• "Warehouse performance"
• "City performance"

💰 *Financial Analytics*
• "Revenue analysis"
• "Outstanding analysis"

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
