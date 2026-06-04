# ==========================================================
# FILE: app/services/ai_query_service.py (FINAL IMPROVED VERSION)
# ==========================================================

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
import json
import time
from difflib import get_close_matches

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
# USER ROLES
# ======================================================

class UserRole:
    CEO = "ceo"
    MANAGER = "manager"
    BRANCH = "branch"
    VENDOR = "vendor"
    GUEST = "guest"


# ======================================================
# FIX 1: IMPROVED SINGLETON PATTERN with Session Handling
# ======================================================

class AIQueryServiceSingleton:
    """Singleton wrapper with proper session handling"""
    _instance = None
    _db_session = None
    
    @classmethod
    def get_instance(cls, db: Session = None):
        if cls._instance is None:
            if db is None:
                raise Exception("First call must provide db")
            cls._db_session = db
            cls._instance = AIQueryService(db)
            logger.info("✅ AIQueryService singleton created")
        return cls._instance
    
    @classmethod
    def update_db_session(cls, db: Session):
        """Update database session for existing instance"""
        if cls._instance:
            cls._instance.db = db
            cls._instance.analytics = AnalyticsService(db)
            cls._instance.logistics = LogisticsQueryService()
            logger.info("✅ AIQueryService database session updated")
    
    @classmethod
    def reset(cls):
        cls._instance = None
        cls._db_session = None
        logger.info("🔄 AIQueryService singleton reset")


# ======================================================
# CONVERSATIONAL MEMORY
# ======================================================

