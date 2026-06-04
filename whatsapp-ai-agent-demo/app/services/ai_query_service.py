# ==========================================================
# FILE: app/services/ai_query_service.py
# ==========================================================
# COMPLETE AI QUERY SERVICE - WORLD CLASS PRODUCTION READY
# ENHANCED: Intent Priority, AI Classification, Executive Intelligence,
# Dealer Intelligence, City Intelligence, Warehouse Intelligence,
# Natural Language Search, Conversational Memory, Proactive AI

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
# CONVERSATIONAL MEMORY (Enhanced)
# ======================================================

class ConversationMemory:
    """Store conversation context per user with enhanced memory"""
    
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
                "last_dashboard": None,
                "last_analysis": None,
                "conversation_history": [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        return self.memories[user_phone]
    
    def update(self, user_phone: str, intent: str = None, entity: Any = None,
               city: str = None, dealer: str = None, dn: str = None,
               question: str = None, response: str = None,
               dashboard: Dict = None, analysis: Dict = None):
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
        if dashboard:
            memory["last_dashboard"] = dashboard
        if analysis:
            memory["last_analysis"] = analysis
        
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
# PHASE 1: AI INTENT CLASSIFICATION
# ======================================================

class AIIntentClassifier:
    """Use AI to classify complex intents that regex can't handle"""
    
    @classmethod
    def classify_with_ai(cls, question: str, ai_provider=None, user_phone=None) -> Tuple[str, Optional[str]]:
        """Use DeepSeek to classify intent when regex is uncertain"""
        if not ai_provider:
            return None, None
        
        try:
            prompt = f"""Classify this logistics question into one of these intents:
- DN: asking about a specific delivery note number
- RANKING: asking for top/best/worst/highest/lowest performers
- COMPARISON: comparing two entities
- RISK: asking about problems, issues, delays, bottlenecks
- EXECUTIVE: asking for summary, dashboard, what to focus on
- CITY: asking about a specific city's performance
- WAREHOUSE: asking about a warehouse
- PRODUCT: asking about a product
- POD: asking about proof of delivery
- DEALER: asking about a specific dealer
- GENERAL: non-logistics questions

Question: "{question}"

Respond with JSON: {{"intent": "INTENT_NAME", "entity": "extracted_entity_or_null"}}"""

            response = ai_provider.answer_question(
                prompt,
                {"type": "intent_classification"},
                structured=True,
                user_phone=user_phone
            )
            
            if response.get("success") and response.get("structured"):
                result = response["structured"]
                intent = result.get("intent", "").upper()
                entity = result.get("entity")
                if intent in ["DN", "RANKING", "COMPARISON", "RISK", "EXECUTIVE", 
                              "CITY", "WAREHOUSE", "PRODUCT", "POD", "DEALER", "GENERAL"]:
                    return intent, entity
        except Exception as e:
            logger.error(f"AI Intent classification error: {e}")
        
        return None, None


# ======================================================
# PHASE 1: REBUILT INTENT PRIORITY ENGINE
# ======================================================

class IntentClassifier:
    """Strict intent hierarchy with corrected priority order"""
    
    # PHASE 1: Corrected priority order
    # RANKING now has higher priority than DEALER
    INTENT_PRIORITY = {
        "DN": 1,
        "RANKING": 2,      # Moved up - fixes "Which dealer has highest DN quantity"
        "COMPARISON": 3,
        "RISK": 4,
        "EXECUTIVE": 5,
        "CITY": 6,
        "WAREHOUSE": 7,
        "PRODUCT": 8,
        "POD": 9,
        "DEALER": 10,      # Moved down - dealer is now lower priority
        "FORECAST": 11,
        "PENDING": 12,
        "SERVICE_DISCOVERY": 13,
        "GENERAL": 14,
        "UNKNOWN": 15
    }
    
    # DN patterns (highest priority)
    DN_PATTERNS = [
        r'\b(\d{8,15})\b',
        r'dn[\s:]*(\d{8,15})',
        r'delivery[-\s]?note[\s:]*(\d{8,15})',
        r'track[\s:]*(\d{8,15})'
    ]
    
    # Ranking patterns - NOW HIGHER PRIORITY
    RANKING_PATTERNS = [
        r'(?:top|best|highest|largest|maximum)\s+(\d+)?\s*(?:dealer|warehouse|city|product)',
        r'(?:worst|lowest|smallest|minimum)\s+(\d+)?\s*(?:dealer|warehouse|city|product)',
        r'which\s+(?:dealer|warehouse|city|product)\s+(?:has|is)\s+(?:the\s+)?(?:highest|lowest|best|worst)',
        r'(?:ranking|leaderboard|top\s+performers?)'
    ]
    
    # Comparison patterns
    COMPARISON_PATTERNS = [
        r'compare\s+([A-Za-z0-9\s]+)\s+(?:and|vs|versus)\s+([A-Za-z0-9\s]+)',
        r'(?:difference|versus|vs)\s+between\s+([A-Za-z0-9\s]+)\s+and\s+([A-Za-z0-9\s]+)'
    ]
    
    # Risk patterns
    RISK_PATTERNS = [
        r'(?:risk|critical|urgent|problem|issue|delay|bottleneck)',
        r'which\s+(?:dealer|warehouse|cities?)\s+(?:is|are)\s+(?:causing|cause)\s+(?:issues|problems|delays)'
    ]
    
    # Executive patterns
    EXECUTIVE_PATTERNS = [
        r'(?:ceo|executive|command center|what should i focus|overview|kpi|performance report)',
        r'(?:today\'s|daily)\s+(?:logistics|status|briefing)',
        r'what\s+(?:is|are)\s+(?:the|our)\s+(?:biggest|top)\s+(?:issue|problem|risk)'
    ]
    
    # City patterns with Pakistan cities
    CITY_PATTERNS = [
        r'(?:in|for|at)\s+([A-Za-z\s]+?)(?:\s+only|\s+$|\.|\?|$)',
        r'(karachi|lahore|islamabad|multan|faisalabad|hyderabad|peshawar|quetta|rawalpindi|gujranwala|sialkot)',
        r'which\s+city\s+(?:has|is)\s+(?:the\s+)?(?:highest|lowest|worst|best)'
    ]
    
    # Warehouse patterns
    WAREHOUSE_PATTERNS = [
        r'(?:warehouse|godown)[\s:]+([A-Za-z0-9]+)',
        r'which\s+warehouse\s+(?:has|is)\s+(?:the\s+)?(?:highest|lowest|best|worst)'
    ]
    
    # Product patterns
    PRODUCT_PATTERNS = [
        r'(?:product|material|model|sku)[\s:]+([A-Za-z0-9\-]+)',
        r'which\s+product\s+(?:has|is)\s+(?:the\s+)?(?:highest|lowest|best|worst)'
    ]
    
    # POD patterns
    POD_PATTERNS = [
        r'(?:pod|acknowledgement|proof of delivery|awaiting acknowledgement)',
        r'pending\s+pod',
        r'pod\s+(?:aging|backlog)'
    ]
    
    # Dealer patterns (lower priority now)
    DEALER_PATTERNS = [
        r'(?:dealer|customer)[\s:]+([A-Za-z0-9\s&]+)',
        r'(?:show|get|find)[\s]+(?:dealer|customer)[\s]+([A-Za-z0-9\s&]+)',
        r'(?:dashboard|performance|summary)[\s]+(?:for|of)[\s]+([A-Za-z0-9\s&]+)'
    ]
    
    # Service discovery keywords
    SERVICE_KEYWORDS = [
        "what can you do", "help", "menu", "services", "capabilities",
        "what do you offer", "how can you help", "available services",
        "what services", "features", "what can i ask"
    ]
    
    @classmethod
    def classify(cls, question: str, memory: Dict = None, ai_provider=None, user_phone=None) -> Tuple[str, Optional[str]]:
        """
        Classify question using corrected priority hierarchy.
        Falls back to AI for complex queries.
        """
        question_lower = question.lower().strip()
        question_original = question.strip()
        
        # PHASE 1: Try AI classification first for complex queries
        ai_intent, ai_entity = AIIntentClassifier.classify_with_ai(question, ai_provider, user_phone)
        if ai_intent:
            logger.info(f"AI classified as: {ai_intent} | Entity: {ai_entity}")
            return ai_intent, ai_entity
        
        # PRIORITY 1: DN Query
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, question_original, re.IGNORECASE)
            if match:
                return "DN", match.group(1)
        
        # PRIORITY 2: Ranking Query (MOVED UP - fixes the main issue)
        for pattern in cls.RANKING_PATTERNS:
            if re.search(pattern, question_lower, re.IGNORECASE):
                # Extract entity type if present
                entity_match = re.search(r'(dealer|warehouse|city|product)', question_lower)
                entity = entity_match.group(1) if entity_match else None
                return "RANKING", entity
        
        # Check for ranking without patterns
        ranking_indicators = ["top", "best", "highest", "lowest", "worst", "ranking", "leaderboard"]
        if any(ind in question_lower for ind in ranking_indicators):
            entity_match = re.search(r'(dealer|warehouse|city|product)', question_lower)
            entity = entity_match.group(1) if entity_match else None
            return "RANKING", entity
        
        # PRIORITY 3: Comparison Query
        for pattern in cls.COMPARISON_PATTERNS:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                groups = match.groups()
                if len(groups) >= 2:
                    return "COMPARISON", (groups[0].strip(), groups[1].strip())
        
        # PRIORITY 4: Risk Query
        for pattern in cls.RISK_PATTERNS:
            if re.search(pattern, question_lower, re.IGNORECASE):
                return "RISK", None
        
        # PRIORITY 5: Executive Query
        for pattern in cls.EXECUTIVE_PATTERNS:
            if re.search(pattern, question_lower, re.IGNORECASE):
                return "EXECUTIVE", None
        
        # PRIORITY 6: City Query
        for pattern in cls.CITY_PATTERNS:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                city = match.group(1).strip().title() if match.groups() else None
                if city and len(city) > 1 and len(city) < 30:
                    return "CITY", city
        
        # PRIORITY 7: Warehouse Query
        for pattern in cls.WAREHOUSE_PATTERNS:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                return "WAREHOUSE", match.group(1).upper() if match.groups() else None
        
        # PRIORITY 8: Product Query
        for pattern in cls.PRODUCT_PATTERNS:
            match = re.search(pattern, question_lower, re.IGNORECASE)
            if match:
                return "PRODUCT", match.group(1) if match.groups() else None
        
        # PRIORITY 9: POD Query
        for pattern in cls.POD_PATTERNS:
            if re.search(pattern, question_lower, re.IGNORECASE):
                return "POD", None
        
        # PRIORITY 10: Dealer Query (MOVED DOWN)
        if "dealer" in question_lower or "customer" in question_lower:
            for pattern in cls.DEALER_PATTERNS:
                match = re.search(pattern, question_lower, re.IGNORECASE)
                if match:
                    return "DEALER", match.group(1).strip().title() if match.groups() else None
            # Extract dealer name from simple queries
            words = question_lower.split()
            for i, word in enumerate(words):
                if word in ["dealer", "customer"] and i + 1 < len(words):
                    return "DEALER", words[i + 1].title()
        
        # PRIORITY 11: Forecast Query
        forecast_keywords = ["forecast", "predict", "trend", "projection", "future", "upcoming"]
        if any(kw in question_lower for kw in forecast_keywords):
            return "FORECAST", None
        
        # PRIORITY 12: Pending Query
        pending_keywords = ["pending", "backlog", "waiting", "not delivered"]
        if any(kw in question_lower for kw in pending_keywords):
            return "PENDING", None
        
        # PRIORITY 13: Service Discovery
        if any(kw in question_lower for kw in cls.SERVICE_KEYWORDS):
            return "SERVICE_DISCOVERY", None
        
        # PHASE 1: Check for follow-up questions using memory
        if memory and memory.get("last_intent"):
            follow_up_keywords = ["why", "how", "what about", "tell me more", "explain", "and", "also"]
            if any(kw in question_lower for kw in follow_up_keywords):
                return memory["last_intent"], memory.get("last_entity")
        
        # PRIORITY 14: General AI (non-logistics)
        general_keywords = ["who is", "what is", "why", "how to", "tell me a", "write", "create", 
                           "joke", "story", "poem", "python", "code", "weather", "news"]
        if any(kw in question_lower for kw in general_keywords):
            return "GENERAL", None
        
        # PRIORITY 15: Unknown - will use AI
        return "UNKNOWN", None


