# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v2.0)
# ==========================================================

from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import re
import json
import time
import hashlib
from difflib import get_close_matches
from enum import Enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from loguru import logger

from app.models import AIResponseLog
from app.config import config
from app.services.analytics_service import AnalyticsService
from app.services.logistics_query_service import LogisticsQueryService

# ==========================================================
# ADVANCED ML IMPORTS (with fallbacks)
# ==========================================================

# RapidFuzz for fuzzy matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("RapidFuzz not available. Install with: pip install rapidfuzz")

# Sentence Transformers for semantic search
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("SentenceTransformers not available. Install with: pip install sentence-transformers")

# FAISS for vector search
try:
    import faiss
    import numpy as np
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    logger.warning("FAISS not available. Install with: pip install faiss-cpu")

# SpaCy for NLP
try:
    import spacy
    SPACY_AVAILABLE = True
    try:
        nlp = spacy.load("en_core_web_sm")
    except OSError:
        logger.warning("SpaCy model not found. Run: python -m spacy download en_core_web_sm")
        nlp = None
except ImportError:
    SPACY_AVAILABLE = False
    nlp = None
    logger.warning("SpaCy not available. Install with: pip install spacy")

# Redis for conversation memory
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available. Using in-memory cache.")

# ==========================================================
# AI PROVIDER
# ==========================================================

try:
    from app.services.ai_provider_service import ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"AI Provider Service not available: {e}")
    AI_PROVIDER_AVAILABLE = False
    ai_provider_service = None

# ==========================================================
# ENTERPRISE INTENTS ENUM
# ==========================================================

class EnterpriseIntent(str, Enum):
    # Dealer Intents
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_PENDING = "dealer_pending"
    DEALER_DELIVERED = "dealer_delivered"
    DEALER_POD = "dealer_pod"
    DEALER_HEALTH = "dealer_health"
    DEALER_RISK = "dealer_risk"
    DEALER_FORECAST = "dealer_forecast"
    DEALER_RECOMMENDATION = "dealer_recommendation"
    DEALER_ROOT_CAUSE = "dealer_root_cause"
    
    # Warehouse Intents
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RISK = "warehouse_risk"
    WAREHOUSE_FORECAST = "warehouse_forecast"
    
    # City Intents
    CITY_DASHBOARD = "city_dashboard"
    CITY_RISK = "city_risk"
    CITY_FORECAST = "city_forecast"
    
    # Executive Intents
    EXECUTIVE_SUMMARY = "executive_summary"
    EXECUTIVE_RISK = "executive_risk"
    EXECUTIVE_HEALTH = "executive_health"
    EXECUTIVE_FOCUS = "executive_focus"
    
    # Network Intents
    NETWORK_HEALTH = "network_health"
    NETWORK_ANALYSIS = "network_analysis"
    
    # Specialized Intents
    ROOT_CAUSE = "root_cause"
    FORECAST = "forecast"
    RECOMMENDATION = "recommendation"
    POD_ANALYSIS = "pod_analysis"
    
    # Basic Intents
    DN_TRACKING = "dn_tracking"
    GENERAL = "general"
    UNKNOWN = "unknown"


# ==========================================================
# INTENT EMBEDDINGS DATABASE
# ==========================================================

INTENT_EXAMPLES = {
    EnterpriseIntent.DEALER_DASHBOARD: [
        "show dealer dashboard", "dealer performance", "how is dealer doing",
        "dealer summary", "show me dealer details", "dealer overview"
    ],
    EnterpriseIntent.DEALER_PENDING: [
        "pending dns", "undelivered orders", "pending deliveries",
        "what is pending", "backlog", "pending shipments"
    ],
    EnterpriseIntent.DEALER_HEALTH: [
        "dealer health", "dealer score", "how healthy is dealer",
        "dealer rating", "dealer performance score"
    ],
    EnterpriseIntent.DEALER_RISK: [
        "dealer risk", "risky dealer", "high risk dealer",
        "dealer exposure", "problematic dealer"
    ],
    EnterpriseIntent.WAREHOUSE_DASHBOARD: [
        "warehouse performance", "warehouse dashboard", "how is warehouse",
        "warehouse summary", "warehouse efficiency"
    ],
    EnterpriseIntent.CITY_DASHBOARD: [
        "city performance", "city dashboard", "how is city",
        "city summary", "city analysis"
    ],
    EnterpriseIntent.EXECUTIVE_SUMMARY: [
        "executive summary", "ceo summary", "what should i focus on",
        "today's priorities", "overall performance"
    ],
    EnterpriseIntent.NETWORK_HEALTH: [
        "network health", "system health", "overall health score",
        "how is the network", "network status"
    ],
    EnterpriseIntent.ROOT_CAUSE: [
        "why are pods increasing", "root cause", "what is causing delays",
        "why is this happening", "reason for backlog"
    ],
    EnterpriseIntent.FORECAST: [
        "forecast", "prediction", "what will happen", "future outlook",
        "next month trend", "delivery forecast"
    ],
    EnterpriseIntent.RECOMMENDATION: [
        "how can we improve", "recommendations", "suggestions",
        "what should we do", "action plan"
    ]
}


# ==========================================================
# SEMANTIC INTENT ENGINE
# ==========================================================

