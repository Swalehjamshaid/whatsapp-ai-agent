# ==========================================================
# FILE: app/services/ai_query_service.py
# ==========================================================
# COMPLETE AI QUERY SERVICE - PRODUCTION READY
# ENHANCED: AI Startup Logging, DeepSeek Call Logging, Dealer Lookup,
# Ranking Logic, Conversational Memory, Executive Advisor

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
# CONVERSATIONAL MEMORY
# ======================================================

class ConversationMemory:
    """Store conversation context per user for follow-up questions"""
    
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
    
    def get_context(self, user_phone: str) -> Dict:
        """Get conversation context for follow-up questions"""
        memory = self.get(user_phone)
        return {
            "last_intent": memory.get("last_intent"),
            "last_entity": memory.get("last_entity"),
            "last_city": memory.get("last_city"),
            "last_dealer": memory.get("last_dealer"),
            "last_dn": memory.get("last_dn"),
            "history": memory.get("conversation_history", [])[-3:]
        }
    
    def clear(self, user_phone: str):
        """Clear memory for a user"""
        if user_phone in self.memories:
            del self.memories[user_phone]


# ======================================================
# ENHANCED INTENT CLASSIFIER
# ======================================================

class IntentClassifier:
    """Enhanced intent classification with improved dealer lookup and ranking detection"""
    
    # Known cities for direct detection
    KNOWN_CITIES = [
        "karachi", "lahore", "islamabad", "faisalabad", "multan",
        "peshawar", "quetta", "rawalpindi", "gujranwala", "sialkot",
        "hyderabad", "bahawalpur", "sukkur", "larkana"
    ]
    
    # Ranking keywords
    RANKING_KEYWORDS = [
        "highest", "lowest", "top", "bottom", "best", "worst",
        "largest", "smallest", "most", "least", "maximum", "minimum",
        "ranking", "leaderboard", "top 10", "top 5", "top 3"
    ]
    
    # General AI keywords (non-logistics)
    GENERAL_AI_KEYWORDS = [
        "who is", "what is", "why is", "how to", "tell me about",
        "explain", "describe", "write", "create", "generate",
        "joke", "story", "poem", "python", "code", "programming",
        "weather", "news", "sports", "politics", "history",
        "who won", "what happened", "when did"
    ]
    
    # DN patterns
    DN_PATTERNS = [
        r'\b(\d{8,15})\b',
        r'dn[\s:]*(\d{8,15})',
        r'delivery[-\s]?note[\s:]*(\d{8,15})',
        r'track[\s:]*(\d{8,15})'
    ]
    
    # Dealer indicators
    DEALER_INDICATORS = [
        "dealer", "customer", "dealer dashboard", "dealer summary",
        "dealer performance", "show dealer", "tell me about dealer",
        "dashboard for", "performance of"
    ]
    
    @classmethod
    def classify(cls, question: str, memory: Dict = None, logistics_service=None) -> Tuple[str, Optional[str]]:
        """
        Enhanced classify with improved dealer lookup and ranking detection
        """
        question_lower = question.lower().strip()
        question_original = question.strip()
        
        # Check for General AI questions first (highest priority)
        for keyword in cls.GENERAL_AI_KEYWORDS:
            if keyword in question_lower:
                logistics_keywords = ["dealer", "dn", "delivery", "warehouse", "pod", "pending"]
                if not any(lk in question_lower for lk in logistics_keywords):
                    return "GENERAL", None
        
        # PRIORITY 1: DN Query
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, question_original, re.IGNORECASE)
            if match:
                return "DN", match.group(1)
        
        # PRIORITY 2: Ranking Query
        for keyword in cls.RANKING_KEYWORDS:
            if keyword in question_lower:
                entity_match = re.search(r'(dealer|warehouse|city|product|dn)', question_lower)
                entity = entity_match.group(1) if entity_match else None
                return "RANKING", entity
        
        # Check for ranking patterns
        ranking_patterns = [
            r'which\s+(dealer|warehouse|city|product)\s+(?:has|is)\s+(?:the\s+)?(?:most|least)',
            r'(?:show|get|display)\s+(?:the\s+)?(?:top|bottom)'
        ]
        for pattern in ranking_patterns:
            if re.search(pattern, question_lower):
                entity_match = re.search(r'(dealer|warehouse|city|product)', question_lower)
                entity = entity_match.group(1) if entity_match else None
                return "RANKING", entity
        
        # PRIORITY 3: City Query
        for city in cls.KNOWN_CITIES:
            if city in question_lower:
                return "CITY", city.title()
        
        city_patterns = [
            r'(?:in|for|at)\s+(' + '|'.join(cls.KNOWN_CITIES) + r')',
            r'(' + '|'.join(cls.KNOWN_CITIES) + r')\s+(?:situation|performance|status|delivery)',
            r'what(?:\'s| is)\s+(' + '|'.join(cls.KNOWN_CITIES) + r')\s+(?:situation|status)'
        ]
        for pattern in city_patterns:
            match = re.search(pattern, question_lower)
            if match:
                city = match.group(1).strip().title()
                return "CITY", city
        
        # PRIORITY 4: Executive Query
        executive_keywords = [
            "executive", "ceo", "command center", "what should i focus",
            "overview", "kpi", "performance report", "summary"
        ]
        if any(kw in question_lower for kw in executive_keywords):
            return "EXECUTIVE", None
        
        # PRIORITY 5: Risk Query
        risk_keywords = ["risk", "critical", "urgent", "problem", "issue", "delay", "bottleneck"]
        if any(kw in question_lower for kw in risk_keywords):
            return "RISK", None
        
        # PRIORITY 6: POD Query
        pod_keywords = ["pod", "acknowledgement", "proof of delivery", "awaiting acknowledgement"]
        if any(kw in question_lower for kw in pod_keywords):
            return "POD", None
        
        # PRIORITY 7: Warehouse Query
        warehouse_keywords = ["warehouse", "godown", "stock location", "storage"]
        if any(kw in question_lower for kw in warehouse_keywords):
            warehouse_match = re.search(r'(?:warehouse|godown)[\s:]+([A-Za-z0-9]+)', question_lower)
            if warehouse_match:
                return "WAREHOUSE", warehouse_match.group(1).upper()
            return "WAREHOUSE", None
        
        # PRIORITY 8: Product Query
        product_keywords = ["product", "material", "model", "sku"]
        if any(kw in question_lower for kw in product_keywords):
            product_match = re.search(r'(?:product|material|model)[\s:]+([A-Za-z0-9\-]+)', question_lower)
            if product_match:
                return "PRODUCT", product_match.group(1)
            return "PRODUCT", None
        
        # PRIORITY 9: Pending Query
        pending_keywords = ["pending", "backlog", "waiting", "not delivered"]
        if any(kw in question_lower for kw in pending_keywords):
            return "PENDING", None
        
        # PRIORITY 10: Dealer Query with improved lookup
        # Check for explicit dealer indicators
        if any(indicator in question_lower for indicator in cls.DEALER_INDICATORS):
            for pattern in [
                r'(?:dealer|customer)[\s:]+([A-Za-z0-9\s&]+)',
                r'(?:show|get|find)[\s]+(?:dealer|customer)[\s]+([A-Za-z0-9\s&]+)',
                r'(?:dashboard|performance|summary)[\s]+(?:for|of)[\s]+([A-Za-z0-9\s&]+)'
            ]:
                match = re.search(pattern, question_lower)
                if match:
                    return "DEALER", match.group(1).strip().title()
        
        # IMPROVEMENT: Try full question dealer lookup first (most important fix)
        if logistics_service and hasattr(logistics_service, 'search_dealer'):
            try:
                # First try the entire question as a dealer name
                dealer_match = logistics_service.search_dealer(question_original)
                if dealer_match:
                    logger.info(f"Dealer found via full question lookup: '{question_original}' -> '{dealer_match}'")
                    return "DEALER", dealer_match
            except Exception as e:
                logger.debug(f"Full question dealer search error: {e}")
        
        # Then try single word lookup if question is short
        words = question_original.strip().split()
        if len(words) == 1 and 2 < len(question_original) < 30 and not re.search(r'\d', question_original):
            if logistics_service and hasattr(logistics_service, 'search_dealer'):
                try:
                    dealer_match = logistics_service.search_dealer(question_original)
                    if dealer_match:
                        return "DEALER", dealer_match
                except Exception as e:
                    logger.debug(f"Dealer search error: {e}")
        
        # Also try individual words that might be dealer names
        for word in words:
            if len(word) > 3 and not word in cls.GENERAL_AI_KEYWORDS and not word in cls.RANKING_KEYWORDS:
                if logistics_service and hasattr(logistics_service, 'search_dealer'):
                    try:
                        dealer_match = logistics_service.search_dealer(word)
                        if dealer_match:
                            logger.info(f"Dealer found via word lookup: '{word}' -> '{dealer_match}'")
                            return "DEALER", dealer_match
                    except Exception as e:
                        logger.debug(f"Dealer search error for '{word}': {e}")
        
        # Check for follow-up questions using memory
        if memory and memory.get("last_intent"):
            follow_up_keywords = ["why", "how", "what about", "tell me more", "explain", "and", "also"]
            if any(kw in question_lower for kw in follow_up_keywords):
                return memory["last_intent"], memory.get("last_entity")
        
        # PRIORITY 11: Service Discovery
        service_keywords = ["help", "menu", "services", "what can you do", "capabilities"]
        if any(kw in question_lower for kw in service_keywords):
            return "SERVICE_DISCOVERY", None
        
        # Default to UNKNOWN
        return "UNKNOWN", None