# ======================================================
# PHASE 2: EXECUTIVE INTELLIGENCE ENGINE
# ======================================================

class ExecutiveIntelligenceEngine:
    """Generate executive-level insights and recommendations"""
    
    @staticmethod
    def generate_daily_briefing(analytics_service, ai_provider=None, user_phone=None) -> Dict:
        """Generate daily operations briefing"""
        briefing = {
            "top_delays": [],
            "top_dealers": [],
            "top_warehouses": [],
            "top_risks": [],
            "actions_required": [],
            "ai_summary": ""
        }
        
        try:
            # Get top delays
            if hasattr(analytics_service, 'aging_summary'):
                aging = analytics_service.aging_summary()
                if aging.get("critical_count", 0) > 0:
                    briefing["top_delays"].append(f"{aging.get('critical_count', 0)} DNs > 15 days old")
            
            # Get top risk dealers
            if hasattr(analytics_service, 'top_risk_dealers'):
                risk_dealers = analytics_service.top_risk_dealers(3)
                for dealer in risk_dealers:
                    briefing["top_dealers"].append(dealer.get('dealer', 'Unknown'))
            
            # Get top risk warehouses
            if hasattr(analytics_service, 'top_risk_warehouses'):
                risk_warehouses = analytics_service.top_risk_warehouses(3)
                for wh in risk_warehouses:
                    briefing["top_warehouses"].append(wh.get('warehouse', 'Unknown'))
            
            # Generate AI summary
            if ai_provider:
                context = {"briefing": briefing}
                response = ai_provider.answer_question(
                    "Provide a concise daily logistics briefing with top priorities",
                    context,
                    structured=False,
                    user_phone=user_phone
                )
                if response.get("success"):
                    briefing["ai_summary"] = response.get("content", "")[:500]
        
        except Exception as e:
            logger.error(f"Daily briefing error: {e}")
        
        return briefing
    
    @staticmethod
    def generate_root_cause_analysis(question: str, analytics_service, ai_provider=None, user_phone=None) -> str:
        """Analyze root cause of logistics issues"""
        analysis = ""
        
        try:
            context = {
                "question": question,
                "pending_metrics": {},
                "pod_metrics": {},
                "aging_summary": {}
            }
            
            if hasattr(analytics_service, 'pending_metrics'):
                context["pending_metrics"] = analytics_service.pending_metrics()
            if hasattr(analytics_service, 'pod_metrics'):
                context["pod_metrics"] = analytics_service.pod_metrics()
            if hasattr(analytics_service, 'aging_summary'):
                context["aging_summary"] = analytics_service.aging_summary()
            
            if ai_provider:
                response = ai_provider.answer_question(
                    f"Analyze root cause: {question}. Consider dealers, warehouses, cities, and products.",
                    context,
                    structured=False,
                    user_phone=user_phone
                )
                if response.get("success"):
                    analysis = response.get("content", "")
        
        except Exception as e:
            logger.error(f"Root cause analysis error: {e}")
            analysis = "Unable to perform root cause analysis at this time."
        
        return analysis


