# ==========================================================
# FILE: app/services/ai_query_service.py (COMPLETE v26.0)
# ==========================================================
# COMPLETE AI QUERY SERVICE WITH GROQ INTEGRATION
# - Direct service calls (no router dependency)
# - GROQ AI for complex queries
# - DN lookup, Dealer lookup, Executive dashboard
# - Help commands and fallback responses
# ==========================================================

import time
import re
import hashlib
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from loguru import logger

from app.config import config


class AIQueryService:
    """Complete AI Query Service - Direct service calls with GROQ AI"""
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        self._groq_service = None
        logger.info("=" * 60)
        logger.info("🚀 AI QUERY SERVICE v26.0 (with GROQ AI)")
        logger.info("   Direct Service Calls + AI Integration")
        logger.info("=" * 60)
    
    def _get_groq_service(self):
        """Lazy load GROQ service"""
        if self._groq_service is None:
            try:
                from app.services.groq_insight_service import GroqInsightService
                self._groq_service = GroqInsightService(self.db)
                logger.info(f"✅ GROQ Service loaded (AI available: {self._groq_service.ai_available})")
            except Exception as e:
                logger.error(f"Failed to load GROQ service: {e}")
                self._groq_service = None
        return self._groq_service
    
    def process_query(
        self, 
        question: str, 
        user_phone: str = None, 
        user_role: str = None
    ) -> Dict[str, Any]:
        """Process query directly - no router dependency"""
        
        self.start_time = time.time()
        request_id = hashlib.md5(f"{user_phone}:{question}".encode()).hexdigest()[:8]
        
        question = question.strip()
        logger.info(f"[{request_id}] 📱 Processing: {question[:100]}")
        
        # ==========================================================
        # DIRECT DN LOOKUP (Highest Priority)
        # ==========================================================
        dn_number = self._extract_dn_number(question)
        
        if dn_number:
            logger.info(f"[{request_id}] 🔢 DN Lookup: {dn_number}")
            result = self._direct_dn_lookup(dn_number)
            if result and "error" not in result:
                response = self._format_dn_response(result, dn_number)
                return {"success": True, "response": response, "request_id": request_id}
            else:
                error_msg = result.get("error", "Not found") if result else "No response"
                return {"success": False, "response": f"🔢 DN {dn_number} not found", "request_id": request_id}
        
        # ==========================================================
        # DIRECT DEALER LOOKUP
        # ==========================================================
        if len(question) > 3 and not question.isdigit() and len(question) < 50:
            logger.info(f"[{request_id}] 🏪 Dealer Lookup: {question}")
            result = self._direct_dealer_lookup(question)
            if result and "error" not in result and result.get("total_dns", 0) > 0:
                response = self._format_dealer_response(result)
                return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # HELP / COMMANDS
        # ==========================================================
        if question.lower() in ["help", "menu", "hi", "hello", "start", "?"]:
            return {"success": True, "response": self._get_help_message(), "request_id": request_id}
        
        # ==========================================================
        # EXECUTIVE SUMMARY
        # ==========================================================
        if any(word in question.lower() for word in ["executive", "ceo", "summary", "dashboard", "kpi"]):
            response = self._direct_executive_summary()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # PENDING PODS
        # ==========================================================
        if any(word in question.lower() for word in ["pending pod", "pod pending", "missing pod"]):
            response = self._direct_pending_pods()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # PENDING PGI
        # ==========================================================
        if any(word in question.lower() for word in ["pending pgi", "pgi pending", "pending dispatch"]):
            response = self._direct_pending_pgi()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # TOP DEALERS
        # ==========================================================
        if any(word in question.lower() for word in ["top dealer", "dealer ranking", "best dealer"]):
            response = self._direct_top_dealers()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # TOP PRODUCTS
        # ==========================================================
        if any(word in question.lower() for word in ["top product", "product ranking", "best product"]):
            response = self._direct_top_products()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # REVENUE ANALYSIS
        # ==========================================================
        if any(word in question.lower() for word in ["revenue", "sales analysis", "financial"]):
            response = self._direct_revenue_analysis()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # CONTROL TOWER / CRITICAL ALERTS
        # ==========================================================
        if any(word in question.lower() for word in ["control tower", "critical alerts", "urgent"]):
            response = self._direct_control_tower()
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # GROQ AI FOR COMPLEX QUERIES (Why, How, What, When)
        # ==========================================================
        groq_keywords = [
            "why", "how", "what", "when", "analyze", "explain", "tell me about",
            "root cause", "trend", "forecast", "predict", "insight", "performance"
        ]
        
        if any(keyword in question.lower() for keyword in groq_keywords):
            logger.info(f"[{request_id}] 🧠 Routing to GROQ AI")
            response = self._direct_groq_analysis(question, request_id)
            return {"success": True, "response": response, "request_id": request_id}
        
        # ==========================================================
        # DEFAULT FALLBACK
        # ==========================================================
        return {
            "success": True,
            "response": self._get_fallback_response(question),
            "request_id": request_id
        }
    
    def _extract_dn_number(self, text: str) -> Optional[str]:
        """Extract DN number from various formats"""
        text = text.strip()
        
        # Just digits
        if re.match(r'^\d{10,15}$', text):
            return text
        
        # DN prefix
        match = re.match(r'^DN\s*(\d{10,15})$', text, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Status of / Track
        match = re.search(r'(?:status|track|of)\s*(\d{10,15})', text, re.IGNORECASE)
        if match:
            return match.group(1)
        
        # Any 10-15 digit number
        match = re.search(r'\b(\d{10,15})\b', text)
        if match:
            return match.group(1)
        
        return None
    
    # ==========================================================
    # DIRECT SERVICE CALLS (Bypass Router)
    # ==========================================================
    
    def _direct_dn_lookup(self, dn_number: str) -> Dict:
        """Direct DN lookup - bypass router"""
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(self.db)
            return service.get_complete_dn_intelligence(dn_number)
        except Exception as e:
            logger.exception(f"DN lookup error: {e}")
            return {"error": str(e)}
    
    def _direct_dealer_lookup(self, dealer_name: str) -> Dict:
        """Direct dealer lookup - bypass router"""
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(self.db)
            return service.get_dealer_dashboard(dealer_name)
        except Exception as e:
            logger.exception(f"Dealer lookup error: {e}")
            return {"error": str(e)}
    
    def _direct_executive_summary(self) -> str:
        """Direct executive summary - bypass router"""
        try:
            from app.services.kpi_service import KPIService
            service = KPIService(self.db)
            result = service.get_executive_dashboard()
            
            if "error" not in result:
                return f"""
👑 *EXECUTIVE SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *SALES*
• Today: Rs {result.get('sales_today', 0):,.2f}
• MTD: Rs {result.get('sales_mtd', 0):,.2f}
• YTD: Rs {result.get('sales_ytd', 0):,.2f}

📦 *OPERATIONS*
• DN Created: {result.get('dns_created', 0)}
• DN Delivered: {result.get('dns_delivered', 0)}
• DN Pending: {result.get('dns_pending', 0)}

⚠️ *PENDING*
• PGI Pending: {result.get('pgi_pending', 0)}
• POD Pending: {result.get('pod_pending', 0)}

📈 *HEALTH SCORE: {result.get('health_score', 0)}/100*

💡 Type "Help" for commands
"""
        except Exception as e:
            logger.exception(f"Executive summary error: {e}")
        
        return "👑 *Executive Summary*\n\nLoading dashboard...\n\n💡 Type 'Help' for commands"
    
    def _direct_pending_pods(self) -> str:
        """Direct pending pods - bypass router"""
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(self.db)
            results = service.get_pending_pods(10)
            
            if not results:
                return "📋 *Pending PODs*\n\n✅ No pending PODs found."
            
            response = "📋 *PENDING PODs*\n\n"
            for i, pod in enumerate(results[:10], 1):
                response += f"{i}. DN: {pod.get('dn_no')}\n"
                response += f"   🏪 {pod.get('dealer', 'Unknown')[:25]}\n"
                response += f"   💰 Rs {pod.get('value', 0):,.2f}\n"
                if pod.get('pending_days'):
                    response += f"   ⏱️ {pod.get('pending_days', 0)} days\n"
                response += "\n"
            return response
        except Exception as e:
            logger.exception(f"Pending pods error: {e}")
            return "📋 *Pending PODs*\n\nLoading data...\n\n💡 Type 'Help' for commands"
    
    def _direct_pending_pgi(self) -> str:
        """Direct pending PGI - bypass router"""
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(self.db)
            results = service.get_pending_pgi(10)
            
            if not results:
                return "⏳ *Pending PGI*\n\n✅ No pending PGI found."
            
            response = "⏳ *PENDING PGI*\n\n"
            for i, pgi in enumerate(results[:10], 1):
                response += f"{i}. DN: {pgi.get('dn_no')}\n"
                response += f"   🏪 {pgi.get('dealer', 'Unknown')[:25]}\n"
                response += f"   💰 Rs {pgi.get('value', 0):,.2f}\n"
                if pgi.get('pending_days'):
                    response += f"   ⏱️ {pgi.get('pending_days', 0)} days\n"
                response += "\n"
            return response
        except Exception as e:
            logger.exception(f"Pending PGI error: {e}")
            return "⏳ *Pending PGI*\n\nLoading data...\n\n💡 Type 'Help' for commands"
    
    def _direct_top_dealers(self) -> str:
        """Direct top dealers - bypass router"""
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(self.db)
            results = service.get_dealer_ranking(10)
            
            if not results:
                return "🏆 *Top Dealers*\n\nNo dealer data available."
            
            response = "🏆 *TOP 10 DEALERS*\n\n"
            for i, d in enumerate(results[:10], 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')[:30]}*\n"
                response += f"   💰 Rs {d.get('total_value', 0):,.2f}\n"
                response += f"   📦 {d.get('total_dns', 0)} DNs\n\n"
            return response
        except Exception as e:
            logger.exception(f"Top dealers error: {e}")
            return "🏆 *Top Dealers*\n\nLoading rankings...\n\n💡 Type 'Help' for commands"
    
    def _direct_top_products(self) -> str:
        """Direct top products - bypass router"""
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(self.db)
            results = service.get_top_products(10)
            
            if not results:
                return "📦 *Top Products*\n\nNo product data available."
            
            response = "📦 *TOP 10 PRODUCTS*\n\n"
            for i, p in enumerate(results[:10], 1):
                response += f"{i}. *{p.get('product', 'Unknown')[:30]}*\n"
                response += f"   💰 Rs {p.get('total_value', 0):,.2f}\n"
                response += f"   📦 {p.get('total_qty', 0):,.0f} units\n\n"
            return response
        except Exception as e:
            logger.exception(f"Top products error: {e}")
            return "📦 *Top Products*\n\nLoading rankings...\n\n💡 Type 'Help' for commands"
    
    def _direct_revenue_analysis(self) -> str:
        """Direct revenue analysis - bypass router"""
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(self.db)
            result = service.get_revenue_analysis()
            
            if "error" not in result:
                return f"""
💰 *REVENUE ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *BREAKDOWN*
• Total Revenue: Rs {result.get('total_revenue', 0):,.2f}
• Realized: Rs {result.get('realized_revenue', 0):,.2f} ✅
• Pending Delivery: Rs {result.get('pending_delivery', 0):,.2f} ⏳
• POD Pending: Rs {result.get('pod_pending_value', 0):,.2f} 📋

📈 *REALIZATION RATE: {result.get('realization_rate', 0)}%*

💡 Focus on pending POD collection to improve realization
"""
        except Exception as e:
            logger.exception(f"Revenue analysis error: {e}")
            return "💰 *Revenue Analysis*\n\nLoading data...\n\n💡 Type 'Help' for commands"
    
    def _direct_control_tower(self) -> str:
        """Direct control tower - bypass router"""
        try:
            from app.services.control_tower_service import ControlTowerService
            service = ControlTowerService(self.db)
            result = service.get_control_tower_dashboard()
            
            response = "🚨 *CONTROL TOWER*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            
            critical_dns = result.get('critical_dns', [])
            if critical_dns:
                response += f"⚠️ *CRITICAL DNS* (>15 days)\n"
                for dn in critical_dns[:5]:
                    response += f"   • {dn.get('dn_no')}: {dn.get('dealer')[:20]} ({dn.get('aging_days', 0)} days)\n"
                response += "\n"
            else:
                response += "✅ No critical delays\n\n"
            
            high_risk = result.get('high_risk_dns', [])
            if high_risk:
                response += f"💰 *HIGH VALUE PENDING*\n"
                for hr in high_risk[:5]:
                    response += f"   • {hr.get('dn_no')}: Rs {hr.get('value', 0):,.2f}\n"
                response += "\n"
            
            critical_pods = result.get('critical_pods', [])
            if critical_pods:
                response += f"📋 *CRITICAL PODS* (>7 days)\n"
                for pod in critical_pods[:5]:
                    response += f"   • {pod.get('dn_no')}: {pod.get('dealer')[:20]} ({pod.get('pending_days', 0)} days)\n"
                response += "\n"
            
            return response
        except Exception as e:
            logger.exception(f"Control tower error: {e}")
            return "🚨 *Control Tower*\n\nLoading critical alerts...\n\n💡 Type 'Help' for commands"
    
    def _direct_groq_analysis(self, question: str, request_id: str) -> str:
        """Direct GROQ AI analysis for complex queries"""
        
        groq_service = self._get_groq_service()
        
        if not groq_service or not groq_service.ai_available:
            logger.warning(f"[{request_id}] GROQ not available, using rule-based response")
            return self._get_ai_fallback_response(question)
        
        try:
            from app.services.intent_engine import IntentType
            
            logger.info(f"[{request_id}] 🤖 Calling GROQ for analysis")
            result = groq_service.analyze(question, IntentType.GENERAL_QUERY, {})
            
            if result and result.get("success"):
                response = result.get("response", "")
                logger.info(f"[{request_id}] ✅ GROQ response received ({len(response)} chars)")
                return response
            else:
                logger.warning(f"[{request_id}] GROQ returned error: {result.get('response', 'Unknown error')}")
                return self._get_ai_fallback_response(question)
                
        except Exception as e:
            logger.exception(f"[{request_id}] GROQ error: {e}")
            return self._get_ai_fallback_response(question)
    
    def _get_ai_fallback_response(self, question: str) -> str:
        """Fallback response when AI is unavailable"""
        return f"""
🤖 *AI INSIGHTS (Fallback Mode)*

I understand you're asking about: "{question[:100]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands instead:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 "6243612278" - Track a DN
🏪 "GB Electronics" - Dealer dashboard
👑 "Executive summary" - View dashboard
📋 "Pending PODs" - Collection status
🚨 "Control tower" - Critical alerts

*For AI-powered insights, configure GROQ API key*
"""
    
    # ==========================================================
    # FORMATTING METHODS
    # ==========================================================
    
    def _format_dn_response(self, data: Dict, dn_number: str) -> str:
        """Format DN response for WhatsApp"""
        
        risk_icon = {
            "Critical": "💀",
            "High": "🚨",
            "Medium": "⚠️",
            "Low": "✅"
        }.get(data.get("risk_level", "Low"), "❓")
        
        status_icon = {
            "Open": "📝",
            "In Transit": "🚚",
            "Delivered": "✅",
            "Closed": "🔒"
        }.get(data.get("status", "Open"), "❓")
        
        delay_icon = data.get("delay_icon", "⚪")
        delay_bucket = data.get("delay_bucket", "On Time")
        
        return f"""
╔══════════════════════════════════════════════════════════════╗
║                    📦 DN COMPLETE REPORT                      ║
║                         {dn_number}                          ║
╚══════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {data.get('dealer', 'N/A')}
   • City: {data.get('city', 'N/A')}
   • Warehouse: {data.get('warehouse', 'N/A')}
   • Division: {data.get('division', 'N/A')}
   • Status: {status_icon} {data.get('status', 'N/A')}
   • Delay: {delay_icon} {delay_bucket}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIALS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {data.get('total_value', 0):,.2f}
   • Total Units: {data.get('total_units', 0):,.0f}
   • Products: {data.get('product_count', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delivery Aging: {data.get('delivery_aging', 0)} days
   • Pending POD: {data.get('pending_pod_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *HEALTH SCORE: {data.get('health_score', 0)}/100*
{risk_icon} Risk: {data.get('risk_level', 'Low')}

💡 Type "Help" for more commands
"""
    
    def _format_dealer_response(self, data: Dict) -> str:
        """Format dealer dashboard response"""
        return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📋 *DEALER: {data.get('dealer', 'N/A')}*

📊 *PERFORMANCE*
• Total DNs: {data.get('total_dns', 0)}
• Pending DNs: {data.get('pending_dns', 0)}
• POD Pending: {data.get('pod_pending', 0)}
• Completion Rate: {data.get('completion_rate', 0)}%

💰 *FINANCIALS*
• Total Value: Rs {data.get('total_value', 0):,.2f}

📈 *HEALTH SCORE: {data.get('health_score', 0)}/100*

💡 Type "Help" for more commands
"""
    
    # ==========================================================
    # HELP AND FALLBACK METHODS
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """🤖 *AI LOGISTICS ASSISTANT v26.0*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "6243612278" - Track a DN
   • "DN 6243612278" - With prefix

🏪 *DEALER INSIGHTS*
   • "GB Electronics" - Dealer dashboard
   • "Top dealers" - Best performers

📦 *PRODUCT INSIGHTS*
   • "Top products" - Best selling products

📋 *POD & PGI*
   • "Pending PODs" - Collection required
   • "Pending PGI" - Dispatch pending

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Dashboard
   • "Revenue analysis" - Financial view

🧠 *AI INSIGHTS* (Powered by GROQ)
   • "Why are deliveries delayed?" - Root cause
   • "What are the trends?" - Trend analysis
   • "Forecast next month sales" - Predictive

🚨 *ALERTS*
   • "Control tower" - Critical alerts

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Just type your question naturally!

*Powered by GROQ AI | Enterprise Logistics Intelligence*
"""
    
    def _get_fallback_response(self, question: str) -> str:
        return f"""
🤖 *AI LOGISTICS ASSISTANT*

I received: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 "6243612278" - Track a DN
🏪 "Top dealers" - Dealer rankings
👑 "Executive summary" - Dashboard
📋 "Pending PODs" - Collection status
🚨 "Control tower" - Critical alerts
❓ "Help" - Complete menu

*Powered by Logistics Intelligence v26.0*
"""


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(
    question: str, 
    db: Session, 
    user_phone: str = None, 
    user_role: str = None
) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# HEALTH CHECK FUNCTION
# ==========================================================

def health_check(db: Session) -> Dict[str, Any]:
    """Check health of AI Query Service"""
    try:
        service = AIQueryService(db)
        groq = service._get_groq_service()
        return {
            "status": "healthy",
            "version": "26.0",
            "groq_ai": groq.ai_available if groq else False,
            "direct_mode": True,
            "router_bypassed": True
        }
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "version": "26.0"
        }