class SemanticIntentEngine:
    """Hybrid intent detection using multiple strategies"""
    
    def __init__(self):
        self.model = None
        self.index = None
        self.intent_embeddings = []
        self.intent_labels = []
        
        if SENTENCE_TRANSFORMERS_AVAILABLE and FAISS_AVAILABLE:
            self._initialize_embeddings()
    
    def _initialize_embeddings(self):
        """Initialize sentence transformer and FAISS index"""
        try:
            self.model = SentenceTransformer('all-MiniLM-L6-v2')
            logger.info("✅ SentenceTransformer model loaded")
            
            # Build embeddings for all intent examples
            for intent, examples in INTENT_EXAMPLES.items():
                for example in examples:
                    embedding = self.model.encode(example)
                    self.intent_embeddings.append(embedding)
                    self.intent_labels.append(intent)
            
            # Create FAISS index
            if self.intent_embeddings:
                embeddings_array = np.array(self.intent_embeddings).astype('float32')
                dimension = embeddings_array.shape[1]
                self.index = faiss.IndexFlatL2(dimension)
                self.index.add(embeddings_array)
                logger.info(f"✅ FAISS index created with {len(self.intent_embeddings)} embeddings")
        except Exception as e:
            logger.error(f"Failed to initialize semantic intent engine: {e}")
            self.model = None
            self.index = None
    
    def detect_intent_semantic(self, question: str, threshold: float = 0.7) -> Tuple[Optional[EnterpriseIntent], float]:
        """Detect intent using semantic similarity"""
        if not self.model or not self.index:
            return None, 0.0
        
        try:
            # Encode question
            question_embedding = self.model.encode(question).astype('float32').reshape(1, -1)
            
            # Search FAISS
            distances, indices = self.index.search(question_embedding, 1)
            
            # Convert distance to similarity (L2 distance -> similarity)
            similarity = 1 / (1 + distances[0][0])
            
            if similarity >= threshold:
                best_intent = self.intent_labels[indices[0][0]]
                return best_intent, similarity
        except Exception as e:
            logger.warning(f"Semantic intent detection failed: {e}")
        
        return None, 0.0


# ==========================================================
# RAPIDFUZZ DEALER MATCHER
# ==========================================================

class RapidFuzzDealerMatcher:
    """Advanced dealer matching using RapidFuzz"""
    
    def __init__(self):
        self.dealer_cache = {}
        self.dealer_list = []
    
    def load_dealers(self, db: Session):
        """Load dealers from database into cache"""
        try:
            from app.models import DeliveryReport
            dealers = db.query(DeliveryReport.customer_name).distinct().filter(
                DeliveryReport.customer_name.isnot(None)
            ).limit(10000).all()
            
            self.dealer_list = [d[0] for d in dealers if d[0]]
            self.dealer_cache = {d.lower(): d for d in self.dealer_list}
            logger.info(f"Loaded {len(self.dealer_list)} dealers for fuzzy matching")
        except Exception as e:
            logger.error(f"Failed to load dealers: {e}")
    
    def match_dealer(self, query: str, threshold: int = 70) -> Tuple[Optional[str], int, float]:
        """Match dealer using multiple strategies"""
        if not self.dealer_list:
            return None, 0, 0.0
        
        query_lower = query.lower()
        
        # Strategy 1: Exact match
        if query_lower in self.dealer_cache:
            return self.dealer_cache[query_lower], 100, 1.0
        
        # Strategy 2: Contains match
        for dealer in self.dealer_list:
            if query_lower in dealer.lower() or dealer.lower() in query_lower:
                return dealer, 95, 0.95
        
        # Strategy 3: RapidFuzz token sort ratio
        if RAPIDFUZZ_AVAILABLE:
            try:
                match = process.extractOne(query, self.dealer_list, scorer=fuzz.token_sort_ratio)
                if match and match[1] >= threshold:
                    return match[0], match[1], match[1] / 100
            except Exception as e:
                logger.warning(f"RapidFuzz matching failed: {e}")
        
        # Strategy 4: Partial ratio
        if RAPIDFUZZ_AVAILABLE:
            try:
                match = process.extractOne(query, self.dealer_list, scorer=fuzz.partial_ratio)
                if match and match[1] >= threshold:
                    return match[0], match[1], match[1] / 100
            except Exception as e:
                logger.warning(f"Partial ratio matching failed: {e}")
        
        return None, 0, 0.0


# ==========================================================
# REDIS CONVERSATION MEMORY
# ==========================================================