# ======================================================
# PHASE 3: DEALER INTELLIGENCE ENGINE
# ======================================================

class DealerIntelligenceEngine:
    """Generate dealer risk scores, health scores, and action plans"""
    
    @staticmethod
    def calculate_risk_score(dealer_data: Dict) -> int:
        """Calculate dealer risk score (0-100, higher = more risk)"""
        score = 0
        
        # Pending DNs (40% weight)
        pending_dns = dealer_data.get("pending_dns", 0)
        total_dns = dealer_data.get("total_dns", 1)
        pending_ratio = pending_dns / total_dns if total_dns > 0 else 0
        score += pending_ratio * 40
        
        # Pending PODs (30% weight)
        pod_pending = dealer_data.get("pod_pending_dns", 0)
        pod_ratio = pod_pending / total_dns if total_dns > 0 else 0
        score += pod_ratio * 30
        
        # Aging (20% weight)
        avg_age = dealer_data.get("avg_dispatch_days", 0)
        age_score = min(avg_age / 30, 1) * 20
        score += age_score
        
        # Pending value (10% weight)
        pending_value = dealer_data.get("pending_value", 0)
        value_score = min(pending_value / 10000000, 1) * 10  # Scale to 10M
        score += value_score
        
        return min(int(score), 100)
    
    @staticmethod
    def calculate_health_score(risk_score: int) -> int:
        """Calculate dealer health score (inverse of risk score)"""
        return 100 - risk_score
    
    @staticmethod
    def generate_action_plan(dealer_data: Dict, risk_score: int) -> List[Dict]:
        """Generate dealer-specific action plan"""
        actions = []
        
        pending_dns = dealer_data.get("pending_dns", 0)
        pod_pending = dealer_data.get("pod_pending_dns", 0)
        avg_age = dealer_data.get("avg_dispatch_days", 0)
        
        if pending_dns > 10:
            actions.append({
                "priority": "HIGH",
                "action": f"Review {pending_dns} pending deliveries",
                "reason": "High volume of undelivered items"
            })
        
        if pod_pending > 5:
            actions.append({
                "priority": "HIGH",
                "action": f"Follow up on {pod_pending} pending POD acknowledgements",
                "reason": "Delayed revenue recognition"
            })
        
        if avg_age > 15:
            actions.append({
                "priority": "MEDIUM",
                "action": "Escalate aging deliveries to warehouse team",
                "reason": f"Average dispatch age {avg_age} days exceeds threshold"
            })
        
        if risk_score > 70:
            actions.append({
                "priority": "CRITICAL",
                "action": "Schedule urgent dealer review meeting",
                "reason": f"Risk score {risk_score} indicates critical situation"
            })
        
        if not actions:
            actions.append({
                "priority": "LOW",
                "action": "Continue monitoring",
                "reason": "Dealer performing within acceptable parameters"
            })
        
        return actions


