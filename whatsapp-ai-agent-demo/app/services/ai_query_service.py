# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v5.0)
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.config import config
from app.models import DeliveryReport, AIResponseLog


# ==========================================================
# INTENT TYPES
# ==========================================================

class IntentType(str, Enum):
    DN_LOOKUP = "dn_lookup"
    DEALER_LOOKUP = "dealer_lookup"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_ANALYSIS = "city_analysis"
    WAREHOUSE_ANALYSIS = "warehouse_analysis"
    POD_ANALYSIS = "pod_analysis"
    PENDING_ANALYSIS = "pending_analysis"
    GENERAL_QUERY = "general_query"
    HELP = "help"
    UNKNOWN = "unknown"


# ==========================================================
# INTENT DETECTION ENGINE
# ==========================================================

class IntentDetector:
    """Advanced intent detection for logistics queries"""
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        """Detect DN number (10 digits)"""
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        """Detect intent from natural language"""
        msg_lower = message.lower().strip()
        
        # Help
        if any(word in msg_lower for word in ["help", "menu", "commands", "what can you do"]):
            return IntentType.HELP, None
        
        # DN Lookup (10 digits)
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # Executive Summary
        if any(word in msg_lower for word in ["executive summary", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Network Health
        if any(word in msg_lower for word in ["network health", "health score", "overall health"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Top Risk Dealers
        if any(word in msg_lower for word in ["top risk", "risk dealers", "highest risk", "critical dealers"]):
            return IntentType.TOP_RISK_DEALERS, None
        
        # Top Dealers
        if any(word in msg_lower for word in ["top dealer", "best dealer", "top performing"]):
            return IntentType.TOP_DEALERS, None
        
        # City Analysis
        if any(word in msg_lower for word in ["city", "karachi", "lahore", "islamabad", "multan"]):
            return IntentType.CITY_ANALYSIS, None
        
        # Warehouse Analysis
        if any(word in msg_lower for word in ["warehouse", "godown", "hpk", "lhe"]):
            return IntentType.WAREHOUSE_ANALYSIS, None
        
        # POD Analysis
        if any(word in msg_lower for word in ["pod", "proof of delivery", "acknowledgement"]):
            return IntentType.POD_ANALYSIS, None
        
        # Pending Analysis
        if any(word in msg_lower for word in ["pending", "backlog", "undelivered"]):
            return IntentType.PENDING_ANALYSIS, None
        
        # Dealer Lookup (check if it might be a dealer name)
        if len(msg_lower.split()) <= 4 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, message
        
        # Default to general query
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# DATABASE QUERY SERVICE
# ==========================================================

class DatabaseQueryService:
    """Handle all database queries for logistics data"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_dn_details(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN details from database"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).all()
            
            if not records:
                return {"success": False, "message": f"DN {dn_number} not found"}
            
            record = records[0]
            
            # Calculate ages
            dispatch_age = 0
            pod_age = 0
            if record.dn_create_date:
                dispatch_age = (datetime.now().date() - record.dn_create_date.date()).days
            if record.good_issue_date and record.pod_status == "Pending":
                pod_age = (datetime.now().date() - record.good_issue_date.date()).days
            
            # Determine risk
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
            
            # Format response
            response = f"""╔══════════════════════════════════════════╗
║           📦 DN TRACKING REPORT          ║
║              {dn_number}                    ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Customer: {record.customer_name or 'N/A'}
• Dealer Code: {record.dealer_code or 'N/A'}
• City: {record.ship_to_city or 'N/A'}
• Warehouse: {record.warehouse or 'N/A'}
• Product: {record.product or 'N/A'}
• Quantity: {float(record.dn_qty or 0):,.0f}
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
• Escalation: {'🚨 IMMEDIATE' if risk_level == 'CRITICAL' else '📌 Monitor'}

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
                "data": {
                    "customer": record.customer_name,
                    "city": record.ship_to_city,
                    "warehouse": record.warehouse,
                    "value": float(record.dn_amount or 0),
                    "dispatch_age": dispatch_age,
                    "pod_age": pod_age,
                    "status": "Delivered" if record.pgi_status == "Completed" else "Pending"
                },
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete dealer dashboard"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return {"success": False, "message": f"Dealer '{dealer_name}' not found"}
            
            # Calculate metrics
            total_dns = len(set(str(r.dn_no) for r in records))
            delivered_dns = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed"))
            pending_dns = total_dns - delivered_dns
            pod_pending = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
            
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((delivered_dns - pod_pending) / delivered_dns) * 100 if delivered_dns > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            
            risk_score = 100 - health_score
            if risk_score > 60:
                risk_level = "CRITICAL"
            elif risk_score > 40:
                risk_level = "HIGH"
            elif risk_score > 20:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"
            
            response = f"""╔══════════════════════════════════════════╗
║         📊 DEALER DASHBOARD            ║
║      {dealer_name[:25]}                  ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns}
• Delivered: {delivered_dns} ✅
• Pending: {pending_dns} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• Outstanding: Rs {pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health_score:.1f}/100
• Risk Score: {risk_score:.1f}/100
• Risk Level: {risk_level}
• {'🚨 Immediate attention required' if risk_level in ['HIGH', 'CRITICAL'] else '✅ Monitor regularly'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            if pending_dns > 10:
                response += "• Priority: Clear pending deliveries\n"
            if pod_pending > 5:
                response += "• Action: Collect POD acknowledgements\n"
            if delivery_rate < 80:
                response += "• Escalate: Review delivery process\n"
            
            return {
                "success": True,
                "dealer_name": dealer_name,
                "metrics": {
                    "total_dns": total_dns,
                    "delivered_dns": delivered_dns,
                    "pending_dns": pending_dns,
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
    
    def get_top_dealers(self, limit: int = 10, by: str = "value") -> List[Dict]:
        """Get top dealers by value or delivery"""
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
                desc("total_value") if by == "value" else desc("total_dns")
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
    
    def get_top_risk_dealers(self, limit: int = 10) -> List[Dict]:
        """Get dealers with highest risk (pending deliveries)"""
        try:
            # Get dealers with most pending DNs
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
            
            # POD compliance
            pod_received = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Received"
            ).distinct().count()
            
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
            logger.error(f"Network health error: {e}")
            return {}
    
    def get_city_analysis(self, city_name: str = None) -> Dict[str, Any]:
        """Get city-wise performance"""
        try:
            query = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            )
            
            if city_name:
                query = query.filter(DeliveryReport.ship_to_city.ilike(f"%{city_name}%"))
            
            results = query.all()
            
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
            
            if city_name and cities:
                return cities[0] if cities else None
            
            return {"cities": cities[:10]}
            
        except Exception as e:
            logger.error(f"City analysis error: {e}")
            return {}


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    """Format responses for WhatsApp"""
    
    @staticmethod
    def help_menu() -> str:
        return """╔══════════════════════════════════════════╗
║      📱 WHATSAPP COMMAND CENTER        ║
╚══════════════════════════════════════════╝

🔍 *DN TRACKING*
• Send a 10-digit DN number
• Example: `6243611920`

🏪 *DEALER ANALYTICS*
• Type dealer name (e.g., "Bhatti Electronics")
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

👑 *EXECUTIVE VIEWS*
• "Executive summary" - Management briefing
• "Network health" - Overall performance
• "City analysis" - City-wise breakdown

📊 *OPERATIONAL INSIGHTS*
• "Pending analysis" - Backlog status
• "POD status" - Acknowledgement pending
• "Warehouse analysis" - Hub performance

💡 *Try any of these commands!*"""

    @staticmethod
    def executive_summary(health: Dict, risk_dealers: List, cities: List) -> str:
        """Format executive summary"""
        return f"""╔══════════════════════════════════════════╗
║         👑 EXECUTIVE SUMMARY           ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• POD Compliance: {health.get('pod_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 3 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk_dealers[:3], 1):
            response += f"{i}. {d['name']} - Rs {d['pending_value']:,.2f} pending\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *TOP RISK CITIES*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, c in enumerate(cities.get('cities', [])[:3], 1):
            response += f"{i}. {c['city']} - {c['pending_rate']:.0f}% pending rate\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Escalate top 3 risk dealers immediately
2. Focus POD collection on pending cities
3. Review warehouse processes for delays

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""
        return response


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
            if intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_ANALYSIS:
                result = self._handle_city_analysis(entity or question)
            elif intent == IntentType.PENDING_ANALYSIS:
                result = self._handle_pending_analysis()
            elif intent == IntentType.POD_ANALYSIS:
                result = self._handle_pod_analysis()
            elif intent == IntentType.HELP:
                result = self._handle_help()
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
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        """Handle top dealers request"""
        dealers = self.db_service.get_top_dealers(10, "value")
        
        if not dealers:
            return {"success": True, "response": "No dealer data available."}
        
        response = "📊 *TOP 10 DEALERS BY VALUE*\n\n"
        for i, d in enumerate(dealers, 1):
            response += f"{i}. *{d['name'][:30]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f}\n"
            response += f"   📦 {d['total_dns']} DNs\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        """Handle top risk dealers request"""
        dealers = self.db_service.get_top_risk_dealers(10)
        
        if not dealers:
            return {"success": True, "response": "No risk data available."}
        
        response = "🚨 *TOP 10 RISK DEALERS*\n\n"
        for i, d in enumerate(dealers, 1):
            response += f"{i}. *{d['name'][:30]}*\n"
            response += f"   ⏳ {d['pending_dns']} pending DNs\n"
            response += f"   💰 Rs {d['pending_value']:,.2f} at risk\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        """Handle executive summary request"""
        health = self.db_service.get_network_health()
        risk_dealers = self.db_service.get_top_risk_dealers(5)
        cities = self.db_service.get_city_analysis()
        
        response = self.formatter.executive_summary(health, risk_dealers, cities)
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        """Handle network health request"""
        health = self.db_service.get_network_health()
        
        response = f"""╔══════════════════════════════════════════╗
║         📊 NETWORK HEALTH REPORT        ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Total DNs: {health.get('total_dns', 0)}
• Delivered: {health.get('delivered_dns', 0)} ✅
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• POD Compliance: {health.get('pod_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL IMPACT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'🟢 Network is healthy' if health.get('health_score', 0) > 70 else '🟡 Network needs attention' if health.get('health_score', 0) > 50 else '🔴 Network requires immediate action'}"""
        
        return {"success": True, "response": response}
    
    def _handle_city_analysis(self, query: str) -> Dict[str, Any]:
        """Handle city analysis request"""
        # Extract city name if provided
        cities_data = self.db_service.get_city_analysis()
        
        response = "🌆 *CITY-WISE PERFORMANCE*\n\n"
        for c in cities_data.get('cities', [])[:10]:
            status = "🔴" if c['pending_rate'] > 30 else "🟡" if c['pending_rate'] > 15 else "🟢"
            response += f"{status} *{c['city']}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_pending_analysis(self) -> Dict[str, Any]:
        """Handle pending analysis request"""
        risk_dealers = self.db_service.get_top_risk_dealers(5)
        
        response = "⏳ *PENDING DELIVERY ANALYSIS*\n\n"
        
        if risk_dealers:
            response += "🚨 *HIGHEST PENDING DEALERS*\n"
            for d in risk_dealers[:5]:
                response += f"• {d['name'][:30]}: {d['pending_dns']} DNs (Rs {d['pending_value']:,.2f})\n"
        
        response += "\n💡 *Recommendations:*\n"
        response += "1. Escalate top 5 dealers immediately\n"
        response += "2. Check warehouse capacity\n"
        response += "3. Review dispatch process\n"
        
        return {"success": True, "response": response}
    
    def _handle_pod_analysis(self) -> Dict[str, Any]:
        """Handle POD analysis request"""
        response = """📋 *POD STATUS ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *CURRENT STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• POD Pending DNs: Awaiting acknowledgement
• Impact: Revenue recognition delayed
• Risk: Customer dissatisfaction

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Send daily POD reminders to dealers
2. Implement automated follow-ups
3. Escalate oldest pending PODs
4. Review POD collection process

Type "Top risk dealers" to see affected accounts."""
        
        return {"success": True, "response": response}
    
    def _handle_help(self) -> Dict[str, Any]:
        """Handle help request"""
        return {"success": True, "response": self.formatter.help_menu()}
    
    def _handle_general_query(self, question: str) -> Dict[str, Any]:
        """Handle general queries"""
        response = f"""🤖 *I understand you're asking about: "{question[:50]}"*

Here's how I can help:

📊 *Try these commands:*
• Type a dealer name (e.g., "Bhatti Electronics")
• Send a 10-digit DN number
• "Executive summary" - Management view
• "Top dealers" - Performance ranking
• "Top risk dealers" - Critical accounts
• "Network health" - Overall status
• "City analysis" - Regional performance

💡 *What would you like to know?*"""
        
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