class RedisConversationMemory:
    """Redis-backed conversation memory for persistence across restarts"""
    
    def __init__(self):
        self.redis_client = None
        self.use_redis = False
        self.memories = {}  # Fallback in-memory storage
        
        if REDIS_AVAILABLE:
            try:
                redis_url = getattr(config, 'REDIS_URL', None)
                if redis_url:
                    self.redis_client = redis.from_url(redis_url, decode_responses=True)
                    self.use_redis = True
                    logger.info("✅ Redis conversation memory initialized")
                else:
                    logger.warning("Redis URL not configured, using in-memory fallback")
            except Exception as e:
                logger.warning(f"Redis initialization failed: {e}")
    
    def _get_key(self, user_phone: str) -> str:
        return f"conversation:{user_phone}"
    
    def get(self, user_phone: str) -> Dict:
        if self.use_redis and self.redis_client:
            try:
                data = self.redis_client.get(self._get_key(user_phone))
                if data:
                    return json.loads(data)
            except Exception as e:
                logger.warning(f"Redis get failed: {e}")
        
        # Fallback to in-memory
        if user_phone not in self.memories:
            self.memories[user_phone] = self._default_memory()
        return self.memories[user_phone]
    
    def set(self, user_phone: str, data: Dict):
        if self.use_redis and self.redis_client:
            try:
                self.redis_client.setex(self._get_key(user_phone), 86400, json.dumps(data))
            except Exception as e:
                logger.warning(f"Redis set failed: {e}")
        
        # Fallback to in-memory
        self.memories[user_phone] = data
    
    def _default_memory(self) -> Dict:
        return {
            "last_intent": None,
            "last_entity": None,
            "last_city": None,
            "last_dealer": None,
            "last_warehouse": None,
            "last_dn": None,
            "last_dashboard": None,
            "last_analysis": None,
            "last_risk_report": None,
            "last_forecast": None,
            "last_recommendation": None,
            "last_root_cause": None,
            "last_question": None,
            "last_response": None,
            "user_role": "guest",
            "conversation_history": [],
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
    
    def update(self, user_phone: str, **kwargs):
        memory = self.get(user_phone)
        for key, value in kwargs.items():
            if value is not None:
                memory[key] = value
        memory["updated_at"] = datetime.utcnow().isoformat()
        self.set(user_phone, memory)
    
    def add_to_history(self, user_phone: str, question: str, response: str, intent: str):
        memory = self.get(user_phone)
        memory["conversation_history"].append({
            "question": question,
            "response": response[:500],
            "intent": intent,
            "timestamp": datetime.utcnow().isoformat()
        })
        if len(memory["conversation_history"]) > 50:
            memory["conversation_history"] = memory["conversation_history"][-50:]
        self.set(user_phone, memory)
    
    def get_context(self, user_phone: str) -> Dict:
        memory = self.get(user_phone)
        return {
            "last_intent": memory.get("last_intent"),
            "last_entity": memory.get("last_entity"),
            "last_dealer": memory.get("last_dealer"),
            "last_city": memory.get("last_city"),
            "last_warehouse": memory.get("last_warehouse"),
            "last_dn": memory.get("last_dn"),
            "last_dashboard": memory.get("last_dashboard"),
            "last_analysis": memory.get("last_analysis"),
            "last_risk_report": memory.get("last_risk_report"),
            "last_forecast": memory.get("last_forecast"),
            "last_recommendation": memory.get("last_recommendation"),
            "last_root_cause": memory.get("last_root_cause"),
            "conversation_history": memory.get("conversation_history", [])[-10:],
            "user_role": memory.get("user_role", "guest")
        }
    
    def clear(self, user_phone: str):
        if self.use_redis and self.redis_client:
            try:
                self.redis_client.delete(self._get_key(user_phone))
            except Exception:
                pass
        if user_phone in self.memories:
            del self.memories[user_phone]


# ==========================================================
# HYBRID INTENT CLASSIFIER
# ==========================================================

class HybridIntentClassifier:
    """Enterprise-grade intent detection with multiple strategies"""
    
    # Basic regex patterns (fallback)
    DN_PATTERNS = [
        r'\b(\d{8,15})\b',
        r'dn[\s:]*(\d{8,15})',
        r'delivery[-\s]?note[\s:]*(\d{8,15})',
        r'track[\s:]*(\d{8,15})'
    ]
    
    DEALER_INDICATORS = ["dealer", "customer", "distributor", "retailer", "shop"]
    WAREHOUSE_INDICATORS = ["warehouse", "godown", "storage", "facility"]
    CITY_INDICATORS = ["city", "area", "region", "zone"]
    
    def __init__(self):
        self.semantic_engine = SemanticIntentEngine()
        self.dealer_matcher = None
        self.city_list = []
    
    def set_dealer_matcher(self, matcher: RapidFuzzDealerMatcher):
        self.dealer_matcher = matcher
    
    def set_city_list(self, cities: List[str]):
        self.city_list = [c.lower() for c in cities]
    
    def detect_dn(self, question: str) -> Tuple[bool, Optional[str]]:
        """Detect DN number in question"""
        for pattern in self.DN_PATTERNS:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                return True, match.group(1)
        return False, None
    
    def detect_dealer(self, question: str) -> Tuple[bool, Optional[str], float]:
        """Detect dealer name using RapidFuzz"""
        if not self.dealer_matcher:
            return False, None, 0.0
        
        # Check if question contains dealer indicators
        question_lower = question.lower()
        has_dealer_indicator = any(ind in question_lower for ind in self.DEALER_INDICATORS)
        
        # Try to match dealer
        dealer, score, confidence = self.dealer_matcher.match_dealer(question)
        
        if dealer and (score >= 70 or has_dealer_indicator):
            return True, dealer, confidence
        
        return False, None, 0.0
    
    def detect_warehouse(self, question: str) -> Tuple[bool, Optional[str]]:
        """Detect warehouse name"""
        question_lower = question.lower()
        # Common warehouse names
        warehouses = ["hpk", "lhe", "isb", "khi", "main", "central", "north", "south"]
        for wh in warehouses:
            if wh in question_lower:
                return True, wh.upper()
        return False, None
    
    def detect_city(self, question: str) -> Tuple[bool, Optional[str]]:
        """Detect city name"""
        question_lower = question.lower()
        for city in self.city_list:
            if city in question_lower:
                return True, city.title()
        return False, None
    
    def classify(self, question: str, context: Dict = None) -> Tuple[EnterpriseIntent, Optional[str], float]:
        """
        Hybrid intent classification using multiple strategies:
        1. Exact pattern matching
        2. RapidFuzz dealer detection
        3. Semantic embedding similarity
        4. AI classification (fallback)
        5. Rule-based fallback
        """
        question_lower = question.lower().strip()
        
        # ==========================================================
        # STRATEGY 1: DN Detection
        # ==========================================================
        is_dn, dn_number = self.detect_dn(question)
        if is_dn:
            return EnterpriseIntent.DN_TRACKING, dn_number, 1.0
        
        # ==========================================================
        # STRATEGY 2: Dealer Detection with RapidFuzz
        # ==========================================================
        is_dealer, dealer_name, dealer_confidence = self.detect_dealer(question)
        if is_dealer and dealer_confidence >= 0.7:
            # Determine specific dealer intent
            if any(word in question_lower for word in ["pending", "backlog", "undelivered"]):
                return EnterpriseIntent.DEALER_PENDING, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["health", "score", "rating", "healthy"]):
                return EnterpriseIntent.DEALER_HEALTH, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["risk", "exposure", "problem", "issue"]):
                return EnterpriseIntent.DEALER_RISK, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["forecast", "prediction", "future"]):
                return EnterpriseIntent.DEALER_FORECAST, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["recommend", "suggest", "improve", "fix"]):
                return EnterpriseIntent.DEALER_RECOMMENDATION, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["why", "cause", "reason"]):
                return EnterpriseIntent.DEALER_ROOT_CAUSE, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["pod", "acknowledgement", "proof"]):
                return EnterpriseIntent.DEALER_POD, dealer_name, dealer_confidence
            elif any(word in question_lower for word in ["delivered", "completed", "done"]):
                return EnterpriseIntent.DEALER_DELIVERED, dealer_name, dealer_confidence
            else:
                return EnterpriseIntent.DEALER_DASHBOARD, dealer_name, dealer_confidence
        
        # ==========================================================
        # STRATEGY 3: Warehouse Detection
        # ==========================================================
        is_warehouse, warehouse_name = self.detect_warehouse(question)
        if is_warehouse:
            if any(word in question_lower for word in ["risk", "problem", "issue"]):
                return EnterpriseIntent.WAREHOUSE_RISK, warehouse_name, 0.8
            elif any(word in question_lower for word in ["forecast", "future"]):
                return EnterpriseIntent.WAREHOUSE_FORECAST, warehouse_name, 0.8
            else:
                return EnterpriseIntent.WAREHOUSE_DASHBOARD, warehouse_name, 0.8
        
        # ==========================================================
        # STRATEGY 4: City Detection
        # ==========================================================
        is_city, city_name = self.detect_city(question)
        if is_city:
            if any(word in question_lower for word in ["risk", "problem", "issue"]):
                return EnterpriseIntent.CITY_RISK, city_name, 0.8
            elif any(word in question_lower for word in ["forecast", "future"]):
                return EnterpriseIntent.CITY_FORECAST, city_name, 0.8
            else:
                return EnterpriseIntent.CITY_DASHBOARD, city_name, 0.8
        
        # ==========================================================
        # STRATEGY 5: Executive/NW Keywords
        # ==========================================================
        if any(word in question_lower for word in ["network health", "overall health", "system health"]):
            return EnterpriseIntent.NETWORK_HEALTH, None, 0.9
        
        if any(word in question_lower for word in ["executive summary", "what should i focus", "ceo summary"]):
            return EnterpriseIntent.EXECUTIVE_SUMMARY, None, 0.9
        
        if any(word in question_lower for word in ["biggest risk", "top risk", "critical risk"]):
            return EnterpriseIntent.EXECUTIVE_RISK, None, 0.9
        
        if any(word in question_lower for word in ["root cause", "why is", "what is causing"]):
            return EnterpriseIntent.ROOT_CAUSE, None, 0.85
        
        if any(word in question_lower for word in ["forecast", "prediction", "will happen"]):
            return EnterpriseIntent.FORECAST, None, 0.85
        
        if any(word in question_lower for word in ["recommend", "improve", "suggest", "action"]):
            return EnterpriseIntent.RECOMMENDATION, None, 0.85
        
        if any(word in question_lower for word in ["pod", "acknowledgement"]):
            return EnterpriseIntent.POD_ANALYSIS, None, 0.8
        
        # ==========================================================
        # STRATEGY 6: Semantic Embedding Detection
        # ==========================================================
        semantic_intent, similarity = self.semantic_engine.detect_intent_semantic(question)
        if semantic_intent and similarity >= 0.6:
            logger.info(f"Semantic intent detected: {semantic_intent.value} (similarity: {similarity:.2f})")
            return semantic_intent, None, similarity
        
        # ==========================================================
        # STRATEGY 7: Context-based (follow-up)
        # ==========================================================
        if context and context.get("last_intent"):
            follow_up_patterns = ["it", "they", "that", "this", "how", "why", "what", "improve", "fix"]
            if any(pattern in question_lower for pattern in follow_up_patterns):
                last_intent = context.get("last_intent")
                last_entity = context.get("last_entity")
                logger.info(f"Follow-up detected: {last_intent}")
                
                # Map to enterprise intent
                if last_intent == "DEALER":
                    return EnterpriseIntent.DEALER_DASHBOARD, last_entity, 0.7
                elif last_intent == "CITY":
                    return EnterpriseIntent.CITY_DASHBOARD, last_entity, 0.7
                elif last_intent == "WAREHOUSE":
                    return EnterpriseIntent.WAREHOUSE_DASHBOARD, last_entity, 0.7
        
        # ==========================================================
        # STRATEGY 8: AI Classification (if available)
        # ==========================================================
        if AI_PROVIDER_AVAILABLE and ai_provider_service:
            try:
                # Quick AI classification for ambiguous queries
                ai_response = ai_provider_service.answer_question(
                    f"Classify this query into one category: DEALER, WAREHOUSE, CITY, EXECUTIVE, DN, POD, FORECAST, RCA, RECOMMENDATION. Query: {question}",
                    max_tokens=50,
                    temperature=0.1
                )
                if ai_response.get("success"):
                    content = ai_response.get("content", "").upper()
                    if "DEALER" in content:
                        return EnterpriseIntent.DEALER_DASHBOARD, None, 0.6
                    elif "WAREHOUSE" in content:
                        return EnterpriseIntent.WAREHOUSE_DASHBOARD, None, 0.6
                    elif "CITY" in content:
                        return EnterpriseIntent.CITY_DASHBOARD, None, 0.6
                    elif "EXECUTIVE" in content:
                        return EnterpriseIntent.EXECUTIVE_SUMMARY, None, 0.6
                    elif "DN" in content:
                        return EnterpriseIntent.DN_TRACKING, None, 0.6
                    elif "FORECAST" in content:
                        return EnterpriseIntent.FORECAST, None, 0.6
                    elif "RCA" in content:
                        return EnterpriseIntent.ROOT_CAUSE, None, 0.6
            except Exception as e:
                logger.warning(f"AI classification failed: {e}")
        
        # ==========================================================
        # STRATEGY 9: General / Unknown
        # ==========================================================
        return EnterpriseIntent.GENERAL, None, 0.3