# ======================================================
# AI RECOMMENDATION ENGINE
# ======================================================

class AIRecommendationEngine:
    """Generate AI-powered insights and recommendations"""
    
    @staticmethod
    def generate_pod_insights(pod_data: Dict, ai_provider=None, user_phone=None) -> Dict:
        """Generate AI insights for POD backlog"""
        insights = {
            "success": False,
            "summary": "",
            "recommendations": [],
            "action_plan": []
        }
        
        if not ai_provider:
            return insights
        
        try:
            response = ai_provider.answer_question(
                f"Analyze this POD backlog: {pod_data.get('pod_pending_dns', 0)} pending DNs, "
                f"{pod_data.get('pod_pending_units', 0)} units, {pod_data.get('urgent_count', 0)} urgent. "
                f"Provide insights and recommendations.",
                {"type": "pod_analysis"},
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success"):
                insights["success"] = True
                insights["summary"] = response.get("summary", "")
                insights["recommendations"] = response.get("recommendations", [])[:3]
        except Exception as e:
            logger.error(f"POD insights error: {e}")
        
        return insights
    
    @staticmethod
    def generate_dealer_insights(dealer_data: Dict, ai_provider=None, user_phone=None) -> Dict:
        """Generate AI insights for dealer performance"""
        insights = {
            "success": False,
            "summary": "",
            "risks": [],
            "recommendations": []
        }
        
        if not ai_provider:
            return insights
        
        try:
            response = ai_provider.answer_question(
                f"Analyze this dealer's performance: {dealer_data.get('pending_dns', 0)} pending deliveries, "
                f"{dealer_data.get('pod_pending_dns', 0)} pending PODs. Provide insights.",
                {"type": "dealer_analysis"},
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success"):
                insights["success"] = True
                insights["summary"] = response.get("summary", "")
                insights["risks"] = response.get("risks", [])[:3]
                insights["recommendations"] = response.get("recommendations", [])[:3]
        except Exception as e:
            logger.error(f"Dealer insights error: {e}")
        
        return insights


# ======================================================
# EXECUTIVE ADVISOR
# ======================================================

class ExecutiveAdvisor:
    """Generate executive-level priorities and recommendations"""
    
    @staticmethod
    def generate_priorities(analytics_service, ai_provider=None, user_phone=None) -> Dict:
        """Generate top 3 priorities for executive"""
        priorities = {
            "success": False,
            "priority_1": "",
            "priority_2": "",
            "priority_3": "",
            "recommendations": []
        }
        
        try:
            # Get analytics data
            pending_dns = 0
            pod_pending = 0
            risk_dealers = []
            
            if hasattr(analytics_service, 'pending_metrics'):
                pending = analytics_service.pending_metrics()
                pending_dns = pending.get("pending_dns", 0)
            
            if hasattr(analytics_service, 'pod_metrics'):
                pod = analytics_service.pod_metrics()
                pod_pending = pod.get("pod_pending_dns", 0)
            
            if hasattr(analytics_service, 'top_risk_dealers'):
                risk_dealers = analytics_service.top_risk_dealers(3)
            
            context = {
                "pending_dns": pending_dns,
                "pod_pending": pod_pending,
                "risk_dealers": [d.get("dealer") for d in risk_dealers],
                "type": "executive_priorities"
            }
            
            if ai_provider:
                response = ai_provider.answer_question(
                    "Based on this logistics data, what are the top 3 priorities for today?",
                    context,
                    structured=True,
                    user_phone=user_phone
                )
                
                if response.get("success"):
                    priorities["success"] = True
                    structured = response.get("structured", {})
                    priorities["priority_1"] = structured.get("priority_1", "")
                    priorities["priority_2"] = structured.get("priority_2", "")
                    priorities["priority_3"] = structured.get("priority_3", "")
                    priorities["recommendations"] = structured.get("recommendations", [])[:3]
        except Exception as e:
            logger.error(f"Executive priorities error: {e}")
        
        # Fallback priorities if AI fails
        if not priorities["success"]:
            priorities["success"] = True
            priorities["priority_1"] = f"Clear pending backlog: {pending_dns} DNs pending"
            priorities["priority_2"] = f"Address POD acknowledgements: {pod_pending} pending"
            if risk_dealers:
                priorities["priority_3"] = f"Review top risk dealers: {', '.join([d.get('dealer', 'Unknown') for d in risk_dealers[:2]])}"
        
        return priorities


# ======================================================
# RESPONSE FORMATTER
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
        
        # Add AI insights and recommendations
        if ai_insights and ai_insights.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI INSIGHT*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("summary"):
                response += f"📊 {ai_insights['summary'][:200]}\n\n"
            
            if ai_insights.get("risks"):
                response += "⚠️ *Risks Identified:*\n"
                for risk in ai_insights["risks"][:2]:
                    response += f"   • {risk}\n"
                response += "\n"
            
            if ai_insights.get("recommendations"):
                response += "💡 *Recommended Action:*\n"
                for rec in ai_insights["recommendations"][:2]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def ranking_response(rankings: Dict, category: str, limit: int = 10, sort_by: str = "value") -> str:
        """Format ranking response with flexible sorting"""
        if category not in rankings:
            return f"No ranking data available for {category}"
        
        data = rankings[category][:limit]
        
        if not data:
            return f"No data found"
        
        category_name = category.replace("_", " ").upper()
        sort_display = "PENDING" if sort_by == "pending" else "VALUE" if sort_by == "value" else "SCORE"
        response = f"📊 *{category_name} RANKINGS (by {sort_display})*\n\n"
        
        for i, item in enumerate(data, 1):
            if "dealer" in item:
                response += f"{i}. *{item.get('dealer', 'Unknown')}*\n"
                response += f"   📦 DNs: {item.get('total_dns', 0)}\n"
                response += f"   💰 Value: Rs {item.get('total_value', 0):,.2f}\n"
                pending = item.get('pending_dns', 0)
                if pending > 0:
                    response += f"   ⚠️ Pending: {pending}\n"
                response += "\n"
            elif "warehouse" in item:
                response += f"{i}. *{item.get('warehouse', 'Unknown')}*\n"
                response += f"   📦 DNs: {item.get('total_dns', 0)}\n"
                response += f"   ⚡ Efficiency: {item.get('efficiency_score', 0)}%\n"
                pending = item.get('pending_dns', 0)
                if pending > 0:
                    response += f"   ⏳ Pending: {pending}\n"
                response += "\n"
            elif "city" in item:
                response += f"{i}. *{item.get('city', 'Unknown')}*\n"
                response += f"   📊 Score: {item.get('performance_score', 0)}%\n"
                pending = item.get('pending_dns', 0)
                if pending > 0:
                    response += f"   ⏳ Pending: {pending}\n"
                response += "\n"
        
        return response
    
    @staticmethod
    def city_response(city_name: str, city_data: Dict, ai_insights: Dict = None) -> str:
        """Format city response"""
        response = f"🌆 *CITY: {city_name.upper()}*\n\n"
        response += f"📊 Total DNs: {city_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {city_data.get('pending_dns', 0)}\n"
        response += f"💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}\n"
        response += f"⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%\n"
        response += f"📋 Performance Score: {city_data.get('performance_score', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ANALYSIS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:300]}\n"
        
        return response
    
    @staticmethod
    def executive_response(executive_data: Dict, priorities: Dict = None) -> str:
        """Format executive response with priorities"""
        response = executive_data.get("formatted_message", "")
        
        # Add priorities
        if priorities and priorities.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🎯 *EXECUTIVE PRIORITIES*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if priorities.get("priority_1"):
                response += f"🔴 *Priority 1:* {priorities['priority_1']}\n\n"
            if priorities.get("priority_2"):
                response += f"🟡 *Priority 2:* {priorities['priority_2']}\n\n"
            if priorities.get("priority_3"):
                response += f"🟢 *Priority 3:* {priorities['priority_3']}\n\n"
            
            if priorities.get("recommendations"):
                response += "💡 *Recommendations:*\n"
                for rec in priorities["recommendations"][:2]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def pod_response(pod_data: Dict, ai_insights: Dict = None) -> str:
        """Format POD response with AI insights"""
        response = f"📋 *POD STATUS REPORT*\n\n"
        response += f"📊 Total POD Pending: {pod_data.get('pod_pending_dns', 0)} DNs\n"
        response += f"📦 Pending Units: {pod_data.get('pod_pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {pod_data.get('pod_pending_value', 0):,.2f}\n"
        
        if pod_data.get("urgent_count", 0) > 0:
            response += f"\n⚠️ *URGENT:* {pod_data.get('urgent_count', 0)} DNs older than 15 days\n"
        
        # Add AI insights for POD
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI INSIGHT*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:200]}\n\n"
            
            if ai_insights.get("recommendations"):
                response += "💡 *Recommended Action:*\n"
                for rec in ai_insights["recommendations"][:2]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def service_discovery_response() -> str:
        """Service catalog response"""
        return """
📋 *SERVICE CATALOG*

I can help you with:

📊 *Dealer Analytics*
• "Show Afzal dashboard"
• "Dealer Electro City"
• "Top 10 dealers"

📦 *Delivery Tracking*
• "DN 6243611264"
• "Track delivery"

🌆 *City Intelligence*
• "Karachi situation"
• "Lahore performance"
• "Which city has highest pending?"

🏭 *Warehouse Analytics*
• "Warehouse HPK status"
• "Best warehouse"

📈 *Executive Dashboard*
• "Executive summary"
• "What should I focus on today?"
• "What are the biggest risks?"

📋 *POD Monitoring*
• "Pending POD"
• "POD aging report"

💬 *General Questions*
• "Who is Imran Khan?"
• "Tell me a joke"

Type your question naturally!
"""
    
    @staticmethod
    def help_response() -> str:
        """Quick help response"""
        return """
🤖 *AI LOGISTICS ASSISTANT*

*Quick Examples:*
• `Dealer Afzal` - Dealer dashboard
• `DN 6243611264` - Track delivery
• `Karachi situation` - City analysis
• `Top 10 dealers` - Rankings
• `Executive summary` - CEO view
• `Pending POD` - POD status
• `Who is Imran Khan?` - General AI

Type `services` for complete catalog!
"""
    
    @staticmethod
    def unknown_response() -> str:
        """Unknown question response"""
        return """
❓ I couldn't identify your request.

Type `help` to see what I can do, or `services` for the complete catalog.

Examples:
• `Dealer Afzal` - Dealer dashboard
• `DN 6243611264` - Track delivery
• `Karachi situation` - City analysis
• `Who is Imran Khan?` - General questions
"""
    
    @staticmethod
    def dn_response(dn_details: Dict) -> str:
        """Format DN response"""
        if not dn_details.get("success"):
            return f"❌ DN not found."
        
        dn_no = dn_details.get("dn_no", "Unknown")
        dealer = dn_details.get("dealer", "Unknown")
        status = dn_details.get("status", "Unknown")
        pod_status = dn_details.get("pod_status", "Pending")
        
        response = f"🔹 *DN: {dn_no}*\n\n"
        response += f"📋 Dealer: {dealer}\n"
        response += f"📋 Status: {status}\n"
        response += f"📋 POD: {pod_status}\n"
        
        if dn_details.get("dispatch_age", 0) > 15:
            response += "\n⚠️ *CRITICAL:* Requires immediate attention!"
        
        return response
    
    @staticmethod
    def warehouse_response(warehouse_data: Dict, sort_by: str = "efficiency") -> str:
        """Format warehouse response"""
        response = f"🏭 *WAREHOUSE: {warehouse_data.get('warehouse', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {warehouse_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {warehouse_data.get('pending_dns', 0)}\n"
        response += f"📦 Pending Units: {warehouse_data.get('pending_units', 0):,.0f}\n"
        response += f"⚡ Efficiency Score: {warehouse_data.get('efficiency_score', 0)}%\n"
        return response
    
    @staticmethod
    def product_response(product_data: Dict) -> str:
        """Format product response"""
        product = product_data.get("product", {})
        response = f"📦 *PRODUCT: {product.get('product_name', 'Unknown')}*\n\n"
        response += f"📊 Total Qty: {product.get('total_qty', 0):,.0f} units\n"
        response += f"✅ Fulfillment Rate: {product.get('fulfillment_rate', 0)}%\n"
        response += f"⏳ Pending Qty: {product.get('pending_qty', 0):,.0f} units\n"
        return response
    
    @staticmethod
    def pending_response(pending_data: Dict) -> str:
        """Format pending response"""
        response = f"⏳ *PENDING DELIVERIES*\n\n"
        response += f"📊 Total Pending: {pending_data.get('pending_dns', 0)} DNs\n"
        response += f"📦 Pending Units: {pending_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {pending_data.get('pending_value', 0):,.2f}\n"
        return response
    
    @staticmethod
    def risk_response(risk_data: Dict) -> str:
        """Format risk response"""
        response = "🚨 *RISK ASSESSMENT*\n\n"
        
        if risk_data.get("risk_dealers"):
            response += "⚠️ *Top Risk Dealers:*\n"
            for dealer in risk_data.get("risk_dealers", [])[:5]:
                pending = dealer.get('pending_dns', 0)
                response += f"   • {dealer.get('dealer', 'Unknown')}: {pending} pending\n"
        
        if risk_data.get("action_plan"):
            response += f"\n🎯 *Action Plan:*\n"
            for action in risk_data.get("action_plan", [])[:3]:
                response += f"   • {action.get('action', '')}\n"
        
        return response if len(response) > 30 else "No significant risks detected."