class ConversationMemory:
    """Store conversation context per user with enhanced history"""
    
    def __init__(self):
        self.memories: Dict[str, Dict] = {}
    
    def get(self, user_phone: str) -> Dict:
        if user_phone not in self.memories:
            self.memories[user_phone] = {
                "last_intent": None,
                "last_entity": None,
                "last_city": None,
                "last_dealer": None,
                "last_dn": None,
                "last_question": None,
                "last_response": None,
                "last_analysis": None,
                "role": UserRole.GUEST,
                "conversation_history": [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        return self.memories[user_phone]
    
    def update(self, user_phone: str, intent: str = None, entity: Any = None,
               city: str = None, dealer: str = None, dn: str = None,
               question: str = None, response: str = None, analysis: Dict = None,
               role: str = None):
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
        if analysis:
            memory["last_analysis"] = analysis
        if role:
            memory["role"] = role
        
        if question and response:
            memory["conversation_history"].append({
                "question": question,
                "response": response[:300],
                "intent": intent,
                "timestamp": datetime.utcnow().isoformat()
            })
            if len(memory["conversation_history"]) > 20:
                memory["conversation_history"].pop(0)
        
        memory["updated_at"] = datetime.utcnow()
    
    def get_context_for_ai(self, user_phone: str) -> Dict:
        """Get formatted context for AI injection"""
        memory = self.get(user_phone)
        return {
            "last_intent": memory.get("last_intent"),
            "last_entity": memory.get("last_entity"),
            "last_city": memory.get("last_city"),
            "last_dealer": memory.get("last_dealer"),
            "last_dn": memory.get("last_dn"),
            "last_question": memory.get("last_question"),
            "conversation_history": memory.get("conversation_history", [])[-5:],
            "user_role": memory.get("role", UserRole.GUEST)
        }
    
    def clear(self, user_phone: str):
        if user_phone in self.memories:
            del self.memories[user_phone]


# ======================================================
# NATURAL LANGUAGE RANKING ENGINE
# ======================================================

class NaturalLanguageRankingEngine:
    """Enhanced ranking detection with natural language patterns"""
    
    RANKING_PATTERNS = {
        "dealer": [
            "best performing dealer", "worst performing dealer",
            "top dealer", "bottom dealer", "leading dealer",
            "highest value dealer", "largest dealer", "biggest dealer",
            "most pending dealer", "highest pending dealer",
            "dealer with highest", "dealer with most",
            "which dealer", "what dealer"
        ],
        "warehouse": [
            "best warehouse", "worst warehouse", "top warehouse",
            "highest efficiency warehouse", "most pending warehouse",
            "warehouse with highest", "which warehouse"
        ],
        "city": [
            "best city", "worst city", "top city",
            "highest performing city", "most pending city",
            "city with highest", "which city"
        ]
    }
    
    RANKING_KEYWORDS = [
        "highest", "lowest", "top", "bottom", "best", "worst",
        "largest", "smallest", "most", "least", "maximum", "minimum",
        "ranking", "leaderboard", "top 10", "top 5", "top 3",
        "highest pending", "most pending", "largest backlog",
        "best performing", "leading", "biggest", "greatest"
    ]
    
    @classmethod
    def detect(cls, question: str) -> Tuple[bool, Optional[str]]:
        question_lower = question.lower()
        
        for keyword in cls.RANKING_KEYWORDS:
            if keyword in question_lower:
                for entity_type, patterns in cls.RANKING_PATTERNS.items():
                    for pattern in patterns:
                        if pattern in question_lower or keyword in question_lower:
                            return True, entity_type
                return True, None
        
        for entity_type, patterns in cls.RANKING_PATTERNS.items():
            for pattern in patterns:
                if pattern in question_lower:
                    return True, entity_type
        
        return False, None


# ======================================================
# ENHANCED INTENT CLASSIFIER
# ======================================================

class IntentClassifier:
    
    KNOWN_CITIES = [
        "karachi", "lahore", "islamabad", "faisalabad", "multan",
        "peshawar", "quetta", "rawalpindi", "gujranwala", "sialkot",
        "hyderabad", "bahawalpur", "sukkur", "larkana"
    ]
    
    GENERAL_AI_KEYWORDS = [
        "who is", "what is", "why is", "how to", "tell me about",
        "explain", "describe", "write", "create", "generate",
        "joke", "story", "poem", "python", "code", "programming",
        "weather", "news", "sports", "politics", "history"
    ]
    
    DN_PATTERNS = [
        r'\b(\d{8,15})\b',
        r'dn[\s:]*(\d{8,15})',
        r'delivery[-\s]?note[\s:]*(\d{8,15})',
        r'track[\s:]*(\d{8,15})',
        r'which\s+dn\s+(?:belong|for|of)\s+([A-Za-z0-9\s&]+)',
        r'(?:show|list|get)\s+dn\s+(?:for|of)\s+([A-Za-z0-9\s&]+)'
    ]
    
    EXECUTIVE_KEYWORDS = [
        "executive", "ceo", "command center", "what should i focus",
        "overview", "kpi", "performance report", "summary",
        "what are the biggest", "top risks", "today's priorities"
    ]
    
    @classmethod
    def classify(cls, question: str, memory: Dict = None, logistics_service=None) -> Tuple[str, Optional[str]]:
        question_lower = question.lower().strip()
        question_original = question.strip()
        
        # Check for ranking using natural language engine
        is_ranking, ranking_entity = NaturalLanguageRankingEngine.detect(question)
        if is_ranking:
            logger.info(f"Ranking detected: entity={ranking_entity}")
            return "RANKING", ranking_entity
        
        # Try full question dealer lookup
        if logistics_service:
            try:
                # Try exact match first
                if hasattr(logistics_service, 'search_dealer'):
                    dealer_match = logistics_service.search_dealer(question_original)
                    if dealer_match:
                        logger.info(f"Dealer found: '{question_original}' -> '{dealer_match}'")
                        return "DEALER", dealer_match
                
                # Try fuzzy match for typos
                if hasattr(logistics_service, 'fuzzy_search_dealer'):
                    dealer_match = logistics_service.fuzzy_search_dealer(question_original)
                    if dealer_match:
                        logger.info(f"Fuzzy dealer match: '{question_original}' -> '{dealer_match}'")
                        return "DEALER", dealer_match
            except Exception as e:
                logger.debug(f"Dealer search error: {e}")
        
        # Check for General AI questions
        for keyword in cls.GENERAL_AI_KEYWORDS:
            if keyword in question_lower:
                return "GENERAL", None
        
        # DN Query
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, question_original, re.IGNORECASE)
            if match:
                return "DN", match.group(1)
        
        # Executive Query
        if any(kw in question_lower for kw in cls.EXECUTIVE_KEYWORDS):
            return "EXECUTIVE", None
        
        # City Query
        for city in cls.KNOWN_CITIES:
            if city in question_lower:
                return "CITY", city.title()
        
        # Warehouse Query
        if "warehouse" in question_lower or "godown" in question_lower:
            warehouse_match = re.search(r'(?:warehouse|godown)[\s:]+([A-Za-z0-9]+)', question_lower)
            if warehouse_match:
                return "WAREHOUSE", warehouse_match.group(1).upper()
            return "WAREHOUSE", None
        
        # Product Query
        if "product" in question_lower or "material" in question_lower:
            product_match = re.search(r'(?:product|material)[\s:]+([A-Za-z0-9\-]+)', question_lower)
            if product_match:
                return "PRODUCT", product_match.group(1)
            return "PRODUCT", None
        
        # Risk Query
        if any(kw in question_lower for kw in ["risk", "critical", "urgent", "problem"]):
            return "RISK", None
        
        # POD Query
        if any(kw in question_lower for kw in ["pod", "acknowledgement", "proof of delivery"]):
            return "POD", None
        
        # Pending Query
        if any(kw in question_lower for kw in ["pending", "backlog", "waiting"]):
            return "PENDING", None
        
        # Dealer Query with explicit indicators
        dealer_indicators = ["dealer", "customer", "show", "dashboard"]
        if any(ind in question_lower for ind in dealer_indicators):
            words = question_lower.split()
            for i, word in enumerate(words):
                if word in dealer_indicators and i + 1 < len(words):
                    return "DEALER", words[i + 1].title()
        
        # Check for follow-up questions
        if memory and memory.get("last_intent"):
            follow_up_keywords = ["why", "how", "what about", "tell me more", "explain", "improve", "cause"]
            if any(kw in question_lower for kw in follow_up_keywords):
                logger.info(f"Follow-up detected: returning {memory.get('last_intent')}")
                return memory["last_intent"], memory.get("last_entity")
        
        # Service Discovery
        if any(kw in question_lower for kw in ["help", "menu", "services", "what can you do"]):
            return "SERVICE_DISCOVERY", None
        
        return "UNKNOWN", None


# ======================================================
# EXECUTIVE COMMAND CENTER
# ======================================================

class ExecutiveCommandCenter:
    """Generate executive-level insights and action plans"""
    
    @staticmethod
    def generate_daily_briefing(analytics_service, ai_provider=None, user_phone=None) -> str:
        briefing = ""
        
        try:
            pending_metrics = analytics_service.pending_metrics() if hasattr(analytics_service, 'pending_metrics') else {}
            pod_metrics = analytics_service.pod_metrics() if hasattr(analytics_service, 'pod_metrics') else {}
            risk_dealers = analytics_service.top_risk_dealers(5) if hasattr(analytics_service, 'top_risk_dealers') else []
            
            briefing = "🎯 *EXECUTIVE COMMAND CENTER*\n\n"
            
            briefing += "🚨 *TOP 5 RISKS*\n"
            if risk_dealers:
                for i, dealer in enumerate(risk_dealers[:5], 1):
                    briefing += f"   {i}. {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
            else:
                briefing += "   No major risks detected\n"
            
            briefing += f"\n📊 *KEY METRICS*\n"
            briefing += f"   • Pending DNs: {pending_metrics.get('pending_dns', 0)}\n"
            briefing += f"   • POD Pending: {pod_metrics.get('pod_pending_dns', 0)}\n"
            briefing += f"   • Pending Value: Rs {pending_metrics.get('pending_value', 0):,.2f}\n"
            
            if ai_provider and hasattr(ai_provider, 'answer_question'):
                try:
                    context = {
                        "pending_dns": pending_metrics.get('pending_dns', 0),
                        "pod_pending": pod_metrics.get('pod_pending_dns', 0),
                        "risk_dealers": [d.get('dealer') for d in risk_dealers[:3]]
                    }
                    response = ai_provider.answer_question(
                        "Based on this data, what are the top 3 actions for today?",
                        context,
                        structured=False,
                        user_phone=user_phone
                    )
                    if response.get("success"):
                        briefing += f"\n💡 *AI RECOMMENDATIONS*\n{response.get('content', '')[:300]}\n"
                except Exception as e:
                    logger.error(f"Executive AI error: {e}")
            
        except Exception as e:
            logger.error(f"Executive briefing error: {e}")
            briefing = "🎯 *EXECUTIVE COMMAND CENTER*\n\nUnable to generate briefing at this time."
        
        return briefing


# ======================================================
# SERVICE DISCOVERY
# ======================================================

class ServiceDiscovery:
    
    SERVICE_CATALOG = """
📋 *SERVICE CATALOG*

*1. Dealer Dashboard* - View dealer performance
   Try: "Dealer Afzal" or "Show Abdullah Electronics"

*2. City Analysis* - City-wise delivery performance
   Try: "Karachi situation" or "Lahore performance"

*3. Warehouse Analytics* - Warehouse efficiency
   Try: "Warehouse HPK" or "Which warehouse has highest pending?"

*4. DN Tracking* - Track specific delivery notes
   Try: "DN 6243611264" or "Track delivery 1234567890"

*5. POD Monitoring* - Proof of Delivery status
   Try: "Pending POD" or "POD aging report"

*6. Rankings* - Best/worst performers
   Try: "Top 10 dealers" or "Which dealer has highest pending?"

*7. Executive Dashboard* - Strategic insights
   Try: "Executive summary" or "What should I focus on today?"

*8. Risk Analysis* - Identify critical issues
   Try: "Top risks" or "Which dealer is causing problems?"

*9. General Questions* - Ask anything
   Try: "Who is Imran Khan?" or "Explain logistics"

━━━━━━━━━━━━━━━━━━━━
Just type your question naturally!
"""
    
    @staticmethod
    def get_full_catalog() -> str:
        return ServiceDiscovery.SERVICE_CATALOG
    
    @staticmethod
    def get_quick_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT*

*Quick Examples:*
• `Dealer Afzal` - Dealer dashboard
• `DN 6243611264` - Track delivery
• `Karachi situation` - City analysis
• `Top 10 dealers` - Rankings
• `Executive summary` - CEO view
• `Who is Imran Khan?` - General AI

Type `services` for complete catalog!
"""


# ======================================================
# FIX 4: AI INSIGHTS HELPER (Fallback for missing methods)
# ======================================================

class AIInsightsHelper:
    """Helper to generate AI insights with fallback for missing methods"""
    
    @staticmethod
    def analyze_entity(entity_data: Dict, entity_type: str, ai_provider, user_phone: str = None) -> Optional[Dict]:
        """Generate AI insights for any entity type with fallback"""
        if not ai_provider:
            return None
        
        try:
            # Try direct method if available
            method_name = f"analyze_{entity_type}"
            if hasattr(ai_provider, method_name):
                return getattr(ai_provider, method_name)(entity_data, structured=True, user_phone=user_phone)
            
            # Fallback to generic answer_question
            prompt = f"Analyze this {entity_type} performance and provide key insights, risks, and recommendations."
            response = ai_provider.answer_question(prompt, entity_data, structured=True, user_phone=user_phone)
            return response if response.get("success") else None
            
        except Exception as e:
            logger.error(f"AI insights error for {entity_type}: {e}")
            return None


# ======================================================
# RESPONSE FORMATTER
# ======================================================

class ResponseFormatter:
    
    @staticmethod
    def dealer_response(dealer_name: str, dashboard: Dict, ai_insights: Dict = None) -> str:
        if dashboard.get("fuzzy"):
            return dashboard.get("summary", "Multiple dealers found")
        if not dashboard.get("success"):
            return f"❌ Dealer '{dealer_name}' not found."
        
        response = dashboard.get("formatted_message", "")
        
        if ai_insights and ai_insights.get("success"):
            response += "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI INSIGHTS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            if ai_insights.get("summary"):
                response += f"📊 {ai_insights['summary'][:300]}\n"
            if ai_insights.get("recommendations"):
                response += "\n💡 *Recommendations:*\n"
                for rec in ai_insights["recommendations"][:3]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def city_response(city_name: str, city_data: Dict, ai_insights: Dict = None) -> str:
        response = f"🌆 *CITY: {city_name.upper()}*\n\n"
        response += f"📊 Total DNs: {city_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {city_data.get('pending_dns', 0)}\n"
        response += f"💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}\n"
        response += f"⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ANALYSIS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            if ai_insights.get("summary"):
                response += f"📊 {ai_insights['summary'][:300]}\n"
            if ai_insights.get("risks"):
                response += "\n⚠️ *Risks:*\n"
                for risk in ai_insights["risks"][:2]:
                    response += f"   • {risk}\n"
            if ai_insights.get("recommendations"):
                response += "\n💡 *Recommendations:*\n"
                for rec in ai_insights["recommendations"][:2]:
                    response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def warehouse_response(warehouse_data: Dict, ai_insights: Dict = None) -> str:
        response = f"🏭 *WAREHOUSE: {warehouse_data.get('warehouse', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {warehouse_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {warehouse_data.get('pending_dns', 0)}\n"
        response += f"⚡ Efficiency Score: {warehouse_data.get('efficiency_score', 0)}%\n"
        
        if ai_insights and ai_insights.get("success"):
            response += "\n━━━━━━━━━━━━━━━━━━━━\n"
            response += "🤖 *AI ANALYSIS*\n"
            response += "━━━━━━━━━━━━━━━━━━━━\n"
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:300]}\n"
        
        return response
    
    @staticmethod
    def ranking_response(rankings: Dict, category: str, limit: int = 10) -> str:
        if category not in rankings:
            return "No ranking data available"
        data = rankings[category][:limit]
        if not data:
            return "No data found"
        
        category_name = "DEALERS" if category == "by_value" else category.upper()
        response = f"📊 *TOP {category_name} RANKINGS*\n\n"
        
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
                response += f"   ⚡ Efficiency: {item.get('efficiency_score', 0)}%\n\n"
        
        return response
    
    @staticmethod
    def executive_response(briefing: str) -> str:
        return briefing
    
    @staticmethod
    def service_discovery_response() -> str:
        return ServiceDiscovery.get_full_catalog()
    
    @staticmethod
    def help_response() -> str:
        return ServiceDiscovery.get_quick_help()
    
    @staticmethod
    def unknown_response() -> str:
        return ServiceDiscovery.get_full_catalog()
    
    @staticmethod
    def dn_response(dn_details: Dict) -> str:
        if not dn_details.get("success"):
            return "❌ DN not found."
        return f"🔹 *DN: {dn_details.get('dn_no')}*\n\n📋 Dealer: {dn_details.get('dealer')}\n📋 Status: {dn_details.get('status')}\n📋 POD: {dn_details.get('pod_status')}"
    
    @staticmethod
    def product_response(product_data: Dict) -> str:
        product = product_data.get("product", {})
        return f"📦 *PRODUCT: {product.get('product_name')}*\n\n📊 Total Qty: {product.get('total_qty', 0):,.0f}\n✅ Fulfillment: {product.get('fulfillment_rate', 0)}%"
    
    @staticmethod
    def pending_response(pending_data: Dict) -> str:
        return f"⏳ *PENDING DELIVERIES*\n\n📊 Total: {pending_data.get('pending_dns', 0)} DNs\n📦 Units: {pending_data.get('pending_units', 0):,.0f}"
    
    @staticmethod
    def risk_response(risk_data: Dict) -> str:
        response = "🚨 *RISK ASSESSMENT*\n\n"
        if risk_data.get("risk_dealers"):
            for dealer in risk_data.get("risk_dealers", [])[:5]:
                response += f"⚠️ {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
        return response if len(response) > 30 else "No significant risks detected."
    
    @staticmethod
    def pod_response(pod_data: Dict) -> str:
        return f"📋 *POD STATUS*\n\n📊 Pending: {pod_data.get('pod_pending_dns', 0)} DNs\n📦 Units: {pod_data.get('pod_pending_units', 0):,.0f}"


# ======================================================
# MAIN AI QUERY SERVICE (SINGLETON)
# ======================================================

class AIQueryService:
    """Main AI Query Service - Singleton instance"""
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        self.memory = ConversationMemory()
        self.ai_insights_helper = AIInsightsHelper()
        
        # AI availability settings
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        deepseek_api_key = getattr(config, 'DEEPSEEK_API_KEY', None)
        
        self.ai_available = self.ai_enabled and bool(deepseek_api_key) and ai_provider_service is not None
        
        logger.info("=" * 50)
        logger.info("🚀 AI QUERY SERVICE INITIALIZED (SINGLETON)")
        logger.info(f"AI_ENABLED={self.ai_enabled}")
        logger.info(f"DEEPSEEK_API_KEY={'SET' if deepseek_api_key else 'NOT SET'}")
        logger.info(f"AI_AVAILABLE={self.ai_available}")
        logger.info("=" * 50)
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        # Get user memory
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
        if user_role:
            self.memory.update(user_phone, role=user_role)
        
        logger.info(f"📝 PROCESSING: {question} | User: {user_phone}")
        
        if question.lower() in ["help", "menu", "services", "what can you do", "capabilities"]:
            result = {
                "success": True,
                "response": self.formatter.service_discovery_response(),
                "question_type": "SERVICE_DISCOVERY",
                "ai_used": False
            }
            self.memory.update(user_phone, intent="SERVICE_DISCOVERY", question=question, response=result["response"])
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
        
        intent, entity = IntentClassifier.classify(question, user_memory, self.logistics)
        
        logger.info(f"🏷️ CLASSIFIED: Intent='{intent}' Entity='{entity}'")
        
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
            else:
                result = self._handle_unknown_query(question, user_phone)
        except Exception as e:
            logger.error(f"❌ Error: {e}")
            result = {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again later.",
                "error": str(e),
                "ai_used": False
            }
        
        self.memory.update(user_phone, intent=intent, entity=entity, question=question, response=result.get("response", ""))
        
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
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            return {"success": False, "response": f"❌ Unable to fetch dealer data for '{dealer_name}'.", "ai_used": False}
        
        if not dashboard.get("success"):
            return {"success": False, "response": f"❌ Dealer '{dealer_name}' not found.", "ai_used": False}
        
        if dashboard.get("fuzzy"):
            return {"success": True, "response": dashboard.get("summary", "Multiple dealers found"), "ai_used": False}
        
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = self.ai_insights_helper.analyze_entity(
                    dashboard, "dealer", ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI dealer insights error: {e}")
        
        response = self.formatter.dealer_response(dealer_name, dashboard, ai_insights)
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_city_query(self, city_name: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings()
            else:
                return {"success": False, "response": "❌ City analytics not available.", "ai_used": False}
        except Exception as e:
            logger.error(f"City error: {e}")
            return {"success": False, "response": "❌ Unable to fetch city data.", "ai_used": False}
        
        city_data = None
        for c in rankings.get("all_cities", []):
            if city_name.lower() in c.get("city", "").lower():
                city_data = c
                break
        
        if not city_data:
            return {"success": False, "response": f"❌ City '{city_name}' not found.", "ai_used": False}
        
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = self.ai_insights_helper.analyze_entity(
                    city_data, "city", ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI city insights error: {e}")
        
        response = self.formatter.city_response(city_name, city_data, ai_insights)
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_executive_query(self, user_phone: str = None) -> Dict[str, Any]:
        briefing = ExecutiveCommandCenter.generate_daily_briefing(
            self.analytics, 
            ai_provider_service if self.ai_available else None, 
            user_phone
        )
        response = self.formatter.executive_response(briefing)
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_ranking_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        question_lower = question.lower()
        
        try:
            if "warehouse" in question_lower:
                if hasattr(self.analytics, 'warehouse_rankings'):
                    rankings = self.analytics.warehouse_rankings(10)
                    response = self.formatter.ranking_response(rankings, "all_warehouses", 10)
                else:
                    response = "❌ Warehouse rankings not available"
            elif "dealer" in question_lower or "customer" in question_lower:
                if hasattr(self.analytics, 'dealer_rankings'):
                    rankings = self.analytics.dealer_rankings(10)
                    response = self.formatter.ranking_response(rankings, "by_value", 10)
                else:
                    response = "❌ Dealer rankings not available"
            else:
                response = "📊 Please specify: dealers, warehouses, or cities"
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            response = "❌ Unable to generate rankings at this time."
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_general_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        logger.info(f"🔍 CALLING DEEPSEEK: {question}")
        
        if self.ai_available and ai_provider_service:
            try:
                context = self.memory.get_context_for_ai(user_phone)
                
                response = ai_provider_service.answer_question(
                    question, 
                    context=context,
                    structured=False, 
                    user_phone=user_phone
                )
                
                if response.get("success"):
                    logger.info(f"✅ DEEPSEEK RESPONSE RECEIVED")
                    return {
                        "success": True,
                        "response": response.get("content", "No response generated."),
                        "ai_used": True
                    }
                else:
                    logger.warning(f"⚠️ DEEPSEEK RESPONSE FAILED")
            except Exception as e:
                logger.error(f"❌ DEEPSEEK CALL ERROR: {e}")
        else:
            logger.warning(f"⚠️ DeepSeek not available. ai_available={self.ai_available}")
        
        return {
            "success": False,
            "response": "⚠️ AI service is temporarily unavailable. Please try again later.",
            "ai_used": False
        }
    
    def _handle_unknown_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        logger.info(f"🔍 CALLING DEEPSEEK (unknown query): {question}")
        
        if self.ai_available and ai_provider_service:
            try:
                context = self.memory.get_context_for_ai(user_phone)
                response = ai_provider_service.answer_question(question, context=context, structured=False, user_phone=user_phone)
                
                if response.get("success"):
                    logger.info(f"✅ DEEPSEEK RESPONSE RECEIVED")
                    return {
                        "success": True,
                        "response": response.get("content", self.formatter.unknown_response()),
                        "ai_used": True
                    }
            except Exception as e:
                logger.error(f"Unknown query AI error: {e}")
        
        return {"success": True, "response": self.formatter.unknown_response(), "ai_used": False}
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'warehouse_rankings'):
                rankings = self.analytics.warehouse_rankings()
            else:
                return {"success": False, "response": "❌ Warehouse analytics not available.", "ai_used": False}
        except Exception as e:
            logger.error(f"Warehouse error: {e}")
            return {"success": False, "response": "❌ Unable to fetch warehouse data.", "ai_used": False}
        
        warehouse_data = None
        for w in rankings.get("all_warehouses", []):
            if warehouse_name.upper() in w.get("warehouse", "").upper():
                warehouse_data = w
                break
        
        if not warehouse_data:
            return {"success": False, "response": f"❌ Warehouse '{warehouse_name}' not found.", "ai_used": False}
        
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = self.ai_insights_helper.analyze_entity(
                    warehouse_data, "warehouse", ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI warehouse insights error: {e}")
        
        response = self.formatter.warehouse_response(warehouse_data, ai_insights)
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_dn_query(self, dn_no: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
        except Exception as e:
            logger.error(f"DN error: {e}")
            return {"success": False, "response": f"❌ Unable to fetch DN {dn_no}.", "ai_used": False}
        
        response = self.formatter.dn_response(dn_details)
        return {"success": dn_details.get("success", False), "response": response, "ai_used": False}
    
    def _handle_product_query(self, product_name: str, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'product_dashboard'):
                product_data = self.analytics.product_dashboard(product_name)
            else:
                return {"success": False, "response": "❌ Product analytics not available.", "ai_used": False}
        except Exception as e:
            logger.error(f"Product error: {e}")
            return {"success": False, "response": "❌ Unable to fetch product data.", "ai_used": False}
        
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
    
    def _handle_pod_query(self, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'pod_metrics'):
                pod_data = self.analytics.pod_metrics()
            else:
                pod_data = {}
        except Exception as e:
            logger.error(f"POD error: {e}")
            pod_data = {}
        
        response = self.formatter.pod_response(pod_data)
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_risk_query(self, user_phone: str = None) -> Dict[str, Any]:
        risk_dealers = []
        try:
            if hasattr(self.analytics, 'top_risk_dealers'):
                risk_dealers = self.analytics.top_risk_dealers(5)
        except Exception as e:
            logger.error(f"Risk error: {e}")
        
        risk_data = {"risk_dealers": risk_dealers}
        response = self.formatter.risk_response(risk_data)
        return {"success": True, "response": response, "ai_used": False}
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None, conversation_id: int = None):
        try:
            log_entry = AIResponseLog(
                conversation_id=conversation_id,
                prompt=question[:500],
                ai_response=result.get("response", "")[:2000],
                model_name="deepseek" if result.get("ai_used") else "rule_based",
                success=result.get("success", False),
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log query: {e}")
            self.db.rollback()


# ======================================================
# FACTORY FUNCTIONS
# ======================================================

def get_ai_query_service(db: Session = None) -> AIQueryService:
    """Get singleton instance of AIQueryService"""
    if db:
        return AIQueryServiceSingleton.get_instance(db)
    return AIQueryServiceSingleton.get_instance()


def update_ai_query_service_db(db: Session):
    """Update database session for existing instance"""
    AIQueryServiceSingleton.update_db_session(db)


def reset_ai_query_service():
    AIQueryServiceSingleton.reset()


def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    service = get_ai_query_service(db)
    result = service.process_query(question, user_phone, user_role)
    return result.get("response", "Unable to process your request. Please try again.")