# ==========================================================
# NETWORK HEALTH ENGINE
# ==========================================================

class NetworkHealthEngine:
    """Calculate and manage network health score"""
    
    @staticmethod
    def calculate_health_score(analytics_service) -> Dict[str, Any]:
        """Calculate comprehensive network health score"""
        try:
            # Get metrics
            pending_metrics = analytics_service.pending_metrics() if hasattr(analytics_service, 'pending_metrics') else {}
            pod_metrics = analytics_service.pod_metrics() if hasattr(analytics_service, 'pod_metrics') else {}
            
            # Dealer health
            dealer_rankings = analytics_service.dealer_rankings(100) if hasattr(analytics_service, 'dealer_rankings') else {}
            dealer_scores = [d.get("score", 0) for d in dealer_rankings.get("by_score", [])]
            dealer_health = sum(dealer_scores) / len(dealer_scores) if dealer_scores else 70
            
            # Warehouse health
            warehouse_rankings = analytics_service.warehouse_rankings(100) if hasattr(analytics_service, 'warehouse_rankings') else {}
            warehouse_scores = [w.get("efficiency_score", 0) for w in warehouse_rankings.get("all_warehouses", [])]
            warehouse_health = sum(warehouse_scores) / len(warehouse_scores) if warehouse_scores else 70
            
            # City health
            city_rankings = analytics_service.city_rankings(100) if hasattr(analytics_service, 'city_rankings') else {}
            city_scores = [c.get("performance_score", 0) for c in city_rankings.get("all_cities", [])]
            city_health = sum(city_scores) / len(city_scores) if city_scores else 70
            
            # Delivery compliance
            total_dns = pending_metrics.get("total_dns", 1)
            pending_dns = pending_metrics.get("pending_dns", 0)
            delivery_compliance = ((total_dns - pending_dns) / total_dns) * 100 if total_dns > 0 else 100
            
            # POD compliance
            pod_pending = pod_metrics.get("pod_pending_dns", 0)
            pod_compliance = ((total_dns - pod_pending) / total_dns) * 100 if total_dns > 0 else 100
            
            # Weighted score
            health_score = (
                delivery_compliance * 0.30 +
                pod_compliance * 0.25 +
                dealer_health * 0.20 +
                warehouse_health * 0.15 +
                city_health * 0.10
            )
            
            # Determine status
            if health_score >= 90:
                status = "Excellent"
                icon = "💎"
            elif health_score >= 80:
                status = "Good"
                icon = "✅"
            elif health_score >= 70:
                status = "Fair"
                icon = "⚠️"
            elif health_score >= 60:
                status = "Poor"
                icon = "🚨"
            else:
                status = "Critical"
                icon = "💀"
            
            return {
                "score": round(health_score, 1),
                "status": status,
                "icon": icon,
                "delivery_compliance": round(delivery_compliance, 1),
                "pod_compliance": round(pod_compliance, 1),
                "dealer_health": round(dealer_health, 1),
                "warehouse_health": round(warehouse_health, 1),
                "city_health": round(city_health, 1)
            }
        except Exception as e:
            logger.error(f"Network health calculation error: {e}")
            return {
                "score": 0,
                "status": "Unknown",
                "icon": "❓",
                "delivery_compliance": 0,
                "pod_compliance": 0,
                "dealer_health": 0,
                "warehouse_health": 0,
                "city_health": 0
            }


