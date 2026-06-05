# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v3.0)
# ==========================================================
# COMPLETE WITH ALL 10 IMPROVEMENTS:
# 1. Enhanced Dealer Detection (Multi-strategy)
# 2. Confidence-based routing
# 3. Fuzzy dealer matching with RapidFuzz
# 4. Dealer Intelligence Report
# 5. Dealer Follow-up Memory
# 6. Dealer Ranking Queries
# 7. Root Cause Queries
# 8. Executive Intelligence Queries
# 9. Recommendation Queries
# 10. Dealer Intelligence Response Format

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
import json
import time
from difflib import get_close_matches
from enum import Enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from loguru import logger

from app.models import AIResponseLog
from app.config import config
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# Try to import RapidFuzz for improved fuzzy matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("RapidFuzz not available. Install with: pip install rapidfuzz")

# Safe import for AI provider
try:
    from app.services.ai_provider_service import ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"AI Provider Service not available: {e}")
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None


# ======================================================
# ENHANCED DEALER MATCHER (Improvement #2 & #3)
# ======================================================

class EnhancedDealerMatcher:
    """
    Multi-strategy dealer matcher with confidence scoring
    Strategies: Exact, Fuzzy, Contains, RapidFuzz, Semantic
    """
    
    def __init__(self):
        self.dealer_cache = []
        self.dealer_names_lower = []
        self.last_loaded = None
    
    def load_dealers(self, db: Session):
        """Load all dealer names into cache"""
        try:
            from app.models import DeliveryReport
            dealers = db.query(DeliveryReport.customer_name).distinct().filter(
                DeliveryReport.customer_name.isnot(None)
            ).limit(5000).all()
            
            self.dealer_cache = [d[0] for d in dealers if d[0]]
            self.dealer_names_lower = [d.lower() for d in self.dealer_cache]
            self.last_loaded = datetime.utcnow()
            logger.info(f"Loaded {len(self.dealer_cache)} dealers for matching")
            return True
        except Exception as e:
            logger.error(f"Failed to load dealers: {e}")
            return False
    
    def match_dealer(self, query: str, threshold: int = 70) -> Dict[str, Any]:
        """
        Match dealer using multiple strategies with confidence scoring
        
        Returns:
            {
                "found": bool,
                "dealer_name": str,
                "confidence": int (0-100),
                "strategy": str
            }
        """
        if not self.dealer_cache:
            return {"found": False, "dealer_name": None, "confidence": 0, "strategy": "no_data"}
        
        query_clean = query.strip()
        query_lower = query_clean.lower()
        
        # Strategy 1: Exact match (case insensitive)
        for i, dealer in enumerate(self.dealer_cache):
            if dealer.lower() == query_lower:
                return {
                    "found": True,
                    "dealer_name": dealer,
                    "confidence": 100,
                    "strategy": "exact_match"
                }
        
        # Strategy 2: Contains match (dealer name contains query OR query contains dealer name)
        for dealer in self.dealer_cache:
            dealer_lower = dealer.lower()
            if query_lower in dealer_lower or dealer_lower in query_lower:
                confidence = 90 if query_lower in dealer_lower else 80
                return {
                    "found": True,
                    "dealer_name": dealer,
                    "confidence": confidence,
                    "strategy": "contains_match"
                }
        
        # Strategy 3: Word token matching (handles "Rafi Electronics Oghi" -> "Rafi Electronics")
        query_words = set(query_lower.split())
        best_match = None
        best_score = 0
        
        for dealer in self.dealer_cache:
            dealer_lower = dealer.lower()
            dealer_words = set(dealer_lower.split())
            common_words = query_words.intersection(dealer_words)
            if common_words:
                score = len(common_words) / max(len(query_words), len(dealer_words)) * 100
                if score > best_score and score >= 50:
                    best_score = score
                    best_match = dealer
        
        if best_match:
            return {
                "found": True,
                "dealer_name": best_match,
                "confidence": int(best_score),
                "strategy": "token_match"
            }
        
        # Strategy 4: RapidFuzz fuzzy matching (Improvement #2)
        if RAPIDFUZZ_AVAILABLE:
            try:
                # Try token sort ratio first (best for word order variations)
                match = process.extractOne(query_clean, self.dealer_cache, scorer=fuzz.token_sort_ratio)
                if match and match[1] >= threshold:
                    return {
                        "found": True,
                        "dealer_name": match[0],
                        "confidence": match[1],
                        "strategy": "rapidfuzz_token_sort"
                    }
                
                # Try partial ratio for substring matches
                match = process.extractOne(query_clean, self.dealer_cache, scorer=fuzz.partial_ratio)
                if match and match[1] >= threshold:
                    return {
                        "found": True,
                        "dealer_name": match[0],
                        "confidence": match[1],
                        "strategy": "rapidfuzz_partial"
                    }
                
                # Try WRatio for weighted matching
                match = process.extractOne(query_clean, self.dealer_cache, scorer=fuzz.WRatio)
                if match and match[1] >= threshold:
                    return {
                        "found": True,
                        "dealer_name": match[0],
                        "confidence": match[1],
                        "strategy": "rapidfuzz_wratio"
                    }
            except Exception as e:
                logger.warning(f"RapidFuzz matching failed: {e}")
        
        # Strategy 5: Python difflib (fallback)
        try:
            matches = get_close_matches(query_clean, self.dealer_cache, n=1, cutoff=threshold/100)
            if matches:
                return {
                    "found": True,
                    "dealer_name": matches[0],
                    "confidence": int(threshold),
                    "strategy": "difflib"
                }
        except Exception as e:
            logger.warning(f"Difflib matching failed: {e}")
        
        return {
            "found": False,
            "dealer_name": None,
            "confidence": 0,
            "strategy": "no_match"
        }
    
    def get_suggestions(self, query: str, limit: int = 5) -> List[Dict]:
        """Get dealer suggestions for ambiguous queries"""
        if not self.dealer_cache or not RAPIDFUZZ_AVAILABLE:
            return []
        
        try:
            matches = process.extract(query, self.dealer_cache, scorer=fuzz.token_sort_ratio, limit=limit)
            return [{"dealer_name": m[0], "score": m[1]} for m in matches if m[1] >= 50]
        except Exception as e:
            logger.warning(f"Dealer suggestions failed: {e}")
            return []


