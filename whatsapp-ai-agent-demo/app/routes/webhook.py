# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v8.0)
# ==========================================================
# FULLY INTEGRATED WITH GROQ AI
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
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


class IntentType(str, Enum):
    HELP = "help"
    DN_LOOKUP = "dn_lookup"
    DEALER_LOOKUP = "dealer_lookup"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_ANALYSIS = "city_analysis"
    WAREHOUSE_ANALYSIS = "warehouse_analysis"
    REVENUE_ANALYSIS = "revenue_analysis"
    GENERAL_QUERY = "general_query"


# ==========================================================
# INTENT DETECTION
# ==========================================================

class IntentDetector:
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        msg_lower = message.lower().strip()
        
        # Help
        if any(word in msg_lower for word in ["help", "menu", "commands", "what can you do", "hello", "hi", "hey"]):
            return IntentType.HELP, None
        
        # DN Lookup
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # Executive Summary
        if any(word in msg_lower for word in ["executive summary", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        # Network Health
        if any(word in msg_lower for word in ["network health", "health score"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Top Risk Dealers
        if any(word in msg_lower for word in ["top risk", "risk dealers"]):
            return IntentType.TOP_RISK_DEALERS, None
        
        # Top Dealers
        if any(word in msg_lower for word in ["top dealer", "best dealer", "top performing"]):
            return IntentType.TOP_DEALERS, None
        
        # City Analysis
        if any(word in msg_lower for word in ["city", "karachi", "lahore", "islamabad"]):
            return IntentType.CITY_ANALYSIS, None
        
        # Warehouse
        if "warehouse" in msg_lower:
            return IntentType.WAREHOUSE_ANALYSIS, None
        
        # Revenue
        if any(word in msg_lower for word in ["revenue", "financial"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        # Dealer lookup (name)
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, message
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# DATABASE SERVICE
# ==========================================================

class DatabaseService:
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_dn_details(self, dn_number: str) -> Dict[str, Any]:
        try:
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).first()
            
            if not record:
                return {"success": False, "message": f"❌ DN {dn_number} not found"}
            
            # Calculate ages safely
            dispatch_age = 0
            if record.dn_create_date:
                if isinstance(record.dn_create_date, datetime):
                    create_date = record.dn_create_date.date()
                else:
                    create_date = record.dn_create_date
                dispatch_age = (datetime.now().date() - create_date).days
            
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery: {'✅ DELIVERED' if record.pgi_status == 'Completed' else '⏳ PENDING'}
• POD: {'✅ RECEIVED' if record.pod_status == 'Received' else '📋 PENDING'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Help" for more commands"""
            
            return {"success": True, "formatted_response": response}
            
        except Exception as e:
            logger.error(f"DN lookup error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return {"success": False, "message": f"❌ Dealer '{dealer_name}' not found"}
            
            total_dns = len(set(str(r.dn_no) for r in records))
            delivered = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed"))
            pending = total_dns - delivered
            pod_pending = len(set(str(r.dn_no) for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"))
            total_value = sum(float(r.dn_amount or 0) for r in records)
            pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
            
            delivery_rate = (delivered / total_dns) * 100 if total_dns > 0 else 0
            health_score = delivery_rate
            
            response = f"""╔══════════════════════════════════════════╗
║         📊 DEALER DASHBOARD            ║
║      {dealer_name[:25]}                  ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
• Delivered: {delivered} ✅
• Pending: {pending} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *HEALTH SCORE: {health_score:.1f}/100*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'✅ Healthy' if health_score >= 80 else '⚠️ Needs Attention' if health_score >= 60 else '🚨 Critical'}

💡 Type "Help" for more commands"""
            
            return {"success": True, "formatted_response": response}
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
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
            logger.error(f"Top dealers error: {e}")
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
            logger.error(f"Top risk dealers error: {e}")
            return []
    
    def get_network_health(self) -> Dict[str, Any]:
        try:
            total_dns = self.db.query(DeliveryReport.dn_no).distinct().count()
            delivered_dns = self.db.query(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").distinct().count()
            pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status != "Completed").scalar() or 0
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "revenue_at_risk": round(float(pending_value), 2),
                "health_score": round(delivery_rate, 1)
            }
        except Exception as e:
            logger.error(f"Network health error: {e}")
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
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        try:
            total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
            pending = total - delivered
            
            return {
                "total_revenue": float(total),
                "delivered_revenue": float(delivered),
                "pending_revenue": float(pending),
                "realization_rate": (delivered / total) * 100 if total > 0 else 0
            }
        except Exception as e:
            logger.error(f"Revenue analysis error: {e}")
            return {}


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    
    def __init__(self, db: Session):
        self.db = db
        self.db_service = DatabaseService(db)
        self.ai_provider = get_ai_provider_service(db) if AI_PROVIDER_AVAILABLE else None
        self.ai_available = self.ai_provider is not None and self.ai_provider.is_available
        
        logger.info("=" * 50)
        logger.info("🚀 AI QUERY SERVICE INITIALIZED")
        logger.info(f"GROQ Available: {self.ai_available}")
        logger.info("=" * 50)
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            if intent == IntentType.HELP:
                result = self._handle_help()
            elif intent == IntentType.DN_LOOKUP:
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
                result = self._handle_city_analysis(entity)
            elif intent == IntentType.WAREHOUSE_ANALYSIS:
                result = self._handle_warehouse_analysis()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            else:
                result = self._handle_general_query(question, user_phone, user_role)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    def _handle_help(self) -> Dict[str, Any]:
        return {"success": True, "response": self._get_help_menu()}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        result = self.db_service.get_dn_details(dn_number)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "DN not found"))}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        result = self.db_service.get_dealer_dashboard(dealer_name)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Dealer not found"))}
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_dealers(20)
        if not dealers:
            return {"success": True, "response": "📊 No dealer data available."}
        
        response = "🏆 *TOP 20 PERFORMING DEALERS*\n\n"
        for i, d in enumerate(dealers, 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n\n"
        return {"success": True, "response": response}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_risk_dealers(20)
        if not dealers:
            return {"success": True, "response": "🚨 No risk data available."}
        
        response = "🚨 *TOP 20 RISK DEALERS*\n\n"
        for i, d in enumerate(dealers, 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   ⏳ {d['pending_dns']} pending | Rs {d['pending_value']:,.2f}\n\n"
        return {"success": True, "response": response}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        top = self.db_service.get_top_dealers(5)
        risk = self.db_service.get_top_risk_dealers(5)
        
        response = f"""👑 *EXECUTIVE SUMMARY*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(top, 1):
            response += f"{i}. {d['name'][:30]} - Rs {d['total_value']:,.2f}\n"
        
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 5 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk, 1):
            response += f"{i}. {d['name'][:30]} - {d['pending_dns']} pending\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type "Help" for all commands"""
        
        return {"success": True, "response": response}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        health = self.db_service.get_network_health()
        
        response = f"""📊 *NETWORK HEALTH REPORT*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Total DNs: {health.get('total_dns', 0):,}
• Delivered: {health.get('delivered_dns', 0):,}
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'🟢 Network is healthy' if health.get('health_score', 0) >= 70 else '🟡 Needs attention'}

💡 Type "Executive summary" for detailed analysis"""
        
        return {"success": True, "response": response}
    
    def _handle_city_analysis(self, city_name: str = None) -> Dict[str, Any]:
        cities = self.db_service.get_city_performance()
        
        if city_name:
            for c in cities:
                if city_name.lower() in c['city'].lower():
                    response = f"""🌆 *CITY: {c['city'].upper()}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {c['total_dns']:,}
• Pending: {c['pending_dns']} ({c['pending_rate']:.0f}%)
• Total Value: Rs {c['total_value']:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{'⚠️ This city needs attention' if c['pending_rate'] > 15 else '✅ Performance is good'}"""
                    return {"success": True, "response": response}
        
        if not cities:
            return {"success": True, "response": "🌆 No city data available."}
        
        response = "🌆 *CITY PERFORMANCE RANKING*\n\n"
        for c in cities[:15]:
            status = "🔴" if c['pending_rate'] > 30 else "🟡" if c['pending_rate'] > 15 else "🟢"
            response += f"{status} *{c['city'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_warehouse_analysis(self) -> Dict[str, Any]:
        return {"success": True, "response": "🏭 *Warehouse Analytics*\n\n📊 Feature coming soon. Type 'Help' for available commands."}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        revenue = self.db_service.get_revenue_analysis()
        
        response = f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized: Rs {revenue.get('delivered_revenue', 0):,.2f} ✅
• Pending: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *REALIZATION RATE: {revenue.get('realization_rate', 0):.1f}%*

💡 Type "Top risk dealers" to see pending value breakdown"""
        
        return {"success": True, "response": response}
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str) -> Dict[str, Any]:
        """Use GROQ AI for general questions"""
        
        # Try to use GROQ
        if self.ai_available and self.ai_provider:
            try:
                result = self.ai_provider.answer_question(
                    question=question,
                    user_phone=user_phone,
                    user_role=user_role or "guest"
                )
                if result.get("success"):
                    return {"success": True, "response": result.get("content")}
            except Exception as e:
                logger.error(f"GROQ error: {e}")
        
        # Fallback to intelligent response
        response = f"""🤖 *I understand you're asking about: "{question[:50]}"*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 • Type a dealer name (e.g., "Bhatti Electronics")
🔢 • Send a 10-digit DN number
👑 • "Executive summary" - Leadership view
🏆 • "Top dealers" - Best performers
🚨 • "Top risk dealers" - Critical accounts
🌆 • "City analysis" - Regional performance
💰 • "Revenue analysis" - Financial view

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Type "Help" for complete menu."""
        
        return {"success": True, "response": response}
    
    def _get_help_menu(self) -> str:
        return """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT*

I can help you with logistics data in real-time!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealers*
• Type a dealer name (e.g., "Bhatti Electronics")
• "Top dealers" - Best performers
• "Top risk dealers" - Critical accounts

🔢 *DN Tracking*
• Send a 10-digit DN number

👑 *Executive Reports*
• "Executive summary"
• "Network health"

🌆 *Cities*
• "City analysis"
• "Karachi analysis"

💰 *Financial*
• "Revenue analysis"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Just type your question naturally!*"""


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