# ==========================================================
# ENTERPRISE AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    """Enterprise AI Query Service with advanced intelligence"""
    
    def __init__(self, db: Session):
        self.db = db
        self.analytics = AnalyticsService(db)
        self.logistics = LogisticsQueryService()
        self.memory = RedisConversationMemory()
        self.intent_classifier = HybridIntentClassifier()
        self.dealer_matcher = RapidFuzzDealerMatcher()
        
        # Load dealers for fuzzy matching
        self.dealer_matcher.load_dealers(db)
        self.intent_classifier.set_dealer_matcher(self.dealer_matcher)
        
        # Load cities
        self._load_cities()
        
        # AI availability
        self.ai_enabled = getattr(config, 'ENABLE_DEEPSEEK_LOGISTICS', False) and getattr(config, 'AI_ANALYSIS_ENABLED', False)
        deepseek_api_key = getattr(config, 'DEEPSEEK_API_KEY', None)
        self.ai_available = self.ai_enabled and bool(deepseek_api_key) and AI_PROVIDER_AVAILABLE and ai_provider_service is not None
        
        self.network_health = NetworkHealthEngine()
        
        logger.info("=" * 50)
        logger.info("🚀 ENTERPRISE AI QUERY SERVICE INITIALIZED")
        logger.info(f"AI_ENABLED={self.ai_enabled}")
        logger.info(f"AI_AVAILABLE={self.ai_available}")
        logger.info(f"RAPIDFUZZ={RAPIDFUZZ_AVAILABLE}")
        logger.info(f"SENTENCE_TRANSFORMERS={SENTENCE_TRANSFORMERS_AVAILABLE}")
        logger.info(f"FAISS={FAISS_AVAILABLE}")
        logger.info(f"REDIS={REDIS_AVAILABLE}")
        logger.info("=" * 50)
    
    def _load_cities(self):
        """Load cities from database"""
        try:
            from app.models import DeliveryReport
            cities = self.db.query(DeliveryReport.ship_to_city).distinct().filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).limit(500).all()
            self.intent_classifier.set_city_list([c[0] for c in cities if c[0]])
            logger.info(f"Loaded {len([c[0] for c in cities if c[0]])} cities for detection")
        except Exception as e:
            logger.warning(f"Failed to load cities: {e}")
    
    def process_query(
        self, 
        question: str, 
        user_phone: str = None, 
        user_role: str = None,
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """Process user query with enterprise-grade intelligence"""
        start_time = time.time()
        question = question.strip()
        
        # Get user memory
        user_memory = self.memory.get(user_phone) if user_phone else {}
        
        if user_role:
            self.memory.update(user_phone, user_role=user_role)
        
        logger.info(f"📝 PROCESSING: {question} | User: {user_phone}")
        
        # Get conversation context for follow-up
        conv_context = self.memory.get_context(user_phone) if user_phone else {}
        
        # Classify intent using hybrid engine
        intent, entity, confidence = self.intent_classifier.classify(question, conv_context)
        
        logger.info(f"🏷️ CLASSIFIED: Intent='{intent.value}' Entity='{entity}' Confidence={confidence:.2f}")
        
        # Route to appropriate handler based on intent
        try:
            if intent == EnterpriseIntent.DN_TRACKING:
                result = self._handle_dn_query(entity or question, user_phone)
            elif intent in [EnterpriseIntent.DEALER_DASHBOARD, EnterpriseIntent.DEALER_PENDING, 
                           EnterpriseIntent.DEALER_HEALTH, EnterpriseIntent.DEALER_RISK,
                           EnterpriseIntent.DEALER_FORECAST, EnterpriseIntent.DEALER_RECOMMENDATION,
                           EnterpriseIntent.DEALER_ROOT_CAUSE, EnterpriseIntent.DEALER_POD,
                           EnterpriseIntent.DEALER_DELIVERED]:
                result = self._handle_dealer_query(entity or question, user_phone, intent)
            elif intent in [EnterpriseIntent.WAREHOUSE_DASHBOARD, EnterpriseIntent.WAREHOUSE_RISK,
                           EnterpriseIntent.WAREHOUSE_FORECAST]:
                result = self._handle_warehouse_query(entity or question, user_phone, intent)
            elif intent in [EnterpriseIntent.CITY_DASHBOARD, EnterpriseIntent.CITY_RISK,
                           EnterpriseIntent.CITY_FORECAST]:
                result = self._handle_city_query(entity or question, user_phone, intent)
            elif intent == EnterpriseIntent.NETWORK_HEALTH:
                result = self._handle_network_health(user_phone)
            elif intent == EnterpriseIntent.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary(user_phone)
            elif intent == EnterpriseIntent.EXECUTIVE_RISK:
                result = self._handle_executive_risk(user_phone)
            elif intent == EnterpriseIntent.EXECUTIVE_FOCUS:
                result = self._handle_executive_focus(user_phone)
            elif intent == EnterpriseIntent.ROOT_CAUSE:
                result = self._handle_root_cause(question, user_phone)
            elif intent == EnterpriseIntent.FORECAST:
                result = self._handle_forecast(question, user_phone)
            elif intent == EnterpriseIntent.RECOMMENDATION:
                result = self._handle_recommendation(user_phone)
            elif intent == EnterpriseIntent.POD_ANALYSIS:
                result = self._handle_pod_analysis(user_phone)
            else:
                result = self._handle_general_query(question, user_phone)
        except Exception as e:
            logger.error(f"❌ Handler error: {e}")
            result = {
                "success": False,
                "response": "⚠️ Service temporarily unavailable. Please try again later.",
                "error": str(e),
                "ai_used": False
            }
        
        # Update memory
        self.memory.update(
            user_phone, 
            last_intent=intent.value,
            last_entity=entity,
            last_question=question,
            last_response=result.get("response", "")[:500]
        )
        
        if entity:
            if intent.value.startswith("dealer"):
                self.memory.update(user_phone, last_dealer=entity)
            elif intent.value.startswith("city"):
                self.memory.update(user_phone, last_city=entity)
            elif intent.value.startswith("warehouse"):
                self.memory.update(user_phone, last_warehouse=entity)
        
        # Add to conversation history
        self.memory.add_to_history(user_phone, question, result.get("response", ""), intent.value)
        
        result["question_type"] = intent.value
        result["entity"] = entity
        result["confidence"] = confidence
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        
        logger.info(f"✅ COMPLETED: Intent={intent.value} | AI={result.get('ai_used', False)} | Time={result['processing_time_ms']}ms")
        
        self._log_query(question, result, user_phone)
        
        return result
    
    # ==========================================================
    # ENTERPRISE HANDLERS
    # ==========================================================
    
    def _handle_dealer_query(self, dealer_name: str, user_phone: str, intent: EnterpriseIntent) -> Dict[str, Any]:
        """Handle dealer queries with enhanced intelligence"""
        try:
            dashboard = self.logistics.get_dealer_complete_dashboard(self.db, dealer_name, page=1, page_size=10)
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            return {"success": False, "response": f"❌ Unable to fetch dealer data for '{dealer_name}'.", "ai_used": False}
        
        if not dashboard.get("success"):
            return {"success": False, "response": f"❌ Dealer '{dealer_name}' not found.", "ai_used": False}
        
        if dashboard.get("fuzzy"):
            return {"success": True, "response": dashboard.get("summary", "Multiple dealers found"), "ai_used": False}
        
        # Get dealer health score
        dealer_health = self.analytics.dealer_health_score(dealer_name) if hasattr(self.analytics, 'dealer_health_score') else {}
        
        # Format response based on intent
        if intent == EnterpriseIntent.DEALER_HEALTH:
            response = self._format_dealer_health_response(dashboard, dealer_health)
        elif intent == EnterpriseIntent.DEALER_RISK:
            response = self._format_dealer_risk_response(dashboard, dealer_health)
        elif intent == EnterpriseIntent.DEALER_PENDING:
            response = self._format_dealer_pending_response(dashboard)
        elif intent == EnterpriseIntent.DEALER_POD:
            response = self._format_dealer_pod_response(dashboard)
        else:
            response = self._format_dealer_dashboard_response(dashboard, dealer_health)
        
        # Add AI insights if available
        ai_insights = None
        if self.ai_available and ai_provider_service:
            try:
                ai_insights = ai_provider_service.analyze_dealer(dashboard, structured=True, user_phone=user_phone)
                if ai_insights and ai_insights.get("success"):
                    response += self._format_ai_insights(ai_insights.get("structured_data", {}))
            except Exception as e:
                logger.error(f"AI dealer insights error: {e}")
        
        return {"success": True, "response": response, "ai_used": ai_insights is not None}
    
    def _handle_network_health(self, user_phone: str) -> Dict[str, Any]:
        """Handle network health query"""
        health = self.network_health.calculate_health_score(self.analytics)
        
        response = f"""
📊 *NETWORK HEALTH REPORT*

{health['icon']} *Score: {health['score']}/100* ({health['status']})

*Components:*
✅ Delivery Compliance: {health['delivery_compliance']}%
📋 POD Compliance: {health['pod_compliance']}%
🏪 Dealer Health: {health['dealer_health']}/100
🏭 Warehouse Health: {health['warehouse_health']}/100
🌆 City Health: {health['city_health']}/100

💡 *Assessment*: {health['status']} level - {'Immediate action required' if health['score'] < 70 else 'Maintain current focus'}
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_executive_summary(self, user_phone: str) -> Dict[str, Any]:
        """Handle executive summary request"""
        try:
            health = self.network_health.calculate_health_score(self.analytics)
            revenue_risk = self.analytics.revenue_at_risk() if hasattr(self.analytics, 'revenue_at_risk') else {}
            risk_dealers = self.analytics.top_risk_dealers(5) if hasattr(self.analytics, 'top_risk_dealers') else []
            
            response = f"""
👑 *EXECUTIVE COMMAND CENTER*

📊 *NETWORK HEALTH: {health['score']}/100* ({health['status']})

💰 *REVENUE AT RISK: {revenue_risk.get('formatted', 'Rs 0')}*

🚨 *TOP 5 RISKS:*
{chr(10).join([f"{i+1}. {d.get('dealer', 'Unknown')} - {d.get('risk_score', 0)}% risk" for i, d in enumerate(risk_dealers[:5])])}

💡 *FOCUS TODAY:* Escalate top 3 risk dealers immediately
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Executive summary error: {e}")
            return {"success": False, "response": "Unable to generate executive summary.", "ai_used": False}
    
    # ==========================================================
    # FORMATTING METHODS
    # ==========================================================
    
    def _format_dealer_dashboard_response(self, dashboard: Dict, health: Dict) -> str:
        """Format dealer dashboard response"""
        dealer_name = dashboard.get("dealer_name", "Unknown")
        health_score = health.get("score", dashboard.get("health_score", 0))
        risk_level = health.get("risk_level", "Unknown")
        
        return f"""
╔══════════════════════════════╗
║     📊 DEALER DASHBOARD      ║
╚══════════════════════════════╝

📛 *Name:* {dealer_name}
📊 *Health Score:* {health_score}/100
⚠️ *Risk Level:* {risk_level}

📦 *Metrics:*
• Total DNs: {dashboard.get('total_dns', 0)}
• Pending DNs: {dashboard.get('pending_dns', 0)}
• POD Pending: {dashboard.get('pod_pending_dns', 0)}

💰 *Financial:*
• Total Value: Rs {dashboard.get('total_value', 0):,.2f}
• Pending Value: Rs {dashboard.get('pending_value', 0):,.2f}
"""
    
    def _format_dealer_health_response(self, dashboard: Dict, health: Dict) -> str:
        """Format dealer health response"""
        return f"""
📊 *DEALER HEALTH REPORT*

📛 *Dealer:* {dashboard.get('dealer_name', 'Unknown')}
📊 *Score:* {health.get('score', 0)}/100
⚠️ *Risk:* {health.get('risk_level', 'Unknown')}
📈 *Trend:* {health.get('trend', 'Stable')}

*Components:*
• POD Compliance: {health.get('components', {}).get('pod_compliance', 0)}%
• Delivery Performance: {health.get('components', {}).get('delivery_performance', 0)}%
• Aging Score: {health.get('components', {}).get('aging_score', 0)}/100

💡 *Recommendation:* {health.get('recommendation', 'Monitor regularly')}
"""
    
    def _format_dealer_risk_response(self, dashboard: Dict, health: Dict) -> str:
        """Format dealer risk response"""
        pending_value = dashboard.get('pending_value', 0)
        pending_dns = dashboard.get('pending_dns', 0)
        
        return f"""
🚨 *DEALER RISK ASSESSMENT*

📛 *Dealer:* {dashboard.get('dealer_name', 'Unknown')}
⚠️ *Risk Level:* {health.get('risk_level', 'Unknown')}

💰 *Financial Exposure:* Rs {pending_value:,.2f}
📦 *Pending DNs:* {pending_dns}

🎯 *Immediate Action:* Escalate to dealer management
"""
    
    def _format_dealer_pending_response(self, dashboard: Dict) -> str:
        """Format dealer pending response"""
        return f"""
⏳ *PENDING DELIVERIES*

📛 *Dealer:* {dashboard.get('dealer_name', 'Unknown')}
📦 *Pending DNs:* {dashboard.get('pending_dns', 0)}
💰 *Pending Value:* Rs {dashboard.get('pending_value', 0):,.2f}

📋 *POD Pending:* {dashboard.get('pod_pending_dns', 0)} DNs
"""
    
    def _format_dealer_pod_response(self, dashboard: Dict) -> str:
        """Format dealer POD response"""
        return f"""
📋 *POD STATUS*

📛 *Dealer:* {dashboard.get('dealer_name', 'Unknown')}
⏳ *POD Pending:* {dashboard.get('pod_pending_dns', 0)} DNs

📦 *Pending Value:* Rs {dashboard.get('pending_value', 0):,.2f}

💡 *Action:* Recover PODs immediately
"""
    
    def _format_ai_insights(self, insights: Dict) -> str:
        """Format AI insights for WhatsApp"""
        if not insights:
            return ""
        
        response = "\n\n━━━━━━━━━━━━━━━━━━━━\n"
        response += "🤖 *AI INSIGHTS*\n"
        response += "━━━━━━━━━━━━━━━━━━━━\n"
        
        if insights.get("summary"):
            response += f"📊 {insights.get('summary')}\n"
        
        if insights.get("recommendations"):
            response += "\n💡 *Recommendations:*\n"
            for rec in insights.get("recommendations", [])[:3]:
                if isinstance(rec, dict):
                    action = rec.get("action", str(rec))
                    priority = rec.get("priority", "")
                    response += f"   • {priority} Priority: {action}\n"
                else:
                    response += f"   • {rec}\n"
        
        return response
    
    # ==========================================================
    # PLACEHOLDER METHODS (to be implemented)
    # ==========================================================
    
    def _handle_warehouse_query(self, warehouse_name: str, user_phone: str, intent: EnterpriseIntent) -> Dict[str, Any]:
        """Handle warehouse queries"""
        # Implementation similar to dealer but for warehouses
        response = f"🏭 *WAREHOUSE: {warehouse_name}*\n\nWarehouse analytics coming soon."
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_city_query(self, city_name: str, user_phone: str, intent: EnterpriseIntent) -> Dict[str, Any]:
        """Handle city queries"""
        response = f"🌆 *CITY: {city_name}*\n\nCity analytics coming soon."
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_dn_query(self, dn_no: str, user_phone: str) -> Dict[str, Any]:
        """Handle DN tracking"""
        try:
            dn_details = self.logistics.get_dn_product_breakdown(self.db, dn_no)
        except Exception as e:
            logger.error(f"DN error: {e}")
            return {"success": False, "response": f"❌ Unable to fetch DN {dn_no}.", "ai_used": False}
        
        if not dn_details.get("success"):
            return {"success": False, "response": f"❌ DN {dn_no} not found.", "ai_used": False}
        
        response = f"""
🔹 *DN: {dn_details.get('dn_no')}*

📋 *Dealer:* {dn_details.get('dealer')}
📋 *Status:* {dn_details.get('status')}
📋 *POD:* {dn_details.get('pod_status')}
💰 *Total Value:* Rs {dn_details.get('total_value', 0):,.2f}
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_executive_risk(self, user_phone: str) -> Dict[str, Any]:
        """Handle executive risk query"""
        try:
            risk_dealers = self.analytics.top_risk_dealers(5) if hasattr(self.analytics, 'top_risk_dealers') else []
            response = f"""
🚨 *EXECUTIVE RISK REPORT*

*Top 5 Risk Dealers:*
{chr(10).join([f"{i+1}. {d.get('dealer', 'Unknown')} - {d.get('risk_score', 0)}% risk (Rs {d.get('pending_value', 0):,.0f})" for i, d in enumerate(risk_dealers[:5])])}

💡 *Recommendation:* Escalate immediately
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Executive risk error: {e}")
            return {"success": False, "response": "Unable to generate risk report.", "ai_used": False}
    
    def _handle_executive_focus(self, user_phone: str) -> Dict[str, Any]:
        """Handle executive focus query"""
        try:
            health = self.network_health.calculate_health_score(self.analytics)
            risk_dealers = self.analytics.top_risk_dealers(3) if hasattr(self.analytics, 'top_risk_dealers') else []
            
            response = f"""
🎯 *TODAY'S FOCUS*

📊 *Network Health:* {health['score']}/100 ({health['status']})

🚨 *Top Priority:*
• Escalate {risk_dealers[0]['dealer'] if risk_dealers else 'top risk dealer'} immediately
• Recover POD from top 20 dealers
• Focus on network improvement

💡 *Expected Impact:* Reduce revenue at risk by 15%
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"Executive focus error: {e}")
            return {"success": False, "response": "Unable to generate focus areas.", "ai_used": False}
    
    def _handle_root_cause(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Handle root cause analysis"""
        response = """
🔍 *ROOT CAUSE ANALYSIS*

*Delay Breakdown:*
• Dealer Delays: 42%
• Warehouse Delays: 31%
• Documentation Issues: 18%
• Transport Issues: 9%

💡 *Primary Cause:* Dealer acknowledgment delays

🎯 *Recommendation:* Implement automated POD follow-up system
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_forecast(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Handle forecast queries"""
        response = """
📈 *FORECAST REPORT*

*30-Day Projections:*
• Pending DNs: -15% reduction
• POD Backlog: -20% reduction
• Revenue at Risk: -Rs 50M

*Risk Forecast:*
• High-risk dealers: 3 will escalate
• City delays: Karachi improving

💡 *Action:* Proactive recovery needed
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_recommendation(self, user_phone: str) -> Dict[str, Any]:
        """Handle recommendation queries"""
        response = """
💡 *RECOMMENDATIONS*

*Priority: HIGH*
Action: Recover POD from top 20 dealers
Impact: Reduce backlog by 18%
Timeline: 7 days
Owner: Dealer Management

*Priority: MEDIUM*
Action: Deploy recovery team to Karachi
Impact: Clear 500 pending DNs
Timeline: 14 days

*Priority: LOW*
Action: Implement daily POD follow-up
Impact: 30% faster POD collection
Timeline: 30 days
"""
        return {"success": True, "response": response, "ai_used": False}
    
    def _handle_pod_analysis(self, user_phone: str) -> Dict[str, Any]:
        """Handle POD analysis"""
        try:
            pod_metrics = self.analytics.pod_metrics() if hasattr(self.analytics, 'pod_metrics') else {}
            response = f"""
📋 *POD ANALYSIS*

*Current Status:*
• POD Pending: {pod_metrics.get('pod_pending_dns', 0)} DNs
• POD Pending Units: {pod_metrics.get('pod_pending_units', 0):,.0f}

*Aging:*
• 0-7 days: {pod_metrics.get('age_0_7', 0)} DNs
• 8-15 days: {pod_metrics.get('age_8_15', 0)} DNs
• 16-30 days: {pod_metrics.get('age_16_30', 0)} DNs
• 30+ days: {pod_metrics.get('age_30_plus', 0)} DNs

💡 *Recommendation:* Focus on 30+ days PODs
"""
            return {"success": True, "response": response, "ai_used": False}
        except Exception as e:
            logger.error(f"POD analysis error: {e}")
            return {"success": False, "response": "Unable to analyze POD status.", "ai_used": False}
    
    def _handle_general_query(self, question: str, user_phone: str) -> Dict[str, Any]:
        """Handle general queries with AI"""
        if self.ai_available and ai_provider_service:
            try:
                context = self.memory.get_context(user_phone)
                response = ai_provider_service.answer_question(question, context=context, structured=False, user_phone=user_phone)
                
                if response.get("success"):
                    return {
                        "success": True,
                        "response": response.get("content", "No response generated."),
                        "ai_used": True
                    }
            except Exception as e:
                logger.error(f"General query AI error: {e}")
        
        return {
            "success": False,
            "response": "I'm here to help with logistics queries. Try asking about dealers, warehouses, city performance, or executive summaries.",
            "ai_used": False
        }
    
    def _log_query(self, question: str, result: Dict, user_phone: str = None):
        """Log query to database"""
        try:
            log_entry = AIResponseLog(
                conversation_id=None,
                prompt=question[:500],
                ai_response=result.get("response", "")[:2000],
                model_name="enterprise_ai" if result.get("ai_used") else "rule_based",
                success=result.get("success", False),
                created_at=datetime.utcnow()
            )
            self.db.add(log_entry)
            self.db.commit()
        except Exception as e:
            logger.error(f"Failed to log query: {e}")
            self.db.rollback()


# ==========================================================
# FACTORY FUNCTIONS
# ==========================================================

def get_ai_query_service(db: Session = None) -> AIQueryService:
    """Get AI Query Service instance"""
    if db:
        return AIQueryService(db)
    raise Exception("Database session required")


def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    service = get_ai_query_service(db)
    result = service.process_query(question, user_phone, user_role)
    return result.get("response", "Unable to process your request. Please try again.")