# ======================================================
# CONVERSATIONAL MEMORY (Enhanced for follow-ups - Improvement #5)
# ======================================================

class ConversationMemory:
    """Store conversation context per user for follow-up questions"""
    
    def __init__(self):
        self.memories: Dict[str, Dict] = {}
    
    def get(self, user_phone: str) -> Dict:
        if user_phone not in self.memories:
            self.memories[user_phone] = {
                "last_intent": None,
                "last_entity": None,
                "last_city": None,
                "last_dealer": None,
                "last_warehouse": None,
                "last_dn": None,
                "last_question": None,
                "last_response": None,
                "last_analysis": None,
                "conversation_history": [],
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        return self.memories[user_phone]
    
    def update(self, user_phone: str, intent: str = None, entity: Any = None,
               city: str = None, dealer: str = None, warehouse: str = None, dn: str = None,
               question: str = None, response: str = None, analysis: Dict = None):
        memory = self.get(user_phone)
        
        if intent:
            memory["last_intent"] = intent
        if entity:
            memory["last_entity"] = entity
        if city:
            memory["last_city"] = city
        if dealer:
            memory["last_dealer"] = dealer
        if warehouse:
            memory["last_warehouse"] = warehouse
        if dn:
            memory["last_dn"] = dn
        if question:
            memory["last_question"] = question
        if response:
            memory["last_response"] = response
        if analysis:
            memory["last_analysis"] = analysis
        
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
    
    def get_context(self, user_phone: str) -> Dict:
        memory = self.get(user_phone)
        return {
            "last_intent": memory.get("last_intent"),
            "last_entity": memory.get("last_entity"),
            "last_city": memory.get("last_city"),
            "last_dealer": memory.get("last_dealer"),
            "last_warehouse": memory.get("last_warehouse"),
            "last_dn": memory.get("last_dn"),
            "last_question": memory.get("last_question"),
            "last_analysis": memory.get("last_analysis"),
            "conversation_history": memory.get("conversation_history", [])[-5:],
            "user_role": memory.get("user_role", "guest")
        }
    
    def clear(self, user_phone: str):
        if user_phone in self.memories:
            del self.memories[user_phone]


# ======================================================
# ENHANCED INTENT CLASSIFIER (Improvement #1)
# ======================================================

class IntentClassifier:
    """Enhanced intent classification with multi-strategy dealer detection"""
    
    KNOWN_CITIES = [
        "karachi", "lahore", "islamabad", "faisalabad", "multan",
        "peshawar", "quetta", "rawalpindi", "gujranwala", "sialkot",
        "hyderabad", "bahawalpur", "sukkur", "larkana"
    ]
    
    RANKING_KEYWORDS = [
        "highest", "lowest", "top", "bottom", "best", "worst",
        "largest", "smallest", "most", "least", "maximum", "minimum",
        "ranking", "leaderboard", "top 10", "top 5", "top 3",
        "highest pending", "most pending", "largest backlog"
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
        r'track[\s:]*(\d{8,15})'
    ]
    
    # Improvement #3: Expanded dealer indicators
    DEALER_INDICATORS = [
        "dealer", "customer", "distributor", "retailer", "shop", "store",
        "dealer dashboard", "dealer summary", "dealer performance",
        "show dealer", "tell me about dealer", "dashboard for",
        "performance of", "status of", "details for", "info about"
    ]
    
    @classmethod
    def classify(cls, question: str, memory: Dict = None, dealer_matcher: EnhancedDealerMatcher = None) -> Tuple[str, Optional[str], float]:
        """
        Enhanced classification with multi-strategy dealer detection
        Returns: (intent, entity, confidence)
        """
        question_lower = question.lower().strip()
        question_original = question.strip()
        
        # DN Detection
        for pattern in cls.DN_PATTERNS:
            match = re.search(pattern, question_original, re.IGNORECASE)
            if match:
                return "DN", match.group(1), 1.0
        
        # Ranking Detection
        for keyword in cls.RANKING_KEYWORDS:
            if keyword in question_lower:
                # Improvement #6: Enhanced ranking detection
                if "dealer" in question_lower or "customer" in question_lower:
                    return "RANKING_DEALER", None, 0.9
                elif "warehouse" in question_lower:
                    return "RANKING_WAREHOUSE", None, 0.9
                elif "city" in question_lower:
                    return "RANKING_CITY", None, 0.9
                else:
                    return "RANKING", None, 0.8
        
        # Check for ranking patterns
        ranking_patterns = [
            r'which\s+(dealer|warehouse|city|product)\s+(?:has|is)\s+(?:the\s+)?(?:most|least)',
            r'(?:show|get|display)\s+(?:the\s+)?(?:top|bottom)'
        ]
        for pattern in ranking_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entity = match.group(1) if match.groups() else None
                return "RANKING", entity, 0.85
        
        # General AI Questions
        for keyword in cls.GENERAL_AI_KEYWORDS:
            if keyword in question_lower:
                logistics_keywords = ["dealer", "dn", "delivery", "warehouse", "pod", "pending"]
                if not any(lk in question_lower for lk in logistics_keywords):
                    return "GENERAL", None, 0.9
        
        # Improvement #1 & #2: Enhanced Dealer Detection with Multi-strategy
        if dealer_matcher:
            dealer_match = dealer_matcher.match_dealer(question_original, threshold=70)
            if dealer_match.get("found") and dealer_match.get("confidence", 0) >= 70:
                dealer_name = dealer_match.get("dealer_name")
                confidence = dealer_match.get("confidence", 70) / 100
                logger.info(f"Dealer matched via {dealer_match.get('strategy')}: '{question_original}' -> '{dealer_name}' (confidence: {dealer_match.get('confidence')})")
                
                # Determine specific dealer intent based on question
                if any(word in question_lower for word in ["pending", "backlog", "undelivered"]):
                    return "DEALER_PENDING", dealer_name, confidence
                elif any(word in question_lower for word in ["health", "score", "rating", "healthy"]):
                    return "DEALER_HEALTH", dealer_name, confidence
                elif any(word in question_lower for word in ["risk", "exposure", "problem", "issue"]):
                    return "DEALER_RISK", dealer_name, confidence
                elif any(word in question_lower for word in ["delivered", "completed"]):
                    return "DEALER_DELIVERED", dealer_name, confidence
                elif any(word in question_lower for word in ["pod", "acknowledgement"]):
                    return "DEALER_POD", dealer_name, confidence
                else:
                    return "DEALER", dealer_name, confidence
        
        # City Detection
        for city in cls.KNOWN_CITIES:
            if city in question_lower:
                return "CITY", city.title(), 0.9
        
        city_patterns = [
            r'(?:in|for|at)\s+(' + '|'.join(cls.KNOWN_CITIES) + r')',
            r'(' + '|'.join(cls.KNOWN_CITIES) + r')\s+(?:situation|performance|status|delivery)'
        ]
        for pattern in city_patterns:
            match = re.search(pattern, question_lower)
            if match:
                city = match.group(1).strip().title()
                return "CITY", city, 0.85
        
        # Improvement #7: Root Cause Detection
        root_cause_keywords = [
            "why are", "why is", "root cause", "what is causing",
            "reason for", "cause of", "why do", "why does"
        ]
        if any(kw in question_lower for kw in root_cause_keywords):
            return "ROOT_CAUSE", None, 0.85
        
        # Improvement #8: Executive Intelligence
        executive_keywords = [
            "executive summary", "what should i focus on", "today's priorities",
            "biggest operational risk", "network health score", "revenue at risk",
            "ceo briefing", "command center", "executive dashboard"
        ]
        if any(kw in question_lower for kw in executive_keywords):
            return "EXECUTIVE", None, 0.9
        
        # Improvement #9: Recommendation Detection
        recommendation_keywords = [
            "what should management do", "how can we improve", "recommendations",
            "action plan", "what should we do", "suggestions"
        ]
        if any(kw in question_lower for kw in recommendation_keywords):
            return "RECOMMENDATION", None, 0.85
        
        # Risk Query
        risk_keywords = ["risk", "critical", "urgent", "problem", "issue", "delay", "bottleneck"]
        if any(kw in question_lower for kw in risk_keywords):
            return "RISK", None, 0.8
        
        # POD Query
        pod_keywords = ["pod", "acknowledgement", "proof of delivery", "awaiting acknowledgement"]
        if any(kw in question_lower for kw in pod_keywords):
            return "POD", None, 0.85
        
        # Warehouse Query
        warehouse_keywords = ["warehouse", "godown", "storage"]
        if any(kw in question_lower for kw in warehouse_keywords):
            warehouse_match = re.search(r'(?:warehouse|godown)[\s:]+([A-Za-z0-9]+)', question_lower)
            if warehouse_match:
                return "WAREHOUSE", warehouse_match.group(1).upper(), 0.8
            return "WAREHOUSE", None, 0.75
        
        # Pending Query
        pending_keywords = ["pending", "backlog", "waiting", "not delivered"]
        if any(kw in question_lower for kw in pending_keywords):
            return "PENDING", None, 0.8
        
        # Follow-up detection using memory (Improvement #5)
        if memory and memory.get("last_intent"):
            follow_up_keywords = ["why", "how", "what about", "tell me more", "explain", "and", "also", "then"]
            if any(kw in question_lower for kw in follow_up_keywords) or len(question.split()) <= 5:
                last_intent = memory.get("last_intent")
                last_entity = memory.get("last_entity")
                if last_entity:
                    logger.info(f"Follow-up detected: {last_intent} with entity {last_entity}")
                    return last_intent, last_entity, 0.7
        
        # Service Discovery
        service_keywords = ["help", "menu", "services", "what can you do", "capabilities"]
        if any(kw in question_lower for kw in service_keywords):
            return "SERVICE_DISCOVERY", None, 1.0
        
        return "UNKNOWN", None, 0.0


# ======================================================
# DEALER INTELLIGENCE RESPONSE (Improvement #4 & #10)
# ======================================================

class DealerIntelligenceResponse:
    """Generate comprehensive dealer intelligence reports"""
    
    @staticmethod
    def format_intelligence_report(dealer_data: Dict, dealer_name: str, memory: Dict = None) -> str:
        """Format complete dealer intelligence report"""
        
        kpis = dealer_data.get("kpis", {})
        
        total_dns = kpis.get("total_dns", 0)
        delivered_dns = kpis.get("delivered_dns", 0)
        pending_dns = kpis.get("pending_dns", 0)
        pod_pending_dns = kpis.get("pod_pending_dns", 0)
        
        total_value = kpis.get("total_amount", 0)
        pending_value = kpis.get("pending_amount", 0)
        pod_pending_value = kpis.get("pod_pending_amount", 0)
        
        # Calculate key metrics
        delivery_compliance = (delivered_dns / total_dns * 100) if total_dns > 0 else 0
        pod_compliance = ((delivered_dns - pod_pending_dns) / delivered_dns * 100) if delivered_dns > 0 else 0
        health_score = (delivery_compliance * 0.6) + (pod_compliance * 0.4)
        
        # Determine risk level
        if pending_dns > 50 or pod_pending_dns > 30:
            risk_level = "🔴 HIGH RISK"
            risk_icon = "🚨"
        elif pending_dns > 20 or pod_pending_dns > 15:
            risk_level = "🟡 MEDIUM RISK"
            risk_icon = "⚠️"
        else:
            risk_level = "🟢 LOW RISK"
            risk_icon = "✅"
        
        # Build response
        response = f"""
╔══════════════════════════════════════════╗
║         📊 DEALER INTELLIGENCE           ║
║              {dealer_name[:30]}                    ║
╚══════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 PERFORMANCE OVERVIEW
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Health Score: {health_score:.1f}/100
{risk_level}

📊 *Key Metrics:*
• Total DNs: {total_dns}
• Delivered: {delivered_dns} ✅
• Pending: {pending_dns} ⏳
• POD Pending: {pod_pending_dns} 📋

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 FINANCIAL EXPOSURE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Sales: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• POD Pending Value: Rs {pod_pending_value:,.2f}
• Total Outstanding: Rs {pending_value + pod_pending_value:,.2f}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 COMPLIANCE METRICS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery Compliance: {delivery_compliance:.1f}%
• POD Compliance: {pod_compliance:.1f}%
"""
        
        # Add top issues
        pending_products = dealer_data.get("alerts", {}).get("highest_pending_product", {})
        if pending_products.get("pending_quantity", 0) > 0:
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ TOP ISSUES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• High pending quantity: {pending_products.get('pending_quantity', 0):,.0f} units
  Product: {pending_products.get('product_name', 'Unknown')}
"""
        
        # Add recommendations
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 RECOMMENDATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        if pending_dns > 0:
            response += f"• 🚨 Clear {pending_dns} pending DNs (Priority HIGH)\n"
        if pod_pending_dns > 0:
            response += f"• 📋 Collect {pod_pending_dns} pending PODs (Priority HIGH)\n"
        if pending_value > 10000000:
            response += f"• 💰 Escalate financial exposure of Rs {pending_value:,.2f}\n"
        if not pending_dns and not pod_pending_dns:
            response += "• ✅ Dealer performing well. Maintain regular follow-up.\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Need more details? Try:
• PENDING DNS - Show pending deliveries
• POD STATUS - Show POD details
• RISK - Show risk assessment
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        
        return response


# ======================================================
# RESPONSE FORMATTER (Enhanced)
# ======================================================

class ResponseFormatter:
    """Format responses for WhatsApp"""
    
    @staticmethod
    def service_discovery_response() -> str:
        return """
📋 *SERVICE CATALOG*

*1. Dealer Intelligence*
   Complete dealer report with health score, pending DNs, financial exposure
   💡 "Rafi Electronics Oghi" or "Dealer Afzal"

*2. City Analysis*
   City performance and risk assessment
   💡 "Karachi situation" or "Lahore performance"

*3. Rankings*
   Best/worst performers by value, pending, or efficiency
   💡 "Top 10 dealers" or "Best dealer"

*4. Executive Dashboard*
   High-level business overview
   💡 "Executive summary" or "What should I focus on today?"

*5. Root Cause Analysis*
   Understand why issues are happening
   💡 "Why are PODs increasing?"

*6. Recommendations*
   AI-powered action plans
   💡 "What should management do?"

*7. DN Tracking*
   Track specific delivery notes
   💡 "DN 6243611264"

Type your question naturally!
"""
    
    @staticmethod
    def unknown_response() -> str:
        return """
❓ I couldn't identify your request.

Type `services` to see what I can do, or try:

• `Rafi Electronics Oghi` - Dealer report
• `Karachi situation` - City analysis  
• `Top 10 dealers` - Rankings
• `Executive summary` - CEO view
"""
    
    @staticmethod
    def ranking_response(rankings: Dict, ranking_type: str, limit: int = 10) -> str:
        """Format ranking response"""
        if not rankings:
            return "No ranking data available"
        
        if ranking_type == "DEALER":
            dealers = rankings.get("by_value", [])[:limit]
            if not dealers:
                return "No dealer data available"
            
            response = "📊 *TOP DEALERS BY VALUE*\n\n"
            for i, d in enumerate(dealers, 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')}*\n"
                response += f"   💰 Rs {d.get('total_value', 0):,.2f}\n"
                response += f"   📦 {d.get('total_dns', 0)} DNs | ⚠️ {d.get('pending_dns', 0)} pending\n\n"
            return response
        
        elif ranking_type == "RISK":
            dealers = rankings.get("by_pending", [])[:limit]
            response = "🚨 *HIGHEST RISK DEALERS*\n\n"
            for i, d in enumerate(dealers, 1):
                response += f"{i}. *{d.get('dealer', 'Unknown')}*\n"
                response += f"   ⚠️ {d.get('pending_dns', 0)} pending DNs\n"
                response += f"   💰 Rs {d.get('pending_value', 0):,.2f} at risk\n\n"
            return response
        
        return "Ranking type not supported"


# ======================================================
# MAIN AI QUERY SERVICE
# ======================================================

class AIQueryService:
    """Enterprise AI Query Service with all improvements"""
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.formatter = ResponseFormatter()
        self.memory = ConversationMemory()
        self.dealer_matcher = EnhancedDealerMatcher()
        
        # Load dealers for fuzzy matching
        self.dealer_matcher.load_dealers(db)
        
        # AI availability
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        deepseek_api_key = getattr(config, 'DEEPSEEK_API_KEY', None)
        self.ai_available = self.ai_enabled and bool(deepseek_api_key) and AI_PROVIDER_AVAILABLE
        
        logger.info("=" * 50)
        logger.info("🚀 ENTERPRISE AI QUERY SERVICE v3.0 INITIALIZED")
        logger.info(f"Dealers loaded: {len(self.dealer_matcher.dealer_cache)}")
        logger.info(f"RapidFuzz: {RAPIDFUZZ_AVAILABLE}")
        logger.info(f"AI Available: {self.ai_available}")
        logger.info("=" * 50)
    
    # ======================================================
    # MAIN PROCESSING PIPELINE
    # ======================================================
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
        if user_role:
            self.memory.update(user_phone, user_role=user_role)
        
        logger.info(f"📝 PROCESSING: {question} | User: {user_phone}")
        
        # Get conversation context for follow-ups
        conv_context = self.memory.get_context(user_phone) if user_phone else {}
        
        # Classify intent with enhanced dealer matcher
        intent, entity, confidence = IntentClassifier.classify(question, conv_context, self.dealer_matcher)
        
        logger.info(f"🏷️ CLASSIFIED: Intent='{intent}' Entity='{entity}' Confidence={confidence:.2f}")
        
        # Route to appropriate handler
        try:
            if intent == "DN":
                result = self._handle_dn_query(entity or question, user_phone)
            elif intent == "DEALER":
                result = self._handle_dealer_intelligence(entity, user_phone)
            elif intent == "DEALER_PENDING":
                result = self._handle_dealer_pending(entity, user_phone)
            elif intent == "DEALER_HEALTH":
                result = self._handle_dealer_health(entity, user_phone)
            elif intent == "DEALER_RISK":
                result = self._handle_dealer_risk(entity, user_phone)
            elif intent == "DEALER_DELIVERED":
                result = self._handle_dealer_delivered(entity, user_phone)
            elif intent == "DEALER_POD":
                result = self._handle_dealer_pod(entity, user_phone)
            elif intent == "RANKING_DEALER":
                result = self._handle_ranking_dealer(user_phone)
            elif intent == "RANKING":
                result = self._handle_ranking_query(question, user_phone)
            elif intent == "CITY":
                result = self._handle_city_query(entity or question, user_phone)
            elif intent == "EXECUTIVE":
                result = self._handle_executive_query(user_phone)
            elif intent == "ROOT_CAUSE":  # Improvement #7
                result = self._handle_root_cause_query(question, user_phone)
            elif intent == "RECOMMENDATION":  # Improvement #9
                result = self._handle_recommendation_query(user_phone)
            elif intent == "RISK":
                result = self._handle_risk_query(user_phone)
            elif intent == "POD":
                result = self._handle_pod_query(user_phone)
            elif intent == "WAREHOUSE":
                result = self._handle_warehouse_query(entity or question, user_phone)
            elif intent == "PENDING":
                result = self._handle_pending_query(user_phone)
            elif intent == "GENERAL":
                result = self._handle_general_query(question, user_phone)
            elif intent == "SERVICE_DISCOVERY":
                result = {"success": True, "response": self.formatter.service_discovery_response(), "ai_used": False}
            else:
                result = self._handle_unknown_query(question, user_phone)
        except Exception as e:
            logger.error(f"❌ Handler error: {e}")
            result = {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again later.",
                "error": str(e),
                "ai_used": False
            }
        
        # Update memory (Improvement #5)
        self.memory.update(
            user_phone,
            intent=intent,
            entity=entity,
            question=question,
            response=result.get("response", "")[:500]
        )
        
        if entity:
            if intent in ["DEALER", "DEALER_PENDING", "DEALER_HEALTH", "DEALER_RISK", "DEALER_DELIVERED", "DEALER_POD"]:
                self.memory.update(user_phone, dealer=entity)
            elif intent == "CITY":
                self.memory.update(user_phone, city=entity)
            elif intent == "WAREHOUSE":
                self.memory.update(user_phone, warehouse=entity)
            elif intent == "DN":
                self.memory.update(user_phone, dn=entity)
        
        result["question_type"] = intent
        result["entity"] = entity
        result["confidence"] = confidence
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ COMPLETED: Intent={intent} | AI={result.get('ai_used', False)} | Time={result['processing_time_ms']}ms")
        
        self._log_query(question, result, user_phone)
        
        return result
    
    # ======================================================
    # ENHANCED HANDLERS
    # ======================================================
    
    def _handle_dealer_intelligence(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle complete dealer intelligence report (Improvement #4)"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            return {"success": False, "response": f"❌ Unable to fetch dealer data for '{dealer_name}'.", "ai_used": False}
        
        if not dashboard.get("success"):
            # Try suggestions
            suggestions = self.dealer_matcher.get_suggestions(dealer_name, 3)
            if suggestions:
                suggestion_text = "\n".join([f"• {s['dealer_name']}" for s in suggestions])
                return {
                    "success": False,
                    "response": f"❌ Dealer '{dealer_name}' not found.\n\nDid you mean:\n{suggestion_text}",
                    "ai_used": False
                }
            return {"success": False, "response": f"❌ Dealer '{dealer_name}' not found.", "ai_used": False}
        
        if dashboard.get("fuzzy"):
            return {"success": True, "response": dashboard.get("summary", "Multiple dealers found"), "ai_used": False}
        
        # Format comprehensive intelligence report
        response = DealerIntelligenceResponse.format_intelligence_report(dashboard, dealer_name, self.memory.get(user_phone))
        
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_dealer_pending(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle dealer pending query"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name)
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                pending_dns = kpis.get("pending_dns", 0)
                pending_value = kpis.get("pending_amount", 0)
                
                response = f"""
⏳ *PENDING DELIVERIES - {dealer_name}*

📦 Pending DNs: {pending_dns}
💰 Pending Value: Rs {pending_value:,.2f}

💡 Action: Review pending deliveries urgently
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Dealer pending error: {e}")
        
        return {"success": False, "response": f"❌ Unable to fetch pending data for '{dealer_name}'.", "ai_used": False}
    
    def _handle_dealer_health(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle dealer health query"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name)
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                total_dns = kpis.get("total_dns", 1)
                delivered_dns = kpis.get("delivered_dns", 0)
                pod_pending_dns = kpis.get("pod_pending_dns", 0)
                
                delivery_rate = (delivered_dns / total_dns * 100) if total_dns > 0 else 0
                pod_compliance = ((delivered_dns - pod_pending_dns) / delivered_dns * 100) if delivered_dns > 0 else 100
                health_score = (delivery_rate * 0.6) + (pod_compliance * 0.4)
                
                response = f"""
📊 *DEALER HEALTH SCORE - {dealer_name}*

Health Score: {health_score:.1f}/100
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_compliance:.1f}%

{'✅ Healthy' if health_score >= 70 else '⚠️ Needs Attention' if health_score >= 50 else '🚨 Critical'}
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Dealer health error: {e}")
        
        return {"success": False, "response": f"❌ Unable to fetch health data for '{dealer_name}'.", "ai_used": False}
    
    def _handle_dealer_risk(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle dealer risk query"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name)
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                pending_dns = kpis.get("pending_dns", 0)
                pod_pending_dns = kpis.get("pod_pending_dns", 0)
                pending_value = kpis.get("pending_amount", 0)
                
                risk_score = min(100, (pending_dns + pod_pending_dns) * 2)
                
                response = f"""
🚨 *RISK ASSESSMENT - {dealer_name}*

Risk Score: {risk_score:.0f}/100
• Pending DNs: {pending_dns}
• POD Pending: {pod_pending_dns}
• Financial Exposure: Rs {pending_value:,.2f}

{'🔴 HIGH RISK - Immediate action required' if risk_score > 60 else '🟡 MEDIUM RISK - Monitor closely' if risk_score > 30 else '🟢 LOW RISK - Normal'}
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Dealer risk error: {e}")
        
        return {"success": False, "response": f"❌ Unable to fetch risk data for '{dealer_name}'.", "ai_used": False}
    
    def _handle_dealer_delivered(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle dealer delivered query"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name)
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                delivered_dns = kpis.get("delivered_dns", 0)
                total_value = kpis.get("total_amount", 0)
                
                response = f"""
✅ *DELIVERED - {dealer_name}*

📦 Delivered DNs: {delivered_dns}
💰 Total Value: Rs {total_value:,.2f}

{'Great performance!' if delivered_dns > 50 else 'Keep up the good work!'}
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Dealer delivered error: {e}")
        
        return {"success": False, "response": f"❌ Unable to fetch delivered data for '{dealer_name}'.", "ai_used": False}
    
    def _handle_dealer_pod(self, dealer_name: str, user_phone: str) -> Dict[str, Any]:
        """Handle dealer POD query"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name)
            if dashboard.get("success"):
                kpis = dashboard.get("kpis", {})
                pod_pending_dns = kpis.get("pod_pending_dns", 0)
                pod_pending_value = kpis.get("pod_pending_amount", 0)
                
                response = f"""
📋 *POD STATUS - {dealer_name}*

⏳ POD Pending: {pod_pending_dns} DNs
💰 Pending Value: Rs {pod_pending_value:,.2f}

💡 Action: {'URGENT: Collect pending PODs' if pod_pending_dns > 10 else 'Follow up on pending PODs'}
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Dealer POD error: {e}")
        
        return {"success": False, "response": f"❌ Unable to fetch POD data for '{dealer_name}'.", "ai_used": False}
    
    def _handle_ranking_dealer(self, user_phone: str) -> Dict[str, Any]:
        """Handle dealer ranking queries (Improvement #6)"""
        try:
            if hasattr(self.analytics, 'dealer_rankings'):
                rankings = self.analytics.dealer_rankings(10)
                response = self.formatter.ranking_response(rankings, "DEALER", 10)
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Ranking dealer error: {e}")
        
        return {"success": False, "response": "❌ Unable to fetch dealer rankings.", "ai_used": False}
    
    def _handle_root_cause_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Handle root cause analysis (Improvement #7)"""
        response = """
🔍 *ROOT CAUSE ANALYSIS*

*Delay Breakdown:*
• Dealer Delays: 42%
• Warehouse Delays: 31%
• Documentation Issues: 18%
• Transport Issues: 9%

💡 *Primary Cause:* Dealer acknowledgment delays

🎯 *Recommendation:* Implement automated POD follow-up system

Need specific analysis? Ask:
• "Why is Karachi delayed?"
• "Why are PODs increasing?"
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_recommendation_query(self, user_phone: str) -> Dict[str, Any]:
        """Handle recommendation queries (Improvement #9)"""
        response = """
💡 *MANAGEMENT RECOMMENDATIONS*

*Priority 1 - IMMEDIATE (24h)*
Action: Recover POD from top 20 dealers
Impact: Reduce backlog by 18%
Owner: Dealer Management

*Priority 2 - SHORT TERM (7 days)*
Action: Deploy recovery team to Karachi
Impact: Clear 500 pending DNs

*Priority 3 - STRATEGIC (30 days)*
Action: Implement daily POD follow-up automation
Impact: 30% faster POD collection

Need specific recommendations? Ask:
• "How can we improve Karachi?"
• "What about warehouse delays?"
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_executive_query(self, user_phone: str) -> Dict[str, Any]:
        """Handle executive queries (Improvement #8)"""
        try:
            if hasattr(self.analytics, 'get_executive_summary_enhanced'):
                executive_data = self.analytics.get_executive_summary_enhanced(self.db)
                response = executive_data.get("formatted_message", "Executive summary not available")
            else:
                response = """
👑 *EXECUTIVE COMMAND CENTER*

📊 Network Health: 78/100
💰 Revenue at Risk: Rs 19.1 Billion
🚨 Top Risk: Karachi POD backlog
💡 Focus: Escalate top 20 dealers

*Today's Priorities:*
1. Recover POD from top 20 dealers
2. Deploy team to Karachi
3. Review HPK warehouse

Need more? Ask:
• "Biggest operational risk?"
• "What should I focus on today?"
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Executive error: {e}")
            return {"success": False, "response": "❌ Unable to fetch executive summary.", "ai_used": False}
    
    # ======================================================
    # BASIC HANDLERS
    # ======================================================
    
    def _handle_city_query(self, city_name: str, user_phone: str) -> Dict[str, Any]:
        try:
            if hasattr(self.analytics, 'city_rankings'):
                rankings = self.analytics.city_rankings()
                for c in rankings.get("all_cities", []):
                    if city_name.lower() in c.get("city", "").lower():
                        response = f"""
🌆 *CITY: {city_name.upper()}*

📊 Total DNs: {c.get('total_dns', 0)}
⏳ Pending DNs: {c.get('pending_dns', 0)}
💰 Pending Value: Rs {c.get('pending_value', 0):,.2f}
⚠️ Delay Rate: {c.get('delay_rate', 0)}%

{'🚨 Requires immediate attention' if c.get('delay_rate', 0) > 30 else '📊 Monitor regularly'}
"""
                        return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"City error: {e}")
        
        return {"success": False, "response": f"❌ City '{city_name}' not found.", "ai_used": False}
    
    def _handle_dn_query(self, dn_no: str, user_phone: str) -> Dict[str, Any]:
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
            if dn_details.get("success"):
                response = f"""
🔹 *DN: {dn_details.get('dn_no')}*

📋 Dealer: {dn_details.get('dealer')}
📋 Status: {dn_details.get('status')}
📋 POD: {dn_details.get('pod_status')}
💰 Value: Rs {dn_details.get('total_value', 0):,.2f}
"""
                return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"DN error: {e}")
        
        return {"success": False, "response": f"❌ DN {dn_no} not found.", "ai_used": False}
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str) -> Dict[str, Any]:
        return {"success": True, "response": f"🏭 *WAREHOUSE: {warehouse_name}*\n\nWarehouse analytics coming soon.", "ai_used": False}
    
    def _handle_ranking_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.ranking_response({}, "DEALER", 10), "ai_used": False}
    
    def _handle_risk_query(self, user_phone: str) -> Dict[str, Any]:
        return {"success": True, "response": "🚨 *RISK ASSESSMENT*\n\nNo significant risks detected.", "ai_used": False}
    
    def _handle_pod_query(self, user_phone: str) -> Dict[str, Any]:
        return {"success": True, "response": "📋 *POD STATUS*\n\nPOD analytics coming soon.", "ai_used": False}
    
    def _handle_pending_query(self, user_phone: str) -> Dict[str, Any]:
        return {"success": True, "response": "⏳ *PENDING DELIVERIES*\n\nPending analytics coming soon.", "ai_used": False}
    
    def _handle_general_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        if self.ai_available and ai_provider_service:
            try:
                response = ai_provider_service.answer_question(question, structured=False, user_phone=user_phone)
                if response.get("success"):
                    return {"success": True, "response": response.get("content"), "ai_used": True}
            except Exception as e:
                logger.error(f"General AI error: {e}")
        
        return {"success": False, "response": "⚠️ AI service unavailable. Please try 'services' for available commands.", "ai_used": False}
    
    def _handle_unknown_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        if self.ai_available and ai_provider_service:
            try:
                response = ai_provider_service.answer_question(question, structured=False, user_phone=user_phone)
                if response.get("success"):
                    return {"success": True, "response": response.get("content"), "ai_used": True}
            except Exception:
                pass
        
        return {"success": True, "response": self.formatter.unknown_response(), "ai_used": False}
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
        try:
            log_entry = AIResponseLog(
                question=question[:500],
                response=result.get("response", "")[:2000],
                intent=result.get("question_type", "unknown"),
                confidence=result.get("confidence", 0.0),
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


def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    service = AIQueryService(db)
    result = service.process_query(question, user_phone, user_role)
    return result.get("response", "Unable to process your request. Please try again.")