# ======================================================
# PHASE 4: CITY INTELLIGENCE ENGINE
# ======================================================

class CityIntelligenceEngine:
    """Generate city rankings, forecasts, and insights"""
    
    @staticmethod
    def generate_city_forecast(city_data: Dict, ai_provider=None, user_phone=None) -> str:
        """Generate city performance forecast"""
        forecast = ""
        
        try:
            if ai_provider:
                context = {
                    "city": city_data.get("city"),
                    "current_pending": city_data.get("pending_dns", 0),
                    "current_delay_rate": city_data.get("delay_rate", 0),
                    "performance_score": city_data.get("performance_score", 0)
                }
                response = ai_provider.answer_question(
                    f"Forecast expected POD backlog and delivery performance for next week",
                    context,
                    structured=False,
                    user_phone=user_phone
                )
                if response.get("success"):
                    forecast = response.get("content", "")[:300]
        except Exception as e:
            logger.error(f"City forecast error: {e}")
            forecast = "Forecast not available at this time."
        
        return forecast


# ======================================================
# PHASE 5: WAREHOUSE INTELLIGENCE ENGINE
# ======================================================

class WarehouseIntelligenceEngine:
    """Generate warehouse scorecards, rankings, and recommendations"""
    
    @staticmethod
    def calculate_efficiency_score(warehouse_data: Dict) -> int:
        """Calculate warehouse efficiency score (0-100)"""
        score = 70  # Base score
        
        # Pending ratio reduces score
        pending_dns = warehouse_data.get("pending_dns", 0)
        total_dns = warehouse_data.get("total_dns", 1)
        pending_ratio = pending_dns / total_dns if total_dns > 0 else 0
        score -= pending_ratio * 30
        
        # POD pending reduces score
        pod_pending = warehouse_data.get("pod_pending_dns", 0)
        pod_ratio = pod_pending / total_dns if total_dns > 0 else 0
        score -= pod_ratio * 20
        
        return max(min(int(score), 100), 0)
    
    @staticmethod
    def generate_recommendations(warehouse_data: Dict, efficiency_score: int) -> List[str]:
        """Generate warehouse improvement recommendations"""
        recommendations = []
        
        if efficiency_score < 50:
            recommendations.append("Critical: Immediate process review required")
            recommendations.append("Escalate pending deliveries to management")
        
        if warehouse_data.get("pending_dns", 0) > 20:
            recommendations.append("Increase warehouse staffing to clear backlog")
        
        if warehouse_data.get("pod_pending_dns", 0) > 10:
            recommendations.append("Implement POD tracking system")
        
        if not recommendations:
            recommendations.append("Maintain current performance levels")
        
        return recommendations


# ======================================================
# PHASE 6: NATURAL LANGUAGE SEARCH
# ======================================================

class NaturalLanguageSearch:
    """Semantic search for natural language queries"""
    
    @staticmethod
    def extract_intent_naturally(question: str) -> Dict:
        """Extract intent using natural language understanding"""
        question_lower = question.lower()
        
        # Risk-focused questions
        risk_phrases = ["hurting us", "causing problems", "biggest issue", "main problem"]
        if any(phrase in question_lower for phrase in risk_phrases):
            return {"intent": "RISK", "confidence": "high"}
        
        # Performance questions
        performance_phrases = ["performing well", "good job", "best performing"]
        if any(phrase in question_lower for phrase in performance_phrases):
            return {"intent": "RANKING", "confidence": "high"}
        
        # Delivery questions
        delivery_phrases = ["delivery problem", "shipping issue", "where is my"]
        if any(phrase in question_lower for phrase in delivery_phrases):
            return {"intent": "PENDING", "confidence": "medium"}
        
        return {"intent": None, "confidence": "low"}
    
    @staticmethod
    def fuzzy_match_dealer(query: str, dealers: List[str]) -> Optional[str]:
        """Fuzzy match dealer name"""
        query_lower = query.lower()
        
        for dealer in dealers:
            dealer_lower = dealer.lower()
            # Check for substring match
            if query_lower in dealer_lower or dealer_lower in query_lower:
                return dealer
            # Check for word match
            query_words = set(query_lower.split())
            dealer_words = set(dealer_lower.split())
            if len(query_words.intersection(dealer_words)) >= 1:
                return dealer
        
        return None


