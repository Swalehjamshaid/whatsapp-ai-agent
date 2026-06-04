# ==========================================================
# FILE: app/services/ai_query_service.py
# ==========================================================
# COMPLETE AI QUERY SERVICE - PRODUCTION READY
# ENHANCED: Intent Classification, Service Discovery, Memory, AI Recommendations

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
import json
import time

from sqlalchemy.orm import Session
from loguru import logger

from app.models import AIResponseLog
from app.config import config
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# Safe import for AI provider
try:
    from app.services.ai_provider_service import ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"AI Provider Service not available: {e}")
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None


# ======================================================
# PRIORITY 4: CONVERSATIONAL MEMORY
# ======================================================

class ConversationMemory:
    """Store conversation context per user"""
    
    def __init__(self):
        self.memories: Dict[str, Dict] = {}
    
    def get(self, user_phone: str) -> Dict:
        """Get memory for a user"""
        if user_phone not in self.memories:
            self.memories[user_phone] = {
                "last_intent": None,
                "last_entity": None,
                "last_city": None,
                "last_dealer": None,
                "last_dn": None,
                "last_question": None,
                "last_response": None,
                "conversation_history": [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        return self.memories[user_phone]
    
    def update(self, user_phone: str, intent: str = None, entity: Any = None,
               city: str = None, dealer: str = None, dn: str = None,
               question: str = None, response: str = None):
        """Update memory for a user"""
        memory = self.get(user_phone)
        
        if intent:
            memory["last_intent"] = intent
        if entity:
            memory["last_entity"] = entity
        if city:
            memory["last_city"] = city
        if dealer:
            memory["last_dealer"] = dealer
        if dn:
            memory["last_dn"] = dn
        if question:
            memory["last_question"] = question
        if response:
            memory["last_response"] = response
        
        # Add to conversation history (keep last 10)
        if question and response:
            memory["conversation_history"].append({
                "question": question,
                "response": response[:200],
                "intent": intent,
                "timestamp": datetime.utcnow().isoformat()
            })
            if len(memory["conversation_history"]) > 10:
                memory["conversation_history"].pop(0)
        
        memory["updated_at"] = datetime.utcnow()
    
    def clear(self, user_phone: str):
        """Clear memory for a user"""
        if user_phone in self.memories:
            del self.memories[user_phone]


# ======================================================
# PRIORITY 1: INTENT CLASSIFICATION ENGINE
# ======================================================

class IntentClassifier:
    """Strict intent hierarchy for classification"""
    
    # Priority order (1 = highest priority)
    INTENT_PRIORITY = {
        "DN": 1,
        "DEALER": 2,
        "CITY": 3,
        "WAREHOUSE": 4,
        "PRODUCT": 5,
        "POD": 6,
        "EXECUTIVE": 7,
        "COMPARISON": 8,
        "RANKING": 9,
        "RISK": 10,
        "PENDING": 11,
        "FORECAST": 12,
        "SERVICE_DISCOVERY": 13,
        "GENERAL": 14,
        "UNKNOWN": 15
    }
    
    # DN patterns (highest priority)
    DN_PATTERNS = [
        r'\b(\d{8,15})\b',  # 8-15 digit numbers
        r'dn[\s:]*(\d{8,15})',
        r'delivery[-\s]?note[\s:]*(\d{8,15})',
        r'track[\s:]*(\d{8,15})'
    ]
    
    # Dealer patterns
    DEALER_PATTERNS = [
        r'(?:dealer|customer)[\s:]+([A-Za-z0-9\s&]+)',
        r'(?:show|get|find)[\s]+(?:dealer|customer)[\s]+([A-Za-z0-9\s&]+)',
        r'(?:dashboard|performance|summary)[\s]+(?:for|of)[\s]+([A-Za-z0-9\s&]+)'
    ]
    
    # City patterns with Pakistan cities
    CITY_PATTERNS = [
        r'(?:in|for|at)[\s]+([A-Za-z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
        r'(karachi|lahore|islamabad|multan|faisalabad|hyderabad|peshawar|quetta|rawalpindi|gujranwala|sialkot)'
    ]
    
    # Warehouse patterns
    WAREHOUSE_PATTERNS = [
        r'(?:warehouse|godown)[\s:]+([A-Za-z0-9]+)',
        r'(?:stock|storage)[\s]+(?:at|in)[\s]+([A-Za-z0-9]+)'
    ]
    
    # Service discovery keywords
    SERVICE_KEYWORDS = [
        "what can you do", "help", "menu", "services", "capabilities",
        "what do you offer", "how can you help", "available services",
        "what services", "features", "what can i ask"
    ]
    
    @classmethod
    def classify(cls, question: str, memory: Dict = None) -> Tuple[str, Optional[str]]:
        """
        Classify question using strict priority hierarchy.
        Returns: (intent, entity)
        """
        question_lower = question.lower().strip()
        question_original = question.strip()
        
        # PRIORITY 1: DN Query
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, question_original, re.IGNORECASE)
            if match:
                return "DN", match.group(1)
        
        # PRIORITY 2: Dealer Query
        if "dealer" in question_lower or "customer" in question_lower:
            for pattern in cls.DEALER_PATTERNS:
                match = re.search(pattern, question_lower)
                if match:
                    return "DEALER", match.group(1).strip().title()
            # If "dealer" mentioned but no pattern match, still try to extract
            words = question_lower.split()
            for i, word in enumerate(words):
                if word in ["dealer", "customer"] and i + 1 < len(words):
                    return "DEALER", words[i + 1].title()
        
        # PRIORITY 3: City Query
        for pattern in cls.CITY_PATTERNS:
            match = re.search(pattern, question_lower)
            if match:
                city = match.group(1).strip().title()
                if len(city) > 1 and len(city) < 30:
                    return "CITY", city
        
        # Check for city situation keywords
        city_keywords = ["situation", "performance", "status", "delivery status"]
        for keyword in city_keywords:
            if keyword in question_lower:
                words = question_lower.split()
                for word in words:
                    if word in ["karachi", "lahore", "islamabad", "multan", "faisalabad", 
                               "hyderabad", "peshawar", "quetta", "rawalpindi"]:
                        return "CITY", word.title()
        
        # PRIORITY 4: Warehouse Query
        if "warehouse" in question_lower or "godown" in question_lower:
            for pattern in cls.WAREHOUSE_PATTERNS:
                match = re.search(pattern, question_lower)
                if match:
                    return "WAREHOUSE", match.group(1).upper()
        
        # PRIORITY 5: Product Query
        if "product" in question_lower or "material" in question_lower or "model" in question_lower:
            product_match = re.search(r'(?:product|material|model)[\s:]+([A-Za-z0-9\-]+)', question_lower)
            if product_match:
                return "PRODUCT", product_match.group(1)
        
        # PRIORITY 6: POD Query
        pod_keywords = ["pod", "acknowledgement", "proof of delivery", "awaiting acknowledgement"]
        if any(kw in question_lower for kw in pod_keywords):
            return "POD", None
        
        # PRIORITY 7: Executive Query
        executive_keywords = ["ceo", "executive", "command center", "what should i focus", 
                             "overview", "kpi", "performance report"]
        if any(kw in question_lower for kw in executive_keywords):
            return "EXECUTIVE", None
        
        # PRIORITY 8: Comparison Query
        if "compare" in question_lower or "versus" in question_lower or "vs" in question_lower:
            compare_match = re.search(r'compare\s+([A-Za-z0-9\s]+)\s+(?:and|vs|versus)\s+([A-Za-z0-9\s]+)', question_lower)
            if compare_match:
                return "COMPARISON", (compare_match.group(1).strip(), compare_match.group(2).strip())
        
        # PRIORITY 9: Ranking Query
        ranking_keywords = ["top", "best", "ranking", "leaderboard", "highest", "lowest", "worst"]
        if any(kw in question_lower for kw in ranking_keywords):
            return "RANKING", None
        
        # PRIORITY 10: Risk Query
        risk_keywords = ["risk", "critical", "urgent", "problem", "issue", "delay", "bottleneck"]
        if any(kw in question_lower for kw in risk_keywords):
            return "RISK", None
        
        # PRIORITY 11: Pending Query
        pending_keywords = ["pending", "backlog", "waiting", "not delivered"]
        if any(kw in question_lower for kw in pending_keywords):
            return "PENDING", None
        
        # PRIORITY 12: Forecast Query
        forecast_keywords = ["forecast", "predict", "trend", "projection", "future", "upcoming"]
        if any(kw in question_lower for kw in forecast_keywords):
            return "FORECAST", None
        
        # PRIORITY 13: Service Discovery
        if any(kw in question_lower for kw in cls.SERVICE_KEYWORDS):
            return "SERVICE_DISCOVERY", None
        
        # PRIORITY 14: General AI (non-logistics)
        general_keywords = ["who is", "what is", "why", "how to", "tell me a", "write", "create", 
                           "joke", "story", "poem", "python", "code", "weather", "news"]
        if any(kw in question_lower for kw in general_keywords):
            return "GENERAL", None
        
        # Check for follow-up questions using memory
        if memory and memory.get("last_intent"):
            follow_up_keywords = ["why", "how", "what about", "tell me more", "explain", "and"]
            if any(kw in question_lower for kw in follow_up_keywords):
                # Return last intent for follow-up
                return memory["last_intent"], memory.get("last_entity")
        
        # PRIORITY 15: Unknown
        return "UNKNOWN", None


# ======================================================
# PRIORITY 2: SERVICE DISCOVERY ENGINE
# ======================================================

class ServiceDiscovery:
    """Service catalog and help system"""
    
    SERVICE_CATALOG = {
        "1. Dealer Analytics": {
            "description": "View dealer performance, pending deliveries, and AI insights",
            "examples": ["Show Afzal dashboard", "Dealer Electro City performance", "Top 10 dealers"]
        },
        "2. Delivery Tracking": {
            "description": "Track specific delivery notes (DN)",
            "examples": ["DN 6243611264", "Track delivery 6243611264", "Status of DN 1234567890"]
        },
        "3. POD Monitoring": {
            "description": "Monitor Proof of Delivery (POD) status and pending acknowledgements",
            "examples": ["Pending POD", "POD aging report", "Awaiting acknowledgement"]
        },
        "4. Warehouse Analytics": {
            "description": "Warehouse performance, efficiency scores, and pending items",
            "examples": ["Warehouse HPK status", "Which warehouse has highest pending?", "Warehouse efficiency"]
        },
        "5. City Analytics": {
            "description": "City-wise delivery performance and risk analysis",
            "examples": ["Karachi situation", "Lahore performance", "City delivery status"]
        },
        "6. Executive Dashboard": {
            "description": "High-level business overview and KPIs",
            "examples": ["Executive summary", "What should I focus on today?", "Top risks"]
        },
        "7. Risk Analysis": {
            "description": "Identify critical dealers, warehouses, and pending items",
            "examples": ["Top risk dealers", "Critical deliveries", "Where are the bottlenecks?"]
        },
        "8. Product Analytics": {
            "description": "Product performance, velocity, and fulfillment rates",
            "examples": ["Product ABC performance", "Which product has highest pending?", "Top products"]
        },
        "9. Rankings & Comparisons": {
            "description": "Compare dealers, warehouses, or cities",
            "examples": ["Compare Dealer A and Dealer B", "Top 10 dealers", "Best warehouse"]
        },
        "10. AI Recommendations": {
            "description": "Get AI-powered insights and action plans",
            "examples": ["What should I do about pending PODs?", "How to improve warehouse efficiency?"]
        },
        "11. General AI Assistant": {
            "description": "Ask any general question",
            "examples": ["Who is Imran Khan?", "Tell me a joke", "What is Python?"]
        }
    }
    
    @classmethod
    def get_full_catalog(cls) -> str:
        """Get complete service catalog"""
        response = "📋 *SERVICE CATALOG*\n\n"
        response += "I can help you with:\n\n"
        
        for service, details in cls.SERVICE_CATALOG.items():
            response += f"*{service}*\n"
            response += f"   📝 {details['description']}\n"
            response += f"   💡 Try: {details['examples'][0]}\n\n"
        
        response += "━━━━━━━━━━━━━━━━━━━━\n"
        response += "💬 *Quick Commands:*\n"
        response += "• `help` - Show this menu\n"
        response += "• `services` - List all services\n"
        response += "• `executive` - Executive dashboard\n"
        response += "• `risks` - Top risks analysis\n"
        
        return response
    
    @classmethod
    def get_quick_help(cls) -> str:
        """Get quick help summary"""
        return """
🤖 *AI LOGISTICS ASSISTANT*

*Quick Examples:*
• `Show Afzal dealer` - Dealer dashboard
• `DN 6243611264` - Track delivery
• `Karachi situation` - City analysis
• `Warehouse HPK` - Warehouse status
• `Pending POD` - POD monitoring
• `Executive summary` - CEO view
• `Top 10 dealers` - Rankings
• `Compare A and B` - Comparison
• `Who is Imran Khan?` - General AI

Type `services` for complete catalog or just ask naturally!
"""
    
    @classmethod
    def get_clarification(cls, unknown_question: str = None) -> str:
        """Get clarification response for unknown questions"""
        response = "❓ *I couldn't identify your request.*\n\n"
        response += cls.get_quick_help()
        response += "\n\n*Please choose a service from above or rephrase your question.*"
        return response


# ======================================================
# PRIORITY 5 & 6: AI RECOMMENDATION LAYER
# ======================================================

class AIRecommendationEngine:
    """Generate AI-powered insights and recommendations"""
    
    @staticmethod
    def generate_dealer_insights(dealer_data: Dict, ai_provider=None, user_phone=None) -> Dict:
        """Generate dealer-specific AI insights"""
        insights = {
            "success": False,
            "summary": "",
            "risks": [],
            "recommendations": [],
            "action_plan": []
        }
        
        if not ai_provider:
            return insights
        
        try:
            context = {
                "type": "dealer",
                "dealer_name": dealer_data.get("dealer_name"),
                "total_dns": dealer_data.get("total_dns", 0),
                "pending_dns": dealer_data.get("pending_dns", 0),
                "pending_value": dealer_data.get("pending_value", 0),
                "pod_pending": dealer_data.get("pod_pending_dns", 0),
                "dispatch_age": dealer_data.get("avg_dispatch_days", 0),
                "risk_score": dealer_data.get("risk_score", 0)
            }
            
            response = ai_provider.answer_question(
                "Analyze this dealer's performance, identify key risks, and provide actionable recommendations.",
                context,
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success"):
                insights["success"] = True
                insights["summary"] = response.get("summary", "")
                insights["risks"] = response.get("risks", [])
                insights["recommendations"] = response.get("recommendations", [])
                
                # Generate action plan
                for risk in insights["risks"][:3]:
                    insights["action_plan"].append({
                        "priority": "HIGH" if "urgent" in risk.lower() else "MEDIUM",
                        "action": f"Address: {risk[:100]}"
                    })
        
        except Exception as e:
            logger.error(f"Dealer insights error: {e}")
        
        return insights
    
    @staticmethod
    def generate_city_insights(city_data: Dict, ai_provider=None, user_phone=None) -> Dict:
        """Generate city-specific AI insights"""
        insights = {
            "success": False,
            "summary": "",
            "risks": [],
            "recommendations": [],
            "top_dealers": []
        }
        
        if not ai_provider:
            return insights
        
        try:
            context = {
                "type": "city",
                "city_name": city_data.get("city"),
                "total_dns": city_data.get("total_dns", 0),
                "pending_dns": city_data.get("pending_dns", 0),
                "delay_rate": city_data.get("delay_rate", 0),
                "performance_score": city_data.get("performance_score", 0)
            }
            
            response = ai_provider.answer_question(
                f"Analyze {city_data.get('city')} city's logistics performance. Identify issues and provide recommendations.",
                context,
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success"):
                insights["success"] = True
                insights["summary"] = response.get("summary", "")
                insights["risks"] = response.get("risks", [])[:3]
                insights["recommendations"] = response.get("recommendations", [])[:3]
        
        except Exception as e:
            logger.error(f"City insights error: {e}")
        
        return insights
    
    @staticmethod
    def generate_executive_insights(executive_data: Dict, ai_provider=None, user_phone=None) -> Dict:
        """Generate executive-level AI insights"""
        insights = {
            "success": False,
            "top_risks": [],
            "top_opportunities": [],
            "top_dealers_action": [],
            "recommendations": [],
            "action_plan": []
        }
        
        if not ai_provider:
            return insights
        
        try:
            context = {
                "type": "executive",
                "data": executive_data
            }
            
            response = ai_provider.analyze_executive(
                executive_data,
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success"):
                insights["success"] = True
                insights["top_risks"] = response.get("risks", [])[:5]
                insights["top_opportunities"] = response.get("opportunities", [])[:5]
                insights["top_dealers_action"] = response.get("dealers_to_watch", [])[:5]
                insights["recommendations"] = response.get("recommendations", [])[:5]
                
                # Generate action plan
                for risk in insights["top_risks"][:3]:
                    insights["action_plan"].append({
                        "priority": "HIGH",
                        "action": risk[:150]
                    })
        
        except Exception as e:
            logger.error(f"Executive insights error: {e}")
        
        return insights


# ======================================================
# RESPONSE FORMATTER WITH AI INSIGHTS
# ======================================================

class ResponseFormatter:
    """Format responses with AI insights and recommendations"""
    
    @staticmethod
    def dealer_response(dealer_name: str, dashboard: Dict, ai_insights: Dict = None) -> str:
        """Format dealer dashboard with AI insights"""
        if dashboard.get("fuzzy"):
            return dashboard.get("summary", "Multiple dealers found")
        
        if not dashboard.get("success"):
            return f"❌ Dealer '{dealer_name}' not found."
        
        response = dashboard.get("formatted_message", "")
        
        # PRIORITY 7: Add AI insights and recommendations
        if ai_insights and ai_insights.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI INTELLIGENCE*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("summary"):
                response += f"📊 *Analysis:*\n{ai_insights['summary'][:200]}\n\n"
            
            if ai_insights.get("risks"):
                response += "⚠️ *Risk Score:*\n"
                for risk in ai_insights["risks"][:3]:
                    response += f"   • {risk}\n"
                response += "\n"
            
            if ai_insights.get("recommendations"):
                response += "💡 *Recommended Actions:*\n"
                for rec in ai_insights["recommendations"][:3]:
                    response += f"   • {rec}\n"
                response += "\n"
            
            if ai_insights.get("action_plan"):
                response += "🎯 *Action Plan:*\n"
                for action in ai_insights["action_plan"][:2]:
                    priority_icon = "🔴" if action.get("priority") == "HIGH" else "🟡"
                    response += f"   {priority_icon} {action.get('action', '')}\n"
        
        return response
    
    @staticmethod
    def city_response(city_name: str, city_data: Dict, ai_insights: Dict = None, 
                      top_dealers: List = None) -> str:
        """PRIORITY 8: Format city response with intelligence"""
        response = f"🌆 *CITY INTELLIGENCE: {city_name.upper()}*\n\n"
        response += f"📊 Total DNs: {city_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending: {city_data.get('pending_dns', 0)} DNs\n"
        response += f"💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}\n"
        response += f"⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%\n"
        response += f"📋 Performance Score: {city_data.get('performance_score', 0)}%\n"
        
        if top_dealers:
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🏪 *TOP DEALERS IN CITY*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            for i, dealer in enumerate(top_dealers[:5], 1):
                response += f"{i}. {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ANALYSIS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:300]}\n\n"
            
            if ai_insights.get("risks"):
                response += "⚠️ *Key Risks:*\n"
                for risk in ai_insights["risks"][:2]:
                    response += f"   • {risk}\n"
                response += "\n"
            
            if ai_insights.get("recommendations"):
                response += "💡 *Recommendations:*\n"
                for rec in ai_insights["recommendations"][:2]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def executive_response(executive_data: Dict, ai_insights: Dict = None) -> str:
        """PRIORITY 6: Format executive response with AI insights"""
        response = executive_data.get("formatted_message", "")
        
        if ai_insights and ai_insights.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *EXECUTIVE AI ADVISOR*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("top_risks"):
                response += "🚨 *TOP 5 RISKS:*\n"
                for i, risk in enumerate(ai_insights["top_risks"][:5], 1):
                    response += f"   {i}. {risk}\n"
                response += "\n"
            
            if ai_insights.get("top_opportunities"):
                response += "🎯 *TOP 5 OPPORTUNITIES:*\n"
                for i, opp in enumerate(ai_insights["top_opportunities"][:5], 1):
                    response += f"   {i}. {opp}\n"
                response += "\n"
            
            if ai_insights.get("top_dealers_action"):
                response += "📋 *DEALERS REQUIRING ACTION:*\n"
                for i, dealer in enumerate(ai_insights["top_dealers_action"][:5], 1):
                    response += f"   {i}. {dealer}\n"
                response += "\n"
            
            if ai_insights.get("recommendations"):
                response += "💡 *AI RECOMMENDATIONS:*\n"
                for rec in ai_insights["recommendations"][:3]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def service_discovery_response() -> str:
        """PRIORITY 2: Service catalog response"""
        return ServiceDiscovery.get_full_catalog()
    
    @staticmethod
    def unknown_response() -> str:
        """PRIORITY 3: Unknown question handler"""
        return ServiceDiscovery.get_clarification()
    
    @staticmethod
    def help_response() -> str:
        """Quick help response"""
        return ServiceDiscovery.get_quick_help()
    
    @staticmethod
    def dn_response(dn_details: Dict) -> str:
        """Format DN details response"""
        if not dn_details.get("success"):
            return f"❌ DN {dn_details.get('dn_no', 'unknown')} not found."
        
        dn_no = dn_details.get("dn_no", "Unknown")
        dealer = dn_details.get("dealer", "Unknown")
        city = dn_details.get("city", "Unknown")
        warehouse = dn_details.get("warehouse", "Unknown")
        status = dn_details.get("status", "Unknown")
        pod_status = dn_details.get("pod_status", "Pending")
        dispatch_age = dn_details.get("dispatch_age", 0)
        pod_age = dn_details.get("pod_age", 0)
        total_qty = dn_details.get("total_quantity", 0)
        total_amount = dn_details.get("total_amount", 0)
        products = dn_details.get("products", [])
        
        dn_date = ""
        if dn_details.get('dn_create_date'):
            if isinstance(dn_details['dn_create_date'], datetime):
                dn_date = dn_details['dn_create_date'].strftime('%d-%b-%Y')
            else:
                dn_date = str(dn_details['dn_create_date'])[:10]
        
        pgi_date = ""
        if dn_details.get('good_issue_date'):
            if isinstance(dn_details['good_issue_date'], datetime):
                pgi_date = dn_details['good_issue_date'].strftime('%d-%b-%Y')
            else:
                pgi_date = str(dn_details['good_issue_date'])[:10]
        
        response = f"🔹 *DN: {dn_no}*\n\n"
        response += f"📋 Dealer: {dealer}\n"
        response += f"📍 City: {city} | 🏭 Warehouse: {warehouse}\n"
        response += f"📅 DN Date: {dn_date}\n"
        response += f"🚚 PGI Date: {pgi_date if pgi_date else 'Not Dispatched'}\n\n"
        response += f"📋 Status: {status}\n"
        response += f"📋 POD: {pod_status}\n"
        response += f"⏱️ Dispatch Age: {dispatch_age} days\n"
        
        if pod_age > 0:
            response += f"⏱️ POD Age: {pod_age} days\n"
        
        response += f"\n📦 Total Qty: {total_qty:,.0f} units\n"
        response += f"💰 Total Value: Rs {total_amount:,.2f}\n\n"
        
        if products:
            response += "📦 *Products:*\n"
            for p in products[:5]:
                response += f"   • {p['product_name']}: {p['quantity']:,.0f} units\n"
            if len(products) > 5:
                response += f"   • +{len(products) - 5} more products\n"
        
        if dispatch_age > 15 or pod_age > 15:
            response += "\n⚠️ *CRITICAL:* This delivery requires immediate attention!"
        
        return response
    
    @staticmethod
    def warehouse_response(warehouse_data: Dict, ai_insights: Dict = None) -> str:
        """Format warehouse response"""
        response = f"🏭 *WAREHOUSE: {warehouse_data.get('warehouse', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {warehouse_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {warehouse_data.get('pending_dns', 0)}\n"
        response += f"📦 Pending Units: {warehouse_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {warehouse_data.get('pending_value', 0):,.2f}\n"
        response += f"📋 POD Pending: {warehouse_data.get('pod_pending_dns', 0)}\n"
        response += f"⚡ Efficiency Score: {warehouse_data.get('efficiency_score', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI RECOMMENDATIONS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            response += ai_insights.get("content", "")[:300]
        
        return response
    
    @staticmethod
    def product_response(product_data: Dict) -> str:
        """Format product response"""
        product = product_data.get("product", {})
        response = f"📦 *PRODUCT: {product.get('product_name', 'Unknown')}*\n\n"
        response += f"📊 Total Qty: {product.get('total_qty', 0):,.0f} units\n"
        response += f"💰 Total Value: Rs {product.get('total_value', 0):,.2f}\n"
        response += f"✅ Fulfillment Rate: {product.get('fulfillment_rate', 0)}%\n"
        response += f"⏳ Pending Qty: {product.get('pending_qty', 0):,.0f} units\n"
        response += f"📋 POD Pending: {product.get('pod_pending_qty', 0):,.0f} units\n"
        response += f"⚡ Velocity: {product.get('velocity', 'Normal')}\n"
        return response
    
    @staticmethod
    def comparison_response(comparison: Dict, entity_type: str) -> str:
        """Format comparison response"""
        if not comparison.get("success"):
            return f"❌ Comparison failed"
        
        entity1 = comparison.get(f"{entity_type}1")
        entity2 = comparison.get(f"{entity_type}2")
        comp_data = comparison.get("comparison", {})
        
        response = f"📊 *COMPARISON: {entity1} vs {entity2}*\n\n"
        
        for metric, data in comp_data.items():
            metric_name = metric.replace("_", " ").title()
            val1 = data.get(entity1, 0)
            val2 = data.get(entity2, 0)
            winner = data.get("winner", "Tie")
            
            winner_icon = "🏆" if winner == entity1 else "🥈" if winner == entity2 else "🤝"
            response += f"📈 *{metric_name}*\n"
            response += f"   {entity1}: {val1:,.0f}\n"
            response += f"   {entity2}: {val2:,.0f}\n"
            response += f"   {winner_icon} Winner: {winner}\n\n"
        
        return response
    
    @staticmethod
    def ranking_response(rankings: Dict, category: str, limit: int = 10) -> str:
        """Format ranking response"""
        if category not in rankings:
            return f"No ranking data available"
        
        data = rankings[category][:limit]
        
        if not data:
            return f"No data found"
        
        response = f"📊 *TOP {limit} {category.replace('_', ' ').upper()}*\n\n"
        
        for i, item in enumerate(data, 1):
            if "dealer" in item:
                response += f"{i}. *{item.get('dealer', 'Unknown')}*\n"
                response += f"   📦 DNs: {item.get('total_dns', 0)}\n"
                response += f"   💰 Value: Rs {item.get('total_value', 0):,.2f}\n\n"
            elif "warehouse" in item:
                response += f"{i}. *{item.get('warehouse', 'Unknown')}*\n"
                response += f"   ⏳ Pending: {item.get('pending_dns', 0)} DNs\n"
                response += f"   ⚡ Efficiency: {item.get('efficiency_score', 0)}%\n\n"
            elif "city" in item:
                response += f"{i}. *{item.get('city', 'Unknown')}*\n"
                response += f"   ⏳ Pending: {item.get('pending_dns', 0)} DNs\n"
                response += f"   ⚡ Performance: {item.get('performance_score', 0)}%\n\n"
        
        return response
    
    @staticmethod
    def pod_response(pod_data: Dict) -> str:
        """Format POD response"""
        response = f"📋 *POD STATUS REPORT*\n\n"
        response += f"📊 Total POD Pending: {pod_data.get('pod_pending_dns', 0)} DNs\n"
        response += f"📦 Pending Units: {pod_data.get('pod_pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {pod_data.get('pod_pending_value', 0):,.2f}\n"
        
        if pod_data.get("urgent_count", 0) > 0:
            response += f"\n⚠️ *URGENT:* {pod_data.get('urgent_count', 0)} DNs > 15 days old"
        
        return response
    
    @staticmethod
    def pending_response(pending_data: Dict) -> str:
        """Format pending response"""
        response = f"⏳ *PENDING DELIVERIES*\n\n"
        response += f"📊 Total Pending DNs: {pending_data.get('pending_dns', 0)}\n"
        response += f"📦 Total Pending Units: {pending_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Total Pending Value: Rs {pending_data.get('pending_value', 0):,.2f}\n"
        return response
    
    @staticmethod
    def risk_response(risk_data: Dict) -> str:
        """Format risk response"""
        response = "🚨 *RISK ASSESSMENT*\n\n"
        
        if risk_data.get("risk_dealers"):
            response += "⚠️ *Top Risk Dealers:*\n"
            for dealer in risk_data.get("risk_dealers", [])[:5]:
                response += f"   • {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
        
        if risk_data.get("action_plan"):
            response += "\n🎯 *Action Plan:*\n"
            for action in risk_data.get("action_plan", [])[:3]:
                response += f"   • {action.get('action', '')}\n"
        
        return response if len(response) > 30 else "No significant risks detected."


# ======================================================
# MAIN AI QUERY SERVICE
# ======================================================

class AIQueryService:
    """
    Complete AI Query Service with intent classification,
    conversational memory, and AI recommendations.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        self.memory = ConversationMemory()  # PRIORITY 4
        
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        self.ai_available = AI_PROVIDER_AVAILABLE and self.ai_enabled
    
    # ======================================================
    # MAIN PROCESSING PIPELINE
    # ======================================================
    
    def process_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """
        Main entry point for processing user questions.
        """
        start_time = time.time()
        question = question.strip()
        
        # PRIORITY 10: Railway debug logging
        logger.info(f"📝 [REQ] Question: {question} | User: {user_phone}")
        
        # Get user memory
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
        # Handle help commands
        if question.lower() in ["help", "menu", "what can you do", "commands", "services"]:
            result = {
                "success": True,
                "response": self.formatter.service_discovery_response(),
                "question_type": "SERVICE_DISCOVERY",
                "ai_used": False
            }
            self.memory.update(user_phone, intent="SERVICE_DISCOVERY", question=question, response=result["response"])
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
        
        # PRIORITY 1: Classify intent
        intent, entity = IntentClassifier.classify(question, user_memory)
        
        # PRIORITY 10: Log classification
        logger.info(f"🏷️ [INTENT] {intent} | Entity: {entity}")
        
        # Route to appropriate handler
        try:
            if intent == "DN":
                result = self._handle_dn_query(entity or question, user_phone)
            elif intent == "DEALER":
                result = self._handle_dealer_query(entity or question, user_phone)
            elif intent == "CITY":
                result = self._handle_city_query(entity or question, user_phone)
            elif intent == "WAREHOUSE":
                result = self._handle_warehouse_query(entity or question, user_phone)
            elif intent == "PRODUCT":
                result = self._handle_product_query(entity or question, user_phone)
            elif intent == "POD":
                result = self._handle_pod_query(user_phone)
            elif intent == "EXECUTIVE":
                result = self._handle_executive_query(user_phone)
            elif intent == "COMPARISON":
                if isinstance(entity, tuple):
                    result = self._handle_comparison_query(entity[0], entity[1], user_phone)
                else:
                    result = self._handle_comparison_query(None, None, user_phone)
            elif intent == "RANKING":
                result = self._handle_ranking_query(question, user_phone)
            elif intent == "RISK":
                result = self._handle_risk_query(user_phone)
            elif intent == "PENDING":
                result = self._handle_pending_query(user_phone)
            elif intent == "FORECAST":
                result = self._handle_forecast_query(user_phone)
            elif intent == "SERVICE_DISCOVERY":
                result = {
                    "success": True,
                    "response": self.formatter.service_discovery_response(),
                    "ai_used": False
                }
            elif intent == "GENERAL":
                result = self._handle_general_query(question, user_phone)
            else:  # UNKNOWN
                result = {
                    "success": True,
                    "response": self.formatter.unknown_response(),
                    "question_type": "UNKNOWN",
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            result = {
                "success": False,
                "response": self.formatter.unknown_response(),
                "error": str(e),
                "ai_used": False
            }
        
        # Update memory
        self.memory.update(
            user_phone,
            intent=intent,
            entity=entity,
            question=question,
            response=result.get("response", "")
        )
        
        # Update specific entity types
        if intent == "CITY" and entity:
            self.memory.update(user_phone, city=entity)
        elif intent == "DEALER" and entity:
            self.memory.update(user_phone, dealer=entity)
        elif intent == "DN" and entity:
            self.memory.update(user_phone, dn=entity)
        
        # Add metadata
        result["question_type"] = intent
        result["entity"] = entity
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        # PRIORITY 10: Log response time and AI usage
        logger.info(f"✅ [RESPONSE] Intent: {intent} | AI: {result.get('ai_used', False)} | Time: {result['processing_time_ms']}ms")
        
        # Log to database
        self._log_query(question, result, user_phone)
        
        return result
    
    # ======================================================
    # HANDLER METHODS
    # ======================================================
    
    def _handle_dealer_query(self, dealer_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle dealer queries with AI insights"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        if not dashboard.get("success"):
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        if dashboard.get("fuzzy"):
            return {
                "success": True,
                "response": dashboard.get("summary", "Multiple dealers found"),
                "ai_used": False
            }
        
        # PRIORITY 7: Generate AI insights for dealer
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_dealer_insights(
                    dashboard, ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI dealer insights error: {e}")
        
        response = self.formatter.dealer_response(dealer_name, dashboard, ai_insights)
        
        return {
            "success": True,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_city_query(self, city_name: str, user_phone: str = None) -> Dict[str, Any]:
        """PRIORITY 8: Handle city queries with intelligence"""
        try:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings()
            else:
                return {
                    "success": False,
                    "response": self.formatter.unknown_response(),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"City error: {e}")
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        city_data = None
        for c in rankings.get("all_cities", []):
            if city_name.lower() in c.get("city", "").lower():
                city_data = c
                break
        
        if not city_data:
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        # Get top dealers in this city
        top_dealers = []
        if hasattr(self.analytics, 'dealer_rankings'):
            try:
                dealer_rankings = self.analytics.dealer_rankings(20)
                for dealer in dealer_rankings.get("by_value", []):
                    if dealer.get("city", "").lower() == city_name.lower():
                        top_dealers.append(dealer)
            except:
                pass
        
        # Generate AI insights
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_city_insights(
                    city_data, ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI city insights error: {e}")
        
        response = self.formatter.city_response(city_name, city_data, ai_insights, top_dealers)
        
        return {
            "success": True,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_executive_query(self, user_phone: str = None) -> Dict[str, Any]:
        """PRIORITY 6: Handle executive queries with AI advisor"""
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
            else:
                executive_data = {"formatted_message": "Executive summary not available"}
        except Exception as e:
            logger.error(f"Executive error: {e}")
            executive_data = {"formatted_message": "Unable to fetch executive summary"}
        
        # Generate AI insights
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_executive_insights(
                    executive_data, ai_provider_service, user_phone
                )
                if ai_insights.get("success"):
                    executive_data["ai_recommendations"] = ai_insights
            except Exception as e:
                logger.error(f"AI executive insights error: {e}")
        
        response = self.formatter.executive_response(executive_data, ai_insights)
        
        return {
            "success": True,
            "response": response,
            "ai_used": ai_insights is not None and ai_insights.get("success", False)
        }
    
    def _handle_general_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """PRIORITY 11: Handle general AI questions"""
        if self.ai_available and ai_provider_service:
            try:
                context = {
                    "type": "general_ai",
                    "question": question,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                ai_response = ai_provider_service.answer_question(
                    question, context, structured=False, user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    return {
                        "success": True,
                        "response": ai_response.get("content", "No response generated."),
                        "ai_used": True,
                        "provider": "DeepSeek"
                    }
            except Exception as e:
                logger.error(f"General AI error: {e}")
        
        return {
            "success": True,
            "response": self.formatter.help_response(),
            "ai_used": False
        }
    
    def _handle_dn_query(self, dn_no: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle DN queries"""
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
        except Exception as e:
            logger.error(f"DN error: {e}")
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        response = self.formatter.dn_response(dn_details)
        
        return {
            "success": dn_details.get("success", False),
            "response": response,
            "ai_used": False
        }
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle warehouse queries"""
        try:
            if hasattr(self.analytics, 'warehouse_rankings'):
                rankings = self.analytics.warehouse_rankings()
            else:
                return {
                    "success": False,
                    "response": self.formatter.unknown_response(),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"Warehouse error: {e}")
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        warehouse_data = None
        for w in rankings.get("all_warehouses", []):
            if warehouse_name.upper() in w.get("warehouse", "").upper():
                warehouse_data = w
                break
        
        if not warehouse_data:
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        response = self.formatter.warehouse_response(warehouse_data)
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_product_query(self, product_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle product queries"""
        try:
            if hasattr(self.analytics, 'product_dashboard'):
                product_data = self.analytics.product_dashboard(product_name)
            else:
                return {
                    "success": False,
                    "response": self.formatter.unknown_response(),
                    "ai_used": False
                }
        except Exception as e:
            logger.error(f"Product error: {e}")
            return {
                "success": False,
                "response": self.formatter.unknown_response(),
                "ai_used": False
            }
        
        response = self.formatter.product_response(product_data)
        
        return {
            "success": product_data.get("success", False),
            "response": response,
            "ai_used": False
        }
    
    def _handle_pod_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle POD queries"""
        try:
            if hasattr(self.analytics, 'pod_metrics'):
                pod_data = self.analytics.pod_metrics()
            else:
                pod_data = {}
        except Exception as e:
            logger.error(f"POD error: {e}")
            pod_data = {}
        
        response = self.formatter.pod_response(pod_data)
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_pending_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle pending queries"""
        try:
            if hasattr(self.analytics, 'pending_metrics'):
                pending_data = self.analytics.pending_metrics()
            else:
                pending_data = {}
        except Exception as e:
            logger.error(f"Pending error: {e}")
            pending_data = {}
        
        response = self.formatter.pending_response(pending_data)
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_risk_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle risk queries"""
        risk_dealers = []
        action_plan = []
        
        try:
            if hasattr(self.analytics, 'top_risk_dealers'):
                risk_dealers = self.analytics.top_risk_dealers(5)
            if hasattr(self.analytics, 'generate_action_plan'):
                action_plan = self.analytics.generate_action_plan()
        except Exception as e:
            logger.error(f"Risk error: {e}")
        
        risk_data = {
            "risk_dealers": risk_dealers,
            "action_plan": action_plan
        }
        
        response = self.formatter.risk_response(risk_data)
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_ranking_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle ranking queries"""
        question_lower = question.lower()
        
        try:
            if "dealer" in question_lower or "customer" in question_lower:
                if hasattr(self.analytics, 'dealer_rankings'):
                    rankings = self.analytics.dealer_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_value", 10)
                else:
                    response = self.formatter.unknown_response()
            elif "warehouse" in question_lower:
                if hasattr(self.analytics, 'warehouse_rankings'):
                    rankings = self.analytics.warehouse_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_efficiency", 10)
                else:
                    response = self.formatter.unknown_response()
            elif "city" in question_lower:
                if hasattr(self.analytics, 'city_rankings'):
                    rankings = self.analytics.city_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_performance", 10)
                else:
                    response = self.formatter.unknown_response()
            else:
                response = self.formatter.unknown_response()
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            response = self.formatter.unknown_response()
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_comparison_query(self, entity1: str, entity2: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle comparison queries"""
        if not entity1 or not entity2:
            return {
                "success": False,
                "response": "Please specify two entities to compare.",
                "ai_used": False
            }
        
        try:
            if hasattr(self.analytics, 'compare_dealers'):
                comparison = self.analytics.compare_dealers(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "dealer")
            else:
                response = self.formatter.unknown_response()
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            response = self.formatter.unknown_response()
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _handle_forecast_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle forecast queries"""
        response = """📈 *FORECASTING*

Forecasting capabilities coming soon!

Future features will include:
• Predictive POD delays
• Delivery time predictions
• Warehouse capacity forecasting
• Demand forecasting

Stay tuned for updates!"""
        
        return {
            "success": True,
            "response": response,
            "ai_used": False
        }
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
        """Log query to database"""
        try:
            log_entry = AIResponseLog(
                question=question[:500],
                response=result.get("response", "")[:2000],
                intent=result.get("question_type", "unknown"),
                confidence=1.0 if result.get("success") else 0.0,
                response_time_ms=result.get("processing_time_ms", 0),
                user_phone=user_phone,
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Log error: {e}")
            self.db.rollback()


# ======================================================
# FACTORY FUNCTIONS
# ======================================================

def get_ai_query_service(db: Session) -> AIQueryService:
    """Get AIQueryService instance"""
    return AIQueryService(db)


def process_whatsapp_query(question: str, db: Session, user_phone: str = None) -> str:
    """Convenience function for WhatsApp integration"""
    service = AIQueryService(db)
    result = service.process_query(question, user_phone)
    return result.get("response", "Unable to process your request. Please try again.")