# ======================================================
# MAIN AI QUERY SERVICE
# ======================================================

class AIQueryService:
    """
    Complete AI Query Service with all improvements:
    - AI Startup Logging
    - DeepSeek Call Logging
    - Improved Dealer Lookup
    - Enhanced Ranking Logic
    - Conversational Memory
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        self.memory = ConversationMemory()
        
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        self.ai_available = AI_PROVIDER_AVAILABLE and self.ai_enabled
        
        # IMPROVEMENT 1: AI startup logging - Highest Priority
        logger.info("=" * 50)
        logger.info("🚀 AI QUERY SERVICE INITIALIZED")
        logger.info(f"AI_PROVIDER_AVAILABLE={AI_PROVIDER_AVAILABLE}")
        logger.info(f"ENABLE_DEEPSEEK_LOGISTICS={getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False)}")
        logger.info(f"AI_ANALYSIS_ENABLED={getattr(config, 'AI_ANALYSIS_ENABLED', False)}")
        logger.info(f"AI_ENABLED={self.ai_enabled}")
        logger.info(f"AI_AVAILABLE={self.ai_available}")
        logger.info(f"AI_PROVIDER={getattr(config, 'AI_PROVIDER', 'NONE')}")
        logger.info(f"DEEPSEEK_API_KEY={'SET' if getattr(config, 'DEEPSEEK_API_KEY', None) else 'NOT SET'}")
        logger.info("=" * 50)
    
    # ======================================================
    # MAIN PROCESSING PIPELINE
    # ======================================================
    
    def process_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """
        Main entry point for processing user questions.
        """
        start_time = time.time()
        question = question.strip()
        
        # Get user memory for context
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
        # Log incoming request
        logger.info(f"📝 PROCESSING: {question} | User: {user_phone}")
        
        # Quick responses
        if question.lower() in ["help", "menu", "services", "what can you do"]:
            result = {
                "success": True,
                "response": self.formatter.service_discovery_response(),
                "question_type": "SERVICE_DISCOVERY",
                "ai_used": False
            }
            self.memory.update(user_phone, intent="SERVICE_DISCOVERY", question=question, response=result["response"])
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
        
        # Classify intent with logistics service for dealer lookup
        intent, entity = IntentClassifier.classify(question, user_memory, self.logistics)
        
        # Log classification result
        logger.info(f"🏷️ CLASSIFIED: Question='{question}' Intent='{intent}' Entity='{entity}'")
        
        # Route to handlers
        try:
            if intent == "DN":
                result = self._handle_dn_query(entity or question, user_phone)
            elif intent == "RANKING":
                result = self._handle_ranking_query(question, user_phone)
            elif intent == "CITY":
                result = self._handle_city_query(entity or question, user_phone)
            elif intent == "EXECUTIVE":
                result = self._handle_executive_query(user_phone)
            elif intent == "RISK":
                result = self._handle_risk_query(user_phone)
            elif intent == "POD":
                result = self._handle_pod_query(user_phone)
            elif intent == "WAREHOUSE":
                result = self._handle_warehouse_query(entity or question, user_phone)
            elif intent == "PRODUCT":
                result = self._handle_product_query(entity or question, user_phone)
            elif intent == "DEALER":
                result = self._handle_dealer_query(entity or question, user_phone)
            elif intent == "PENDING":
                result = self._handle_pending_query(user_phone)
            elif intent == "GENERAL":
                result = self._handle_general_query(question, user_phone)
            elif intent == "SERVICE_DISCOVERY":
                result = {
                    "success": True,
                    "response": self.formatter.service_discovery_response(),
                    "ai_used": False
                }
            else:  # UNKNOWN
                result = self._handle_unknown_query(question, user_phone)
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
        
        if intent == "CITY" and entity:
            self.memory.update(user_phone, city=entity)
        elif intent == "DEALER" and entity:
            self.memory.update(user_phone, dealer=entity)
        elif intent == "DN" and entity:
            self.memory.update(user_phone, dn=entity)
        
        result["question_type"] = intent
        result["entity"] = entity
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ COMPLETED: Intent={intent} | AI={result.get('ai_used', False)} | Time={result['processing_time_ms']}ms")
        
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
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        if not dashboard.get("success"):
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        if dashboard.get("fuzzy"):
            return {"success": True, "response": dashboard.get("summary", "Multiple dealers found"), "ai_used": False}
        
        # Generate AI insights for dealer
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_dealer_insights(
                    dashboard, ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI dealer insights error: {e}")
        
        response = self.formatter.dealer_response(dealer_name, dashboard, ai_insights)
        
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_city_query(self, city_name: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle city queries"""
        try:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings()
            else:
                return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        except Exception as e:
            logger.error(f"City error: {e}")
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        city_data = None
        for c in rankings.get("all_cities", []):
            if city_name.lower() in c.get("city", "").lower():
                city_data = c
                break
        
        if not city_data:
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        response = self.formatter.city_response(city_name, city_data)
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_executive_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle executive queries with priorities"""
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
            else:
                executive_data = {"formatted_message": "Executive summary not available"}
        except Exception as e:
            logger.error(f"Executive error: {e}")
            executive_data = {"formatted_message": "Unable to fetch executive summary"}
        
        # Generate priorities
        priorities = ExecutiveAdvisor.generate_priorities(
            self.analytics, ai_provider_service if self.ai_available else None, user_phone
        )
        
        response = self.formatter.executive_response(executive_data, priorities)
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_ranking_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle ranking queries with improved sorting logic"""
        question_lower = question.lower()
        
        # Determine what to rank
        if "dealer" in question_lower or "customer" in question_lower:
            if hasattr(self.analytics, 'dealer_rankings'):
                rankings = self.analytics.dealer_rankings(10)
                
                # Check if sorting by pending is requested
                if "pending" in question_lower:
                    # Sort by pending_dns if available
                    if "by_value" in rankings:
                        rankings["by_value"] = sorted(
                            rankings.get("by_value", []),
                            key=lambda x: x.get("pending_dns", 0),
                            reverse=True
                        )
                    response = self.formatter.ranking_response(rankings, "by_value", 10, sort_by="pending")
                else:
                    response = self.formatter.ranking_response(rankings, "by_value", 10, sort_by="value")
            else:
                response = self.formatter.unknown_response()
                
        elif "warehouse" in question_lower:
            if hasattr(self.analytics, 'warehouse_rankings'):
                rankings = self.analytics.warehouse_rankings(10)
                
                # Check if sorting by pending is requested
                if "pending" in question_lower:
                    if "all_warehouses" in rankings:
                        rankings["all_warehouses"] = sorted(
                            rankings.get("all_warehouses", []),
                            key=lambda x: x.get("pending_dns", 0),
                            reverse=True
                        )
                    response = self.formatter.ranking_response(rankings, "all_warehouses", 10, sort_by="pending")
                else:
                    response = self.formatter.ranking_response(rankings, "by_efficiency", 10, sort_by="score")
            else:
                response = self.formatter.unknown_response()
                
        elif "city" in question_lower:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings(10)
                
                # Check if sorting by pending is requested
                if "pending" in question_lower:
                    if "all_cities" in rankings:
                        rankings["all_cities"] = sorted(
                            rankings.get("all_cities", []),
                            key=lambda x: x.get("pending_dns", 0),
                            reverse=True
                        )
                    response = self.formatter.ranking_response(rankings, "all_cities", 10, sort_by="pending")
                else:
                    response = self.formatter.ranking_response(rankings, "by_performance", 10, sort_by="score")
            else:
                response = self.formatter.unknown_response()
        else:
            response = self.formatter.unknown_response()
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_pod_query(self, user_phone: str = None) -> Dict[str, Any]:
        """Handle POD queries with AI insights"""
        try:
            if hasattr(self.analytics, 'pod_metrics'):
                pod_data = self.analytics.pod_metrics()
            else:
                pod_data = {}
        except Exception as e:
            logger.error(f"POD error: {e}")
            pod_data = {}
        
        # Generate AI insights for POD
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_pod_insights(
                    pod_data, ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI POD insights error: {e}")
        
        response = self.formatter.pod_response(pod_data, ai_insights)
        
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_general_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle general AI questions with DeepSeek logging"""
        if self.ai_available and ai_provider_service:
            try:
                # IMPROVEMENT 2: DeepSeek call logging
                logger.info(f"🔍 CALLING DEEPSEEK: {question}")
                
                context = {
                    "type": "general_ai",
                    "question": question,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                ai_response = ai_provider_service.answer_question(
                    question, context, structured=False, user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    logger.info(f"✅ DEEPSEEK RESPONSE RECEIVED (length: {len(ai_response.get('content', ''))} chars)")
                    return {
                        "success": True,
                        "response": ai_response.get("content", "No response generated."),
                        "ai_used": True,
                        "provider": "DeepSeek"
                    }
                else:
                    logger.warning(f"⚠️ DEEPSEEK RESPONSE FAILED: {ai_response.get('error', 'Unknown error')}")
            except Exception as e:
                logger.error(f"❌ DEEPSEEK CALL ERROR: {e}")
        
        return {"success": True, "response": self.formatter.help_response(), "ai_used": False}
    
    def _handle_unknown_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Handle unknown queries - try AI first"""
        if self.ai_available and ai_provider_service:
            try:
                logger.info(f"🔍 CALLING DEEPSEEK (unknown query): {question}")
                
                context = {"type": "unknown_query", "question": question}
                ai_response = ai_provider_service.answer_question(
                    question, context, structured=False, user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    logger.info(f"✅ DEEPSEEK RESPONSE RECEIVED for unknown query")
                    return {
                        "success": True,
                        "response": ai_response.get("content", self.formatter.unknown_response()),
                        "ai_used": True
                    }
            except Exception as e:
                logger.error(f"Unknown query AI error: {e}")
        
        return {"success": True, "response": self.formatter.unknown_response(), "ai_used": False}
    
    def _handle_dn_query(self, dn_no: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
        except Exception as e:
            logger.error(f"DN error: {e}")
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        response = self.formatter.dn_response(dn_details)
        
        return {"success": dn_details.get("success", False), "response": response, "ai_used": False}
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'warehouse_rankings'):
                rankings = self.analytics.warehouse_rankings()
            else:
                return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        except Exception as e:
            logger.error(f"Warehouse error: {e}")
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        warehouse_data = None
        for w in rankings.get("all_warehouses", []):
            if warehouse_name.upper() in w.get("warehouse", "").upper():
                warehouse_data = w
                break
        
        if not warehouse_data:
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        response = self.formatter.warehouse_response(warehouse_data)
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_product_query(self, product_name: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'product_dashboard'):
                product_data = self.analytics.product_dashboard(product_name)
            else:
                return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        except Exception as e:
            logger.error(f"Product error: {e}")
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        response = self.formatter.product_response(product_data)
        
        return {"success": product_data.get("success", False), "response": response, "ai_used": False}
    
    def _handle_pending_query(self, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'pending_metrics'):
                pending_data = self.analytics.pending_metrics()
            else:
                pending_data = {}
        except Exception as e:
            logger.error(f"Pending error: {e}")
            pending_data = {}
        
        response = self.formatter.pending_response(pending_data)
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_risk_query(self, user_phone: str = None) -> Dict[str, Any]:
        risk_dealers = []
        action_plan = []
        
        try:
            if hasattr(self.analytics, 'top_risk_dealers'):
                risk_dealers = self.analytics.top_risk_dealers(5)
            if hasattr(self.analytics, 'generate_action_plan'):
                action_plan = self.analytics.generate_action_plan()
        except Exception as e:
            logger.error(f"Risk error: {e}")
        
        risk_data = {"risk_dealers": risk_dealers, "action_plan": action_plan}
        response = self.formatter.risk_response(risk_data)
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
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
    return AIQueryService(db)


def process_whatsapp_query(question: str, db: Session, user_phone: str = None) -> str:
    service = AIQueryService(db)
    result = service.process_query(question, user_phone)
    return result.get("response", "Unable to process your request. Please try again.")