# ======================================================
# PHASE 8: PROACTIVE AI
# ======================================================

class ProactiveAI:
    """Generate suggested questions and next best actions"""
    
    SUGGESTED_QUESTIONS = {
        "DEALER": [
            "Top risk dealers",
            "Pending PODs for this dealer",
            "Compare with top performer"
        ],
        "CITY": [
            "Top risk dealers in this city",
            "Warehouse performance in this city",
            "Pending deliveries in this city"
        ],
        "EXECUTIVE": [
            "Top 5 risks overall",
            "Warehouse efficiency report",
            "Dealer ranking by value"
        ],
        "POD": [
            "Top dealers with pending POD",
            "Aging POD report",
            "Warehouse POD performance"
        ],
        "DEFAULT": [
            "Executive summary",
            "Top risk dealers",
            "Pending POD status",
            "Warehouse efficiency",
            "City performance"
        ]
    }
    
    @classmethod
    def get_suggestions(cls, intent: str) -> List[str]:
        """Get suggested questions based on intent"""
        return cls.SUGGESTED_QUESTIONS.get(intent, cls.SUGGESTED_QUESTIONS["DEFAULT"])
    
    @classmethod
    def get_next_best_action(cls, dashboard: Dict, intent: str) -> str:
        """Get next best action recommendation"""
        if intent == "DEALER":
            pending_dns = dashboard.get("pending_dns", 0)
            pod_pending = dashboard.get("pod_pending_dns", 0)
            
            if pending_dns > 10:
                return f"📌 Next: Review {pending_dns} pending deliveries"
            elif pod_pending > 5:
                return f"📌 Next: Follow up on {pod_pending} pending PODs"
            else:
                return "📌 Next: Check city-wide performance"
        
        elif intent == "POD":
            return "📌 Next: View top dealers with pending POD"
        
        elif intent == "EXECUTIVE":
            return "📌 Next: Review top 5 risks"
        
        return "📌 Next: Type 'help' to see available services"


# ======================================================
# SERVICE DISCOVERY (Enhanced)
# ======================================================

class ServiceDiscovery:
    """Service catalog and help system"""
    
    SERVICE_CATALOG = {
        "1. Dealer Analytics": {
            "description": "View dealer performance, risk scores, and AI insights",
            "examples": ["Show Afzal dashboard", "Dealer Electro City performance"]
        },
        "2. Delivery Tracking": {
            "description": "Track specific delivery notes (DN)",
            "examples": ["DN 6243611264", "Track delivery 6243611264"]
        },
        "3. POD Monitoring": {
            "description": "Monitor Proof of Delivery status",
            "examples": ["Pending POD", "POD aging report"]
        },
        "4. Warehouse Analytics": {
            "description": "Warehouse efficiency scores and recommendations",
            "examples": ["Warehouse HPK status", "Best warehouse"]
        },
        "5. City Intelligence": {
            "description": "City performance, rankings, and forecasts",
            "examples": ["Karachi situation", "Best city", "Lahore forecast"]
        },
        "6. Executive Dashboard": {
            "description": "Daily briefing, risks, and strategic insights",
            "examples": ["Executive summary", "Today's logistics status"]
        },
        "7. Risk Analysis": {
            "description": "Identify critical dealers and bottlenecks",
            "examples": ["Top risks", "Which dealer is causing issues?"]
        },
        "8. Rankings & Comparisons": {
            "description": "Compare dealers, warehouses, or cities",
            "examples": ["Top 10 dealers", "Compare A and B"]
        },
        "9. Root Cause Analysis": {
            "description": "Understand why issues are happening",
            "examples": ["Why are PODs increasing?", "What's causing delays?"]
        },
        "10. General AI Assistant": {
            "description": "Ask any general question",
            "examples": ["Who is Imran Khan?", "Tell me a joke"]
        }
    }
    
    @classmethod
    def get_full_catalog(cls) -> str:
        response = "📋 *SERVICE CATALOG*\n\n"
        for service, details in cls.SERVICE_CATALOG.items():
            response += f"*{service}*\n"
            response += f"   📝 {details['description']}\n"
            response += f"   💡 Try: {details['examples'][0]}\n\n"
        response += "💬 Type `help` anytime for this menu."
        return response
    
    @classmethod
    def get_quick_help(cls) -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT*

*Try these examples:*
• `DN 6243611264` - Track delivery
• `Karachi situation` - City analysis
• `Top 10 dealers` - Rankings
• `What should I focus on today?` - Executive advice
• `Why are PODs increasing?` - Root cause
• `Who is Imran Khan?` - General AI

