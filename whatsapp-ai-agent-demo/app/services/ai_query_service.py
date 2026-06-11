# ==========================================================
# FILE: app/services/ai_query_service.py (IMPROVED v44.0 - PURE ROUTER)
# ==========================================================
# PURPOSE: PURE ORCHESTRATOR - Route Only, No Business Logic
#
# CORE PRINCIPLES v44.0:
# - ONLY Intent Detection, Entity Extraction, Routing
# - NO Business Logic (delegates to AnalyticsService)
# - NO Hardcoded Responses (uses service responses)
# - AI Fallback ONLY when services unavailable
# ==========================================================

from __future__ import annotations

import re
import time
import hashlib
import uuid
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from collections import OrderedDict
from sqlalchemy.orm import Session
from loguru import logger

# Optional Redis support
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available. Using in-memory context only.")

# Feature flags
ENABLE_AUDIT_LOGGING = False


# ==========================================================
# CRITICAL FIX #1: UPDATED DN PATTERN (Fixes 6243611870 detection)
# ==========================================================

# OLD pattern (wrong): r'\b80\d{8}\b' - Only detects 80xxxxxxx
# NEW pattern (correct): r'\b\d{10,12}\b' - Detects any 10-12 digit number

DN_PATTERN = re.compile(r'\b\d{10,12}\b')


# ==========================================================
# TTL CACHE IMPLEMENTATION
# ==========================================================

class TTLCache:
    """Time-To-Live Cache for frequent queries"""
    
    def __init__(self, maxsize: int = 200, ttl_seconds: int = 300):
        self.maxsize = maxsize
        self.ttl = ttl_seconds
        self.cache = OrderedDict()
        self.timestamps = {}
    
    def _make_key(self, intent: str, params: str = "") -> str:
        key_str = f"{intent}:{params}"
        return hashlib.md5(key_str.encode()).hexdigest()
    
    def get(self, intent: str, params: str = "") -> Optional[Any]:
        key = self._make_key(intent, params)
        
        if key in self.cache:
            timestamp = self.timestamps.get(key)
            if timestamp and (datetime.now() - timestamp).seconds < self.ttl:
                self.cache.move_to_end(key)
                return self.cache[key]
            else:
                del self.cache[key]
                del self.timestamps[key]
        return None
    
    def set(self, value: Any, intent: str, params: str = ""):
        key = self._make_key(intent, params)
        
        if len(self.cache) >= self.maxsize:
            oldest_key = next(iter(self.cache))
            del self.cache[oldest_key]
            del self.timestamps[oldest_key]
        
        self.cache[key] = value
        self.timestamps[key] = datetime.now()
        self.cache.move_to_end(key)
    
    def clear(self):
        self.cache.clear()
        self.timestamps.clear()
    
    def get_stats(self) -> Dict:
        return {
            "size": len(self.cache),
            "maxsize": self.maxsize,
            "ttl_seconds": self.ttl,
            "utilization": round(len(self.cache) / self.maxsize * 100, 1) if self.maxsize > 0 else 0
        }


# ==========================================================
# LRU CONTEXT MANAGER
# ==========================================================

@dataclass
class ConversationMemory:
    """Stores conversation context for a user"""
    last_question: str = ""
    last_intent: str = ""
    last_response: str = ""
    last_dn: Optional[str] = None
    last_city: Optional[str] = None
    last_dealer: Optional[str] = None
    last_warehouse: Optional[str] = None
    conversation_history: List[Dict] = field(default_factory=list)
    last_interaction_time: datetime = field(default_factory=datetime.now)
    interaction_count: int = 0
    
    def add_exchange(self, question: str, response: str, intent: str):
        self.conversation_history.append({
            "question": question,
            "response": response[:200],
            "intent": intent,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.conversation_history) > 10:
            self.conversation_history = self.conversation_history[-10:]
        self.last_question = question
        self.last_response = response
        self.last_intent = intent
        self.last_interaction_time = datetime.now()
        self.interaction_count += 1


class LRUContextManager:
    def __init__(self, max_users: int = 500):
        self.max_users = max_users
        self.contexts = OrderedDict()
        self.conversation_memory: Dict[str, ConversationMemory] = {}
    
    def get(self, user_id: str) -> Dict:
        if user_id in self.contexts:
            self.contexts.move_to_end(user_id)
            return self.contexts[user_id]
        return {}
    
    def set(self, user_id: str, context: Dict):
        if user_id in self.contexts:
            self.contexts.move_to_end(user_id)
        else:
            if len(self.contexts) >= self.max_users:
                oldest_key = next(iter(self.contexts))
                del self.contexts[oldest_key]
                if oldest_key in self.conversation_memory:
                    del self.conversation_memory[oldest_key]
                logger.debug(f"Evicted context for user {oldest_key}")
        self.contexts[user_id] = context
    
    def get_memory(self, user_id: str) -> ConversationMemory:
        if user_id not in self.conversation_memory:
            self.conversation_memory[user_id] = ConversationMemory()
        return self.conversation_memory[user_id]
    
    def update_memory(self, user_id: str, question: str, response: str, intent: str, entities: 'ExtractedEntities'):
        memory = self.get_memory(user_id)
        memory.add_exchange(question, response, intent)
        
        if entities.dn_number:
            memory.last_dn = entities.dn_number
        if entities.city:
            memory.last_city = entities.city
        if entities.dealer:
            memory.last_dealer = entities.dealer
        if entities.warehouse:
            memory.last_warehouse = entities.warehouse
    
    def get_size(self) -> int:
        return len(self.contexts)
    
    def get_stats(self) -> Dict:
        return {
            "size": len(self.contexts),
            "max_users": self.max_users,
            "active_memories": len(self.conversation_memory),
            "utilization": round(len(self.contexts) / self.max_users * 100, 1)
        }


# ==========================================================
# INTENT TYPES
# ==========================================================

class Intent(str, Enum):
    # DN Operations
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    
    # Pending Items
    PENDING_POD = "pending_pod"
    PENDING_PGI = "pending_pgi"
    PENDING_DELIVERIES = "pending_deliveries"
    
    # Dealer Operations
    DEALER_PROFILE = "dealer_profile"
    DEALER_EXECUTIVE = "dealer_executive"
    DEALER_PERFORMANCE = "dealer_performance"
    DEALER_PRODUCTS = "dealer_products"
    DEALER_DN_ANALYSIS = "dealer_dn_analysis"
    DEALER_REVENUE = "dealer_revenue"
    DEALER_HEALTH = "dealer_health"
    DEALER_RISK = "dealer_risk"
    DEALER_COMPARE = "dealer_compare"
    DEALER_WAREHOUSE = "dealer_warehouse"
    DEALER_CITY = "dealer_city"
    DEALER_MANAGER = "dealer_manager"
    TOP_DEALERS = "top_dealers"
    BOTTOM_DEALERS = "bottom_dealers"
    
    # Warehouse Operations
    TOP_WAREHOUSES = "top_warehouses"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    
    # KPI Operations
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    CRITICAL_DELAYS = "critical_delays"
    CONTROL_TOWER = "control_tower"
    
    # Analysis
    ROOT_CAUSE = "root_cause"
    AI_QUERY = "ai_query"
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"


class QueryClass(str, Enum):
    OPERATIONAL = "operational"
    ANALYTICAL = "analytical"
    DEALER = "dealer"
    AI = "ai"


# ==========================================================
# CRITICAL FIX #2: ENHANCED ENTITY EXTRACTION
# ==========================================================

@dataclass
class ExtractedEntities:
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    dealer2: Optional[str] = None
    dealer_code: Optional[str] = None
    warehouse: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    sales_manager: Optional[str] = None
    product: Optional[str] = None
    days: Optional[int] = None
    limit: Optional[int] = 10
    last_intent: Optional[str] = None
    last_dn: Optional[str] = None
    last_dealer: Optional[str] = None
    last_city: Optional[str] = None
    compare_mode: bool = False
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def has_any(self) -> bool:
        return any([self.dn_number, self.dealer, self.warehouse, self.city])


class EntityExtractor:
    # FIXED: Detects any 10-12 digit number (including 6243611870)
    DN_PATTERN = re.compile(r'\b\d{10,12}\b')
    
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    LIMIT_PATTERN = re.compile(r'(?:top|limit)\s+(\d+)', re.IGNORECASE)
    COMPARE_PATTERN = re.compile(r'compare\s+([\w\s]+?)\s+(?:vs|and|with)\s+([\w\s]+)', re.IGNORECASE)
    
    # Keywords that indicate the question is NOT a dealer query
    NON_DEALER_KEYWORDS = [
        'pending', 'pod', 'pgi', 'delivery', 'dn', 'track', 'status',
        'top', 'best', 'ranking', 'warehouse', 'kpi', 'dashboard',
        'network', 'health', 'control', 'tower', 'critical', 'delay',
        'help', 'hi', 'hello', 'hey', 'good morning', 'good afternoon'
    ]
    
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
              'hyderabad', 'sukkur', 'bahawalpur', 'sahiwal', 'jhelum', 'sargodha']
    
    @classmethod
    def extract(cls, question: str, context: Dict = None, memory=None) -> ExtractedEntities:
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # Load context memory
        if context:
            entities.last_intent = context.get("last_intent")
            entities.last_dn = context.get("last_dn")
            entities.last_dealer = context.get("last_dealer")
            entities.last_city = context.get("last_city")
        
        if memory:
            if not entities.last_dn and memory.last_dn:
                entities.last_dn = memory.last_dn
            if not entities.last_dealer and memory.last_dealer:
                entities.last_dealer = memory.last_dealer
            if not entities.last_city and memory.last_city:
                entities.last_city = memory.last_city
        
        # Check for dealer comparison
        compare_match = cls.COMPARE_PATTERN.search(question)
        if compare_match:
            entities.dealer = compare_match.group(1).strip()
            entities.dealer2 = compare_match.group(2).strip()
            entities.compare_mode = True
        
        # Extract DN number (FIXED pattern)
        dn_match = cls.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(0)
        
        if not entities.dn_number and entities.last_dn:
            entities.dn_number = entities.last_dn
        
        # Extract days and limit
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        limit_match = cls.LIMIT_PATTERN.search(question_lower)
        if limit_match:
            entities.limit = min(int(limit_match.group(1)), 50)
        
        # Extract city
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        else:
            if entities.last_city:
                entities.city = entities.last_city
        
        # CRITICAL FIX #3: STANDALONE DEALER DETECTION
        # If question is short, has no DN, no keywords, treat as dealer name
        words = question_lower.split()
        is_standalone_dealer = (
            len(words) <= 6 and
            not entities.dn_number and
            not any(kw in question_lower for kw in cls.NON_DEALER_KEYWORDS)
        )
        
        if is_standalone_dealer and not entities.dealer:
            entities.dealer = question.strip()
            logger.debug(f"Detected standalone dealer: {entities.dealer}")
        
        # Extract dealer from patterns (if not already found)
        if not entities.dealer and not is_standalone_dealer:
            dealer_patterns = [
                r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|dashboard|details|risk|profile|analysis|executive|health|products|dns?|revenue|warehouse|city|manager)',
                r'analyze\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
                r'about\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
                r'profile\s+of\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
                r'executive\s+summary\s+for\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)'
            ]
            
            for pattern in dealer_patterns:
                match = re.search(pattern, question_lower)
                if match:
                    entities.dealer = match.group(1).strip()
                    break
            else:
                if entities.last_dealer and not entities.dealer:
                    entities.dealer = entities.last_dealer
        
        # Extract warehouse
        warehouse_match = re.search(r'warehouse\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|status)', question_lower)
        if warehouse_match:
            entities.warehouse = warehouse_match.group(1).strip()
        
        return entities