Type `services` for complete catalog!
"""


# ======================================================
# RESPONSE FORMATTER (Enhanced)
# ======================================================

class ResponseFormatter:
    """Format responses with AI insights and proactive suggestions"""
    
    @staticmethod
    def dealer_response(dealer_name: str, dashboard: Dict, ai_insights: Dict = None) -> str:
        if dashboard.get("fuzzy"):
            return dashboard.get("summary", "Multiple dealers found")
        
        if not dashboard.get("success"):
            return f"❌ Dealer '{dealer_name}' not found."
        
        response = dashboard.get("formatted_message", "")
        
        # PHASE 3: Add risk score and health score
        risk_score = DealerIntelligenceEngine.calculate_risk_score(dashboard)
        health_score = DealerIntelligenceEngine.calculate_health_score(risk_score)
        
        response += f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
        response += f"📊 *DEALER SCORECARD*\n"
        response += f"━━━━━━━━━━━━━━━━━━━━\n"
        response += f"⚠️ Risk Score: {risk_score}/100\n"
        response += f"✅ Health Score: {health_score}/100\n"
        
        # Add risk indicator
        if risk_score >= 70:
            response += f"🔴 CRITICAL RISK - Immediate attention required\n"
        elif risk_score >= 50:
            response += f"🟡 MEDIUM RISK - Monitor closely\n"
        else:
            response += f"🟢 LOW RISK - Performing well\n"
        
        # PHASE 3: Add action plan
        action_plan = DealerIntelligenceEngine.generate_action_plan(dashboard, risk_score)
        if action_plan:
            response += f"\n🎯 *ACTION PLAN*\n"
            for action in action_plan[:3]:
                priority_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(action["priority"], "⚪")
                response += f"{priority_icon} {action['action']}\n"
        
        # Add AI insights
        if ai_insights and ai_insights.get("success"):
            response += f"\n🤖 *AI INSIGHTS*\n"
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:200]}\n"
        
        # PHASE 8: Add next best action
        next_action = ProactiveAI.get_next_best_action(dashboard, "DEALER")
        response += f"\n{next_action}"
        
        return response
    
    @staticmethod
    def city_response(city_name: str, city_data: Dict, ai_insights: Dict = None, 
                      top_dealers: List = None, forecast: str = None) -> str:
        response = f"🌆 *CITY INTELLIGENCE: {city_name.upper()}*\n\n"
        response += f"📊 Total DNs: {city_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending: {city_data.get('pending_dns', 0)} DNs\n"
        response += f"💰 Pending Value: Rs {city_data.get('pending_value', 0):,.2f}\n"
        response += f"⚠️ Delay Rate: {city_data.get('delay_rate', 0)}%\n"
        response += f"📋 Performance Score: {city_data.get('performance_score', 0)}%\n"
        
        # PHASE 4: Add performance indicator
        perf_score = city_data.get('performance_score', 0)
        if perf_score >= 80:
            response += f"🌟 TOP PERFORMING CITY\n"
        elif perf_score <= 50:
            response += f"⚠️ CRITICAL - Requires intervention\n"
        
        # PHASE 4: Add top and worst dealers
        if top_dealers:
            response += f"\n━━━━━━━━━━━━━━━━━━━━\n"
            response += f"🏪 *TOP DEALERS IN CITY*\n"
            response += f"━━━━━━━━━━━━━━━━━━━━\n"
            for i, dealer in enumerate(top_dealers[:5], 1):
                response += f"{i}. {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
        
        # PHASE 4: Add forecast
        if forecast:
            response += f"\n📈 *FORECAST*\n{forecast}\n"
        
        # Add AI insights
        if ai_insights and ai_insights.get("success"):
            response += f"\n🤖 *AI ANALYSIS*\n"
            if ai_insights.get("summary"):
                response += f"{ai_insights['summary'][:300]}\n"
        
        # PHASE 8: Add suggested questions
        suggestions = ProactiveAI.get_suggestions("CITY")
        response += f"\n💡 *You may also ask:*\n"
        for suggestion in suggestions[:3]:
            response += f"   • {suggestion}\n"
        
        return response
    
    @staticmethod
    def executive_response(executive_data: Dict, ai_insights: Dict = None, daily_briefing: Dict = None) -> str:
        response = executive_data.get("formatted_message", "")
        
        # PHASE 2: Add daily briefing
        if daily_briefing:
            response += f"\n\n━━━━━━━━━━━━━━━━━━━━\n"
            response += f"📅 *DAILY OPERATIONS BRIEFING*\n"
            response += f"━━━━━━━━━━━━━━━━━━━━\n"
            
            if daily_briefing.get("top_delays"):
                response += f"⏰ *Top Delays:*\n"
                for delay in daily_briefing["top_delays"][:3]:
                    response += f"   • {delay}\n"
            
            if daily_briefing.get("top_dealers"):
                response += f"\n⚠️ *Dealers Requiring Attention:*\n"
                for dealer in daily_briefing["top_dealers"][:3]:
                    response += f"   • {dealer}\n"
            
            if daily_briefing.get("ai_summary"):
                response += f"\n🤖 *AI SUMMARY*\n{daily_briefing['ai_summary']}\n"
        
        # Add AI insights
        if ai_insights and ai_insights.get("success"):
            response += f"\n━━━━━━━━━━━━━━━━━━━━\n"
            response += f"🎯 *STRATEGIC INSIGHTS*\n"
            response += f"━━━━━━━━━━━━━━━━━━━━\n"
            
            if ai_insights.get("top_risks"):
                response += f"🚨 *Top 5 Risks:*\n"
                for i, risk in enumerate(ai_insights["top_risks"][:5], 1):
                    response += f"   {i}. {risk}\n"
            
            if ai_insights.get("recommendations"):
                response += f"\n💡 *Recommendations:*\n"
                for rec in ai_insights["recommendations"][:3]:
                    response += f"   • {rec}\n"
        
        # PHASE 8: Add suggested questions
        suggestions = ProactiveAI.get_suggestions("EXECUTIVE")
        response += f"\n💡 *Try asking:*\n"
        for suggestion in suggestions[:3]:
            response += f"   • {suggestion}\n"
        
        return response
    
    @staticmethod
    def ranking_response(rankings: Dict, category: str, limit: int = 10) -> str:
        if category not in rankings:
            return f"No ranking data available"
        
        data = rankings[category][:limit]
        
        if not data:
            return f"No data found"
        
        category_name = category.replace("_", " ").upper()
        response = f"📊 *{category_name} RANKINGS*\n\n"
        
        for i, item in enumerate(data, 1):
            if "dealer" in item:
                response += f"{i}. *{item.get('dealer', 'Unknown')}*\n"
                response += f"   📦 {item.get('total_dns', 0)} DNs | 💰 Rs {item.get('total_value', 0):,.2f}\n"
                if item.get('pending_dns', 0) > 0:
                    response += f"   ⚠️ {item.get('pending_dns', 0)} pending\n"
                response += "\n"
            elif "warehouse" in item:
                response += f"{i}. *{item.get('warehouse', 'Unknown')}*\n"
                response += f"   ⚡ Efficiency: {item.get('efficiency_score', 0)}%\n"
                response += f"   ⏳ Pending: {item.get('pending_dns', 0)} DNs\n\n"
            elif "city" in item:
                response += f"{i}. *{item.get('city', 'Unknown')}*\n"
                response += f"   📊 Score: {item.get('performance_score', 0)}%\n"
                response += f"   ⏳ Pending: {item.get('pending_dns', 0)} DNs\n\n"
        
        return response
    
    @staticmethod
    def root_cause_response(analysis: str) -> str:
        return f"🔍 *ROOT CAUSE ANALYSIS*\n\n{analysis}"
    
    @staticmethod
    def service_discovery_response() -> str:
        return ServiceDiscovery.get_full_catalog()
    
    @staticmethod
    def unknown_response() -> str:
        return ServiceDiscovery.get_quick_help()
    
    @staticmethod
    def help_response() -> str:
        return ServiceDiscovery.get_quick_help()
    
    @staticmethod
    def dn_response(dn_details: Dict) -> str:
        # Keep existing DN response logic
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
    def warehouse_response(warehouse_data: Dict) -> str:
        efficiency = WarehouseIntelligenceEngine.calculate_efficiency_score(warehouse_data)
        recommendations = WarehouseIntelligenceEngine.generate_recommendations(warehouse_data, efficiency)
        
        response = f"🏭 *WAREHOUSE: {warehouse_data.get('warehouse', 'Unknown')}*\n\n"
        response += f"📊 Total DNs: {warehouse_data.get('total_dns', 0)}\n"
        response += f"⏳ Pending DNs: {warehouse_data.get('pending_dns', 0)}\n"
        response += f"⚡ Efficiency Score: {efficiency}/100\n"
        
        if efficiency >= 80:
            response += f"🌟 HIGH PERFORMING\n"
        elif efficiency <= 50:
            response += f"⚠️ CRITICAL - Needs intervention\n"
        
        if recommendations:
            response += f"\n💡 *Recommendations:*\n"
            for rec in recommendations[:3]:
                response += f"   • {rec}\n"
        
        return response
    
    @staticmethod
    def product_response(product_data: Dict) -> str:
        product = product_data.get("product", {})
        response = f"📦 *PRODUCT: {product.get('product_name', 'Unknown')}*\n\n"
        response += f"📊 Total Qty: {product.get('total_qty', 0):,.0f} units\n"
        response += f"✅ Fulfillment Rate: {product.get('fulfillment_rate', 0)}%\n"
        response += f"⏳ Pending Qty: {product.get('pending_qty', 0):,.0f} units\n"
        response += f"⚡ Velocity: {product.get('velocity', 'Normal')}\n"
        return response
    
    @staticmethod
    def pod_response(pod_data: Dict) -> str:
        response = f"📋 *POD STATUS*\n\n"
        response += f"📊 Pending PODs: {pod_data.get('pod_pending_dns', 0)} DNs\n"
        response += f"📦 Pending Units: {pod_data.get('pod_pending_units', 0):,.0f}\n"
        
        if pod_data.get("urgent_count", 0) > 0:
            response += f"\n⚠️ URGENT: {pod_data.get('urgent_count', 0)} DNs > 15 days old\n"
            response += f"🚨 Escalation Required!\n"
        
        return response
    
    @staticmethod
    def pending_response(pending_data: Dict) -> str:
        response = f"⏳ *PENDING DELIVERIES*\n\n"
        response += f"📊 Total Pending: {pending_data.get('pending_dns', 0)} DNs\n"
        response += f"📦 Pending Units: {pending_data.get('pending_units', 0):,.0f}\n"
        response += f"💰 Pending Value: Rs {pending_data.get('pending_value', 0):,.2f}\n"
        return response
    
    @staticmethod
    def risk_response(risk_data: Dict) -> str:
        response = "🚨 *RISK ASSESSMENT*\n\n"
        
        if risk_data.get("risk_dealers"):
            response += "⚠️ *Top Risk Dealers:*\n"
            for dealer in risk_data.get("risk_dealers", [])[:5]:
                response += f"   • {dealer.get('dealer', 'Unknown')}: {dealer.get('pending_dns', 0)} pending\n"
        
        if risk_data.get("action_plan"):
            response += f"\n🎯 *Action Plan:*\n"
            for action in risk_data.get("action_plan", [])[:3]:
                response += f"   • {action.get('action', '')}\n"
        
        return response if len(response) > 30 else "No significant risks detected."
    
    @staticmethod
    def comparison_response(comparison: Dict, entity_type: str) -> str:
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


# ======================================================
# MAIN AI QUERY SERVICE
# ======================================================

class AIQueryService:
    """
    Complete AI Query Service with all Phase 1-10 enhancements.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        self.memory = ConversationMemory()
        
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        self.ai_available = AI_PROVIDER_AVAILABLE and self.ai_enabled
    
    def process_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """Main entry point for processing user questions."""
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📝 [REQ] Question: {question} | User: {user_phone}")
        
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
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
        
        # PHASE 1: Classify intent with AI fallback
        intent, entity = IntentClassifier.classify(
            question, user_memory, 
            ai_provider_service if self.ai_available else None, 
            user_phone
        )
        
        logger.info(f"🏷️ [INTENT] {intent} | Entity: {entity}")
        
        # Route to handlers
        try:
            if intent == "DN":
                result = self._handle_dn_query(entity or question, user_phone)
            elif intent == "RANKING":
                result = self._handle_ranking_query(question, user_phone)
            elif intent == "COMPARISON":
                if isinstance(entity, tuple):
                    result = self._handle_comparison_query(entity[0], entity[1], user_phone)
                else:
                    result = self._handle_comparison_query(None, None, user_phone)
            elif intent == "RISK":
                result = self._handle_risk_query(user_phone)
            elif intent == "EXECUTIVE":
                result = self._handle_executive_query(user_phone)
            elif intent == "CITY":
                result = self._handle_city_query(entity or question, user_phone)
            elif intent == "WAREHOUSE":
                result = self._handle_warehouse_query(entity or question, user_phone)
            elif intent == "PRODUCT":
                result = self._handle_product_query(entity or question, user_phone)
            elif intent == "POD":
                result = self._handle_pod_query(user_phone)
            elif intent == "DEALER":
                result = self._handle_dealer_query(entity or question, user_phone)
            elif intent == "PENDING":
                result = self._handle_pending_query(user_phone)
            elif intent == "FORECAST":
                result = self._handle_forecast_query(user_phone)
            elif intent == "GENERAL":
                result = self._handle_general_query(question, user_phone)
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
        
        logger.info(f"✅ [RESPONSE] Intent: {intent} | AI: {result.get('ai_used', False)} | Time: {result['processing_time_ms']}ms")
        
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
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        if not dashboard.get("success"):
            return {"success": False, "response": self.formatter.unknown_response(), "ai_used": False}
        
        if dashboard.get("fuzzy"):
            return {"success": True, "response": dashboard.get("summary", "Multiple dealers found"), "ai_used": False}
        
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
        
        # Generate forecast
        forecast = CityIntelligenceEngine.generate_city_forecast(city_data, ai_provider_service if self.ai_available else None, user_phone)
        
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = AIRecommendationEngine.generate_city_insights(
                    city_data, ai_provider_service, user_phone
                )
            except Exception as e:
                logger.error(f"AI city insights error: {e}")
        
        response = self.formatter.city_response(city_name, city_data, ai_insights, top_dealers, forecast)
        
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_executive_query(self, user_phone: str = None) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
            else:
                executive_data = {"formatted_message": "Executive summary not available"}
        except Exception as e:
            logger.error(f"Executive error: {e}")
            executive_data = {"formatted_message": "Unable to fetch executive summary"}
        
        # PHASE 2: Generate daily briefing
        daily_briefing = ExecutiveIntelligenceEngine.generate_daily_briefing(
            self.analytics, ai_provider_service if self.ai_available else None, user_phone
        )
        
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
        
        response = self.formatter.executive_response(executive_data, ai_insights, daily_briefing)
        
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_ranking_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        question_lower = question.lower()
        
        try:
            if "dealer" in question_lower:
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
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_unknown_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """PHASE 1: Smart unknown question handler - try AI first"""
        
        # PHASE 1: Try AI for unknown questions
        if self.ai_available and ai_provider_service:
            try:
                context = {
                    "type": "unknown_query",
                    "question": question,
                    "available_services": list(ServiceDiscovery.SERVICE_CATALOG.keys())
                }
                
                ai_response = ai_provider_service.answer_question(
                    f"Answer this question if it's a general knowledge question, or suggest a logistics service if it's related to deliveries: {question}",
                    context,
                    structured=False,
                    user_phone=user_phone
                )
                
                if ai_response.get("success"):
                    return {
                        "success": True,
                        "response": ai_response.get("content", "I couldn't understand. Type 'help' to see what I can do."),
                        "ai_used": True
                    }
            except Exception as e:
                logger.error(f"Unknown query AI error: {e}")
        
        # Fallback to help menu
        return {
            "success": True,
            "response": self.formatter.unknown_response(),
            "ai_used": False
        }
    
    def _handle_general_query(self, question: str, user_phone: str = None) -> Dict[str, Any]:
        """PHASE 9: General AI mode for non-logistics questions"""
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
        
        return {"success": True, "response": self.formatter.help_response(), "ai_used": False}
    
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
        
        # PHASE 8: Auto escalation detection
        if pod_data.get("urgent_count", 0) > 0:
            response += "\n\n🚨 *ESCALATION REQUIRED* - PODs older than 15 days need management attention!"
        
        return {"success": True, "response": response, "ai_used": False}
    
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
    
    def _handle_comparison_query(self, entity1: str, entity2: str, user_phone: str = None) -> Dict[str, Any]:
        if not entity1 or not entity2:
            return {"success": False, "response": "Please specify two entities to compare.", "ai_used": False}
        
        try:
            if hasattr(self.analytics, 'compare_dealers'):
                comparison = self.analytics.compare_dealers(entity1, entity2)
                response = self.formatter.comparison_response(comparison, "dealer")
            else:
                response = self.formatter.unknown_response()
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            response = self.formatter.unknown_response()
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_forecast_query(self, user_phone: str = None) -> Dict[str, Any]:
        response = """📈 *FORECASTING*

Predictive analytics coming soon:
• POD backlog predictions
• Delivery delay forecasts
• Warehouse capacity planning
• Dealer risk trending

Enable AI forecasting for real-time predictions."""
        
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