# ==========================================================
# INTENT DETECTION (Enhanced for Dealer Queries)
# ==========================================================

class IntentDetector:
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Tuple[Intent, QueryClass, float]:
        question_lower = question.lower().strip()
        
        # Direct matches
        if question_lower in ['help', 'menu', 'commands']:
            return Intent.HELP, QueryClass.OPERATIONAL, 1.0
        
        if question_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return Intent.GREETING, QueryClass.OPERATIONAL, 1.0
        
        # Root cause analysis
        analysis_keywords = ['why', 'root cause', 'reason', 'what caused', 'how to fix', 
                            'what should we do', 'can you help', 'what issue', 'any risk']
        if any(kw in question_lower for kw in analysis_keywords):
            return Intent.ROOT_CAUSE, QueryClass.AI, 0.85
        
        # DN number present
        if entities.dn_number:
            if 'timeline' in question_lower or 'history' in question_lower:
                return Intent.DN_TIMELINE, QueryClass.OPERATIONAL, 0.95
            return Intent.DN_LOOKUP, QueryClass.OPERATIONAL, 0.95
        
        # Pending items
        if 'pending pod' in question_lower or 'pod pending' in question_lower:
            return Intent.PENDING_POD, QueryClass.OPERATIONAL, 0.95
        if 'pending pgi' in question_lower or 'pgi pending' in question_lower:
            return Intent.PENDING_PGI, QueryClass.OPERATIONAL, 0.95
        if 'pending delivery' in question_lower or 'delivery pending' in question_lower:
            return Intent.PENDING_DELIVERIES, QueryClass.OPERATIONAL, 0.95
        
        # KPI and Dashboard
        if 'executive dashboard' in question_lower or 'ceo dashboard' in question_lower:
            return Intent.EXECUTIVE_DASHBOARD, QueryClass.OPERATIONAL, 0.95
        if 'network health' in question_lower:
            return Intent.NETWORK_HEALTH, QueryClass.OPERATIONAL, 0.95
        if 'critical delay' in question_lower:
            return Intent.CRITICAL_DELAYS, QueryClass.OPERATIONAL, 0.95
        if 'control tower' in question_lower:
            return Intent.CONTROL_TOWER, QueryClass.OPERATIONAL, 0.95
        
        # Top/Bottom dealers
        if 'top dealer' in question_lower or 'best dealer' in question_lower:
            return Intent.TOP_DEALERS, QueryClass.ANALYTICAL, 0.95
        if 'bottom dealer' in question_lower or 'worst dealer' in question_lower:
            return Intent.BOTTOM_DEALERS, QueryClass.ANALYTICAL, 0.95
        
        # Top warehouses
        if 'top warehouse' in question_lower or 'best warehouse' in question_lower:
            return Intent.TOP_WAREHOUSES, QueryClass.ANALYTICAL, 0.95
        
        # DEALER INTENTS (when dealer is present)
        if entities.dealer:
            # Comparison
            if entities.compare_mode or ('compare' in question_lower and 'vs' in question_lower):
                return Intent.DEALER_COMPARE, QueryClass.DEALER, 0.95
            
            # Executive summary (most comprehensive)
            if any(kw in question_lower for kw in ['executive', 'summary', 'analyze', 'full analysis', 'complete']):
                return Intent.DEALER_EXECUTIVE, QueryClass.DEALER, 0.95
            
            # Health score
            if any(kw in question_lower for kw in ['health', 'healthy', 'score', 'rating']):
                return Intent.DEALER_HEALTH, QueryClass.DEALER, 0.95
            
            # Risk
            if any(kw in question_lower for kw in ['risk', 'risky', 'problem', 'issue']):
                return Intent.DEALER_RISK, QueryClass.DEALER, 0.95
            
            # Products
            if any(kw in question_lower for kw in ['product', 'sell', 'sold', 'items']):
                return Intent.DEALER_PRODUCTS, QueryClass.DEALER, 0.95
            
            # Revenue
            if any(kw in question_lower for kw in ['revenue', 'sales', 'value', 'amount']):
                return Intent.DEALER_REVENUE, QueryClass.DEALER, 0.95
            
            # DN analysis
            if any(kw in question_lower for kw in ['dn', 'delivery note', 'deliveries', 'orders']):
                return Intent.DEALER_DN_ANALYSIS, QueryClass.DEALER, 0.95
            
            # Warehouse
            if any(kw in question_lower for kw in ['warehouse', 'served', 'distribution']):
                return Intent.DEALER_WAREHOUSE, QueryClass.DEALER, 0.95
            
            # City
            if any(kw in question_lower for kw in ['city', 'rank', 'market share']):
                return Intent.DEALER_CITY, QueryClass.DEALER, 0.95
            
            # Manager
            if any(kw in question_lower for kw in ['manager', 'sales person', 'who handles']):
                return Intent.DEALER_MANAGER, QueryClass.DEALER, 0.95
            
            # Performance (fallback)
            if 'performance' in question_lower or 'metrics' in question_lower:
                return Intent.DEALER_PERFORMANCE, QueryClass.DEALER, 0.85
            
            # Profile (default dealer intent)
            return Intent.DEALER_PROFILE, QueryClass.DEALER, 0.80
        
        # Warehouse performance
        if entities.warehouse and 'performance' in question_lower:
            return Intent.WAREHOUSE_PERFORMANCE, QueryClass.ANALYTICAL, 0.85
        
        # Default: AI query
        return Intent.AI_QUERY, QueryClass.AI, 0.50


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    @staticmethod
    def format_success(data: Any, summary: str = None, metadata: Dict = None) -> Dict:
        return {
            "success": True, 
            "data": data, 
            "summary": summary or "",
            "metadata": metadata or {}
        }
    
    @staticmethod
    def format_error(message: str, error_id: str = None, code: str = "unknown") -> Dict:
        error_id = error_id or str(uuid.uuid4())[:8]
        return {
            "success": False, 
            "data": {}, 
            "summary": message, 
            "error_code": code,
            "error_id": error_id
        }
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP* v44.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10-12 digit number

📋 *Pending Items*
• `Pending POD` - Missing proofs
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered

🏪 *Dealer Analytics*
• `Top dealers` - Rankings
• `Dealer ABC profile` - Dealer details
• `Analyze dealer ABC` - Executive summary
• `Dealer ABC products` - Product list
• `Dealer ABC revenue` - Sales analysis
• `Is dealer ABC healthy?` - Health score
• `Compare dealer A vs B` - Comparison

🏭 *Warehouse Analytics*
• `Top warehouses` - Rankings

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Control tower` - All alerts

💬 *Ask me anything!*
• "Why is Lahore delayed?"
• "What issues should I know about?"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_greeting() -> str:
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return f"""
{greeting}! 👋

I'm your *AI Logistics Assistant v44.0*. 

I can help you:
• Track any DN with 10-12 digit numbers
• Check pending PODs, PGIs, and deliveries
• Show dealer profiles, products, and revenue
• Analyze dealer health and risks
• Compare dealers side by side

Type `Help` to see all commands!
"""


# ==========================================================
# ROUTE MAP (Pure Router - No Business Logic)
# ==========================================================

class RouteMap:
    # All routes delegate to services - NO business logic here
    ROUTES = {
        # Logistics routes
        Intent.DN_LOOKUP: ("logistics", "get_complete_dn_intelligence", True),
        Intent.DN_TIMELINE: ("logistics", "get_dn_timeline", True),
        Intent.PENDING_POD: ("logistics", "get_pod_status", False),
        Intent.PENDING_PGI: ("logistics", "get_pending_pgi", False),
        Intent.PENDING_DELIVERIES: ("logistics", "get_pending_deliveries", False),
        
        # Dealer routes (delegates to AnalyticsService)
        Intent.TOP_DEALERS: ("analytics", "get_top_dealers", True),
        Intent.BOTTOM_DEALERS: ("analytics", "get_bottom_dealers", True),
        Intent.DEALER_PROFILE: ("analytics", "get_dealer_profile", True),
        Intent.DEALER_EXECUTIVE: ("analytics", "get_dealer_executive_summary", True),
        Intent.DEALER_PERFORMANCE: ("analytics", "get_dealer_performance", True),
        Intent.DEALER_PRODUCTS: ("analytics", "get_dealer_products", True),
        Intent.DEALER_DN_ANALYSIS: ("analytics", "get_dealer_dn_analysis", True),
        Intent.DEALER_REVENUE: ("analytics", "get_dealer_revenue_analysis", True),
        Intent.DEALER_HEALTH: ("analytics", "calculate_dealer_health_score", True),
        Intent.DEALER_RISK: ("analytics", "get_dealer_risk_analysis", True),
        Intent.DEALER_COMPARE: ("analytics", "compare_dealers", True),
        Intent.DEALER_WAREHOUSE: ("analytics", "get_dealer_warehouse_analysis", True),
        Intent.DEALER_CITY: ("analytics", "get_dealer_city_analysis", True),
        Intent.DEALER_MANAGER: ("analytics", "get_sales_manager_analysis", True),
        
        # Warehouse routes
        Intent.TOP_WAREHOUSES: ("analytics", "get_top_warehouses", True),
        Intent.WAREHOUSE_PERFORMANCE: ("analytics", "get_warehouse_performance", True),
        
        # KPI routes
        Intent.EXECUTIVE_DASHBOARD: ("kpi", "get_executive_dashboard", False),
        Intent.NETWORK_HEALTH: ("kpi", "get_network_health", False),
        Intent.CRITICAL_DELAYS: ("kpi", "get_critical_delays", False),
        Intent.CONTROL_TOWER: ("kpi", "get_control_tower_report", False),
    }
    
    @classmethod
    def get_route(cls, intent: Intent) -> Tuple[Optional[str], Optional[str], bool]:
        if intent in cls.ROUTES:
            return cls.ROUTES[intent]
        return None, None, False


# ==========================================================
# QUERY METRICS
# ==========================================================

class QueryMetrics:
    def __init__(self):
        self.metrics = {
            "total_queries": 0,
            "by_intent": {},
            "by_class": {},
            "avg_response_time_ms": 0,
            "success_rate": 100.0,
            "failures": 0,
            "ai_fallbacks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
    
    def record(self, intent: str, query_class: str, processing_time_ms: float, 
               success: bool, confidence: float = 0.5, cache_hit: bool = False, 
               ai_fallback: bool = False):
        self.metrics["total_queries"] += 1
        
        if intent not in self.metrics["by_intent"]:
            self.metrics["by_intent"][intent] = 0
        self.metrics["by_intent"][intent] += 1
        
        if ai_fallback:
            self.metrics["ai_fallbacks"] += 1
        
        if cache_hit:
            self.metrics["cache_hits"] += 1
        else:
            self.metrics["cache_misses"] += 1
        
        current_avg = self.metrics["avg_response_time_ms"]
        total = self.metrics["total_queries"]
        self.metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + processing_time_ms) / total
        
        if not success:
            self.metrics["failures"] += 1
        self.metrics["success_rate"] = ((self.metrics["total_queries"] - self.metrics["failures"]) / self.metrics["total_queries"]) * 100
    
    def get_metrics(self) -> Dict:
        cache_total = self.metrics["cache_hits"] + self.metrics["cache_misses"]
        return {
            **self.metrics,
            "cache_hit_rate": round(self.metrics["cache_hits"] / cache_total * 100, 1) if cache_total > 0 else 0,
        }


# ==========================================================
# REDIS CONTEXT MANAGER
# ==========================================================

class RedisContextManager:
    def __init__(self, redis_url: str = None):
        self.redis_client = None
        self.available = False
        if REDIS_AVAILABLE and redis_url:
            try:
                self.redis_client = redis.from_url(redis_url)
                self.redis_client.ping()
                self.available = True
                logger.info("✅ Redis context manager initialized")
            except Exception as e:
                logger.warning(f"Redis unavailable: {e}")
    
    def get_context(self, user_id: str) -> Dict:
        if not self.available or not self.redis_client:
            return {}
        try:
            import json
            data = self.redis_client.get(f"context:{user_id}")
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Redis get error: {e}")
        return {}
    
    def set_context(self, user_id: str, context: Dict, ttl_seconds: int = 3600):
        if not self.available or not self.redis_client:
            return
        try:
            import json
            self.redis_client.setex(f"context:{user_id}", ttl_seconds, json.dumps(context))
        except Exception as e:
            logger.error(f"Redis set error: {e}")


# ==========================================================
# MAIN AI QUERY SERVICE (v44.0 - PURE ROUTER)
# ==========================================================

class AIQueryService:
    _instance = None
    _initialized = False
    
    def __new__(cls, session_factory=None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, session_factory=None):
        if self._initialized:
            if session_factory:
                self._session_factory = session_factory
            return
        
        self._session_factory = session_factory
        self._ai_provider = None
        self.formatter = ResponseFormatter()
        self.metrics = QueryMetrics()
        self.cache = TTLCache(maxsize=200, ttl_seconds=300)
        self.redis_context = RedisContextManager()
        self.lru_context = LRUContextManager(max_users=500)
        
        self.service_health = {
            "logistics": False,
            "analytics": False,
            "kpi": False,
            "ai": False
        }
        
        self._initialized = True
        
        logger.info("=" * 70)
        logger.info("🧠 AI QUERY SERVICE v44.0 - PURE ROUTER")
        logger.info("   Principles: Detect Intent | Extract Entities | Route | Format")
        logger.info("   NO Business Logic - Delegates to Services")
        logger.info("=" * 70)
    
    def _get_session(self) -> Session:
        if self._session_factory:
            return self._session_factory()
        return None
    
    def _close_session(self, session: Session):
        if session:
            try:
                session.close()
            except Exception as e:
                logger.exception(f"Error closing session: {e}")
    
    def _get_logistics_service(self, session: Session):
        try:
            from app.services.logistics_query_service import LogisticsQueryService
            service = LogisticsQueryService(session)
            self.service_health["logistics"] = True
            return service
        except Exception as e:
            logger.debug(f"Logistics service unavailable: {e}")
            self.service_health["logistics"] = False
            return None
    
    def _get_analytics_service(self, session: Session):
        try:
            from app.services.analytics_service import AnalyticsService
            service = AnalyticsService(session)
            self.service_health["analytics"] = True
            return service
        except Exception as e:
            logger.debug(f"Analytics service unavailable: {e}")
            self.service_health["analytics"] = False
            return None
    
    def _get_kpi_service(self, session: Session):
        try:
            from app.services.kpi_service import KPIService
            service = KPIService(session)
            self.service_health["kpi"] = True
            return service
        except Exception as e:
            logger.debug(f"KPI service unavailable: {e}")
            self.service_health["kpi"] = False
            return None
    
    @property
    def ai_provider(self):
        if self._ai_provider is None:
            try:
                from app.services.ai_provider_service import get_ai_provider
                self._ai_provider = get_ai_provider()
                self.service_health["ai"] = True
                logger.debug("AI Provider loaded (lazy)")
            except Exception as e:
                logger.debug(f"AI Provider unavailable: {e}")
                self.service_health["ai"] = False
        return self._ai_provider
    
    def _handle_ai_fallback(self, question: str, request_id: str) -> str:
        """AI fallback when no specific route matches"""
        if not self.ai_provider:
            return self._get_fallback_response(question)
        
        try:
            result = self.ai_provider.chat(question, "guest", request_id=request_id)
            if not result or len(result.strip()) == 0:
                return self._get_fallback_response(question)
            return result
        except Exception as e:
            logger.bind(request_id=request_id).exception(f"AI fallback failed: {e}")
            return self._get_fallback_response(question)
    
    def _get_fallback_response(self, question: str) -> str:
        """Fallback when everything fails"""
        if 'help' in question.lower():
            return ResponseFormatter.format_help()
        
        if any(word in question.lower() for word in ['hi', 'hello', 'hey', 'good']):
            return ResponseFormatter.format_greeting()
        
        return f"""I understand you're asking about: "{question[:80]}"

📋 *Try these commands:*
• Send any 10-12 digit number to track a DN
• `Pending POD` - Missing proofs
• `Top dealers` - Dealer rankings
• `Dealer [name] profile` - Dealer details
• `Analyze dealer [name]` - Executive summary

Type `Help` for complete list!"""
    
    def _execute_route(self, intent: Intent, entities: ExtractedEntities, 
                       session: Session, request_id: str) -> Optional[Dict]:
        """Execute route - PURE routing, no business logic"""
        service_name, method, has_param = RouteMap.get_route(intent)
        
        if not service_name:
            return None
        
        route_start = time.time()
        
        try:
            if service_name == "logistics":
                service = self._get_logistics_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                if has_param:
                    param = entities.dn_number
                    result = handler(param) if param else handler()
                else:
                    result = handler()
                
            elif service_name == "analytics":
                service = self._get_analytics_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                # Build parameters based on intent
                if has_param:
                    if intent == Intent.DEALER_COMPARE and entities.dealer2:
                        result = handler(entities.dealer, entities.dealer2)
                    elif intent == Intent.TOP_DEALERS or intent == Intent.BOTTOM_DEALERS:
                        result = handler(entities.limit or 10)
                    elif intent in [Intent.DEALER_PROFILE, Intent.DEALER_EXECUTIVE, Intent.DEALER_PERFORMANCE,
                                   Intent.DEALER_PRODUCTS, Intent.DEALER_DN_ANALYSIS, Intent.DEALER_REVENUE,
                                   Intent.DEALER_HEALTH, Intent.DEALER_RISK, Intent.DEALER_WAREHOUSE,
                                   Intent.DEALER_CITY, Intent.DEALER_MANAGER]:
                        result = handler(entities.dealer) if entities.dealer else None
                    elif intent == Intent.WAREHOUSE_PERFORMANCE and entities.warehouse:
                        result = handler(entities.warehouse)
                    else:
                        result = handler(entities.limit or 10)
                else:
                    result = handler()
                
                if not result:
                    return None
                    
            elif service_name == "kpi":
                service = self._get_kpi_service(session)
                if not service:
                    return None
                
                handler = getattr(service, method, None)
                if not handler or not callable(handler):
                    return None
                
                result = handler()
                
            else:
                return None
            
            route_time = round((time.time() - route_start) * 1000, 2)
            logger.bind(request_id=request_id, intent=intent.value).info(f"Route executed in {route_time}ms")
            
            if isinstance(result, dict):
                if result.get("error"):
                    return None
                summary = result.get("_summary", "")
                return self.formatter.format_success(result, summary)
            
            return self.formatter.format_success(result, "")
            
        except Exception as e:
            logger.bind(request_id=request_id).debug(f"Route {method} failed: {e}")
            return None
    
    def _get_context(self, user_id: str) -> Dict:
        if self.redis_context.available:
            context = self.redis_context.get_context(user_id)
            if context:
                return context
        return self.lru_context.get(user_id)
    
    def _update_context(self, user_id: str, intent: Intent, entities: ExtractedEntities, confidence: float):
        context = {
            "last_intent": intent.value,
            "last_intent_confidence": confidence,
            "last_query_time": datetime.now().isoformat(),
        }
        
        if entities.dn_number:
            context["last_dn"] = entities.dn_number
        if entities.dealer:
            context["last_dealer"] = entities.dealer
        if entities.city:
            context["last_city"] = entities.city
        
        if self.redis_context.available:
            self.redis_context.set_context(user_id, context)
        else:
            self.lru_context.set(user_id, context)
    
    def process_query(self, question: str, user_phone: str = None, request_id: str = None) -> Dict:
        start_time = time.time()
        
        if not request_id:
            request_id = str(uuid.uuid4())[:8]
        
        logger.bind(request_id=request_id, phone=user_phone).info(f"Processing: {question[:100]}")
        
        session = None
        
        try:
            session = self._get_session()
            context = self._get_context(user_phone) if user_phone else {}
            memory = self.lru_context.get_memory(user_phone or request_id)
            
            entities = EntityExtractor.extract(question, context, memory)
            logger.bind(request_id=request_id).debug(f"Entities: {entities.to_dict()}")
            
            intent, query_class, confidence = IntentDetector.detect(question, entities)
            
            logger.bind(
                request_id=request_id,
                intent=intent.value,
                query_class=query_class.value,
                confidence=confidence
            ).info("Intent detected")
            
            # Handle HELP and GREETING directly
            if intent == Intent.HELP:
                result = self.formatter.format_success({}, self.formatter.format_help())
            elif intent == Intent.GREETING:
                result = self.formatter.format_success({}, self.formatter.format_greeting())
            else:
                # Try specific route
                result = self._execute_route(intent, entities, session, request_id)
                
                # If route failed, try AI fallback
                if not result or not result.get("success"):
                    logger.bind(request_id=request_id).info(f"Route failed, using AI fallback")
                    ai_response = self._handle_ai_fallback(question, request_id)
                    result = self.formatter.format_success({}, ai_response)
                    self.metrics.record(intent.value, query_class.value, 
                                       (time.time() - start_time) * 1000, 
                                       True, confidence, False, True)
            
            whatsapp_message = result.get("summary", "") if result.get("success") else result.get("summary", "Unable to process")
            error_id = result.get("error_id")
            
            if user_phone:
                self._update_context(user_phone, intent, entities, confidence)
                self.lru_context.update_memory(user_phone, question, whatsapp_message, intent.value, entities)
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            self.metrics.record(intent.value, query_class.value, elapsed_ms, 
                               result.get("success", True), confidence, False,
                               intent in [Intent.AI_QUERY, Intent.ROOT_CAUSE, Intent.GENERAL])
            
            # Auto cache cleanup every 500 queries
            if self.metrics.metrics["total_queries"] % 500 == 0:
                logger.bind(request_id=request_id).info("Auto cache cleanup triggered")
                self.cache.clear()
            
            logger.bind(request_id=request_id).info(f"Response generated in {elapsed_ms:.0f}ms")
            
            return {
                "success": result.get("success", True),
                "response": whatsapp_message,
                "intent": intent.value,
                "intent_confidence": confidence,
                "query_class": query_class.value,
                "entities": entities.to_dict(),
                "processing_time_ms": round(elapsed_ms, 2),
                "request_id": request_id,
                "cache_hit": False,
                "error_id": error_id
            }
        
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            logger.bind(
                request_id=request_id,
                error_id=error_id,
                error=str(e)
            ).exception(f"Query processing error")
            
            return {
                "success": False,
                "response": self._get_fallback_response(question),
                "intent": "error",
                "query_class": "error",
                "entities": {},
                "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                "request_id": request_id,
                "cache_hit": False,
                "error_id": error_id
            }
        
        finally:
            if session:
                self._close_session(session)
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_query_service",
            "version": "44.0",
            "architecture": "pure_router",
            "status": "healthy",
            "metrics": self.metrics.get_metrics(),
            "cache": self.cache.get_stats(),
            "context_stats": self.lru_context.get_stats(),
            "redis_available": self.redis_context.available,
            "service_health": self.service_health,
        }
    
    def get_metrics(self) -> Dict:
        return self.metrics.get_metrics()
    
    def clear_cache(self):
        self.cache.clear()
        logger.info("Cache cleared")


# ==========================================================
# FACTORY FUNCTIONS
# ==========================================================

_SERVICE_INSTANCE = None

def get_ai_query_service(session_factory=None) -> AIQueryService:
    global _SERVICE_INSTANCE
    if _SERVICE_INSTANCE is None:
        _SERVICE_INSTANCE = AIQueryService(session_factory)
    elif session_factory and _SERVICE_INSTANCE._session_factory is None:
        _SERVICE_INSTANCE._session_factory = session_factory
    return _SERVICE_INSTANCE


def process_whatsapp_query(question: str, session_factory, phone_number: str = None, 
                           user_id: str = None, request_id: str = None) -> str:
    try:
        service = get_ai_query_service(session_factory)
        result = service.process_query(question, phone_number or user_id, request_id)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error")
        return "⚠️ Service temporarily unavailable. Please try again later."


def health_check(session_factory=None) -> Dict:
    try:
        service = get_ai_query_service(session_factory)
        return service.health_check()
    except Exception as e:
        return {"service": "ai_query_service", "status": "unhealthy", "error": str(e), "version": "44.0"}


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🧠 AI QUERY SERVICE v44.0 - PURE ROUTER")
logger.info("")
logger.info("   WHAT THIS FILE DOES:")
logger.info("   ✅ Intent Detection - Classifies user questions")
logger.info("   ✅ Entity Extraction - Finds DNs, dealers, cities")
logger.info("   ✅ Route Mapping - Directs to appropriate service")
logger.info("   ✅ Response Formatting - Prepares WhatsApp response")
logger.info("")
logger.info("   WHAT THIS FILE DOES NOT DO:")
logger.info("   ❌ NO Dealer Profile Logic - Delegates to AnalyticsService")
logger.info("   ❌ NO DN Intelligence - Delegates to LogisticsService")
logger.info("   ❌ NO KPI Calculations - Delegates to KPIService")
logger.info("")
logger.info("   FIXES IN v44.0:")
logger.info("   ✅ DN Pattern: r'\\b\\d{{10,12}}\\b' (Fixes 6243611870)")
logger.info("   ✅ Standalone Dealer Detection (Short questions)")
logger.info("   ✅ Pure Routing - No business logic in this file")
logger.info("=" * 70)
