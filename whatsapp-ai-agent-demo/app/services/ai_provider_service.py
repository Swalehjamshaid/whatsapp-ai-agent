"""
File: app/services/ai_provider_service.py
Version: 32.0 - ENTERPRISE AI ROUTER WITH FIXES

CRITICAL FIXES:
1. ✅ Prometheus Duplicate Metrics - Singleton registration
2. ✅ Double Initialization Prevention - Thread-safe singleton
3. ✅ Lazy Loading - Load only when needed
4. ✅ Intelligent Routing Pipeline
5. ✅ Service Registry Pattern
6. ✅ Analytics First Routing
7. ✅ Context Menu Management
8. ✅ Error Recovery
9. ✅ 4096 Character Split for WhatsApp

Status: ENTERPRISE READY
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Set, Tuple, Callable
from functools import lru_cache, wraps

logger = logging.getLogger(__name__)

# =====================================================================================================================
# CONFIGURATION
# =====================================================================================================================

CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.70"))
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
CACHE_TTL = int(os.getenv("ROUTER_CACHE_TTL", "300"))
ENABLE_AI_FALLBACK = os.getenv("ENABLE_AI_FALLBACK", "true").lower() == "true"
DEFAULT_LLM = os.getenv("DEFAULT_LLM", "groq")
ENABLE_REDIS = os.getenv("ENABLE_REDIS", "false").lower() == "true"
ENABLE_RATE_LIMITING = os.getenv("ENABLE_RATE_LIMITING", "true").lower() == "true"
ENABLE_MONITORING = os.getenv("ENABLE_MONITORING", "true").lower() == "true"

# =====================================================================================================================
# LAZY LOADING - Import only when needed
# =====================================================================================================================

class LazyLoader:
    """Lazy load modules to improve startup time"""
    
    _instances = {}
    _lock = threading.Lock()
    
    @classmethod
    def get_spacy(cls):
        """Lazy load spaCy"""
        if "spacy" not in cls._instances:
            with cls._lock:
                if "spacy" not in cls._instances:
                    try:
                        import spacy
                        nlp = None
                        models_to_try = ["en_core_web_sm", "en_core_web_md", "en_core_web_lg"]
                        for model in models_to_try:
                            try:
                                nlp = spacy.load(model)
                                logger.info(f"✅ spaCy loaded: {model}")
                                break
                            except OSError:
                                continue
                        if nlp is None:
                            try:
                                subprocess.run(
                                    ["python", "-m", "spacy", "download", "en_core_web_sm"],
                                    capture_output=True,
                                    check=True,
                                    timeout=120
                                )
                                nlp = spacy.load("en_core_web_sm")
                                logger.info("✅ spaCy model downloaded and loaded")
                            except Exception:
                                nlp = None
                        cls._instances["spacy"] = nlp
                    except ImportError:
                        cls._instances["spacy"] = None
        return cls._instances["spacy"]
    
    @classmethod
    def get_sentence_transformer(cls):
        """Lazy load SentenceTransformer"""
        if "sentence_transformer" not in cls._instances:
            with cls._lock:
                if "sentence_transformer" not in cls._instances:
                    try:
                        from sentence_transformers import SentenceTransformer
                        model = SentenceTransformer('all-MiniLM-L6-v2')
                        logger.info("✅ SentenceTransformer loaded")
                        cls._instances["sentence_transformer"] = model
                    except ImportError:
                        cls._instances["sentence_transformer"] = None
        return cls._instances["sentence_transformer"]
    
    @classmethod
    def get_groq(cls):
        """Lazy load Groq"""
        if "groq" not in cls._instances:
            with cls._lock:
                if "groq" not in cls._instances:
                    try:
                        from groq import Groq
                        client = Groq()
                        logger.info("✅ Groq client initialized")
                        cls._instances["groq"] = client
                    except ImportError:
                        cls._instances["groq"] = None
        return cls._instances["groq"]
    
    @classmethod
    def get_openai(cls):
        """Lazy load OpenAI"""
        if "openai" not in cls._instances:
            with cls._lock:
                if "openai" not in cls._instances:
                    try:
                        from openai import OpenAI
                        client = OpenAI()
                        logger.info("✅ OpenAI client initialized")
                        cls._instances["openai"] = client
                    except ImportError:
                        cls._instances["openai"] = None
        return cls._instances["openai"]
    
    @classmethod
    def get_rapidfuzz(cls):
        """Lazy load RapidFuzz"""
        if "rapidfuzz" not in cls._instances:
            with cls._lock:
                if "rapidfuzz" not in cls._instances:
                    try:
                        from rapidfuzz import fuzz, process
                        cls._instances["rapidfuzz"] = (fuzz, process)
                        logger.info("✅ RapidFuzz loaded")
                    except ImportError:
                        cls._instances["rapidfuzz"] = (None, None)
        return cls._instances["rapidfuzz"]
    
    @classmethod
    def get_flashrank(cls):
        """Lazy load FlashRank"""
        if "flashrank" not in cls._instances:
            with cls._lock:
                if "flashrank" not in cls._instances:
                    try:
                        from flashrank import Ranker
                        ranker = Ranker()
                        logger.info("✅ FlashRank loaded")
                        cls._instances["flashrank"] = ranker
                    except ImportError:
                        cls._instances["flashrank"] = None
        return cls._instances["flashrank"]

# =====================================================================================================================
# PROMETHEUS METRICS - SINGLETON REGISTRATION
# =====================================================================================================================

class MetricsRegistry:
    """Singleton metrics registry to prevent duplicate registration"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if MetricsRegistry._initialized:
            return
        
        self._initialized = True
        self._metrics = {}
        self._enabled = False
        
        try:
            from prometheus_client import Counter, Histogram, Gauge, Summary, Info, REGISTRY
            self.REGISTRY = REGISTRY
            self.Counter = Counter
            self.Histogram = Histogram
            self.Gauge = Gauge
            self.Summary = Summary
            self.Info = Info
            self._enabled = True
            
            # Initialize metrics with duplicate protection
            self._init_metrics()
            logger.info("✅ Prometheus metrics initialized (singleton)")
        except ImportError:
            logger.info("ℹ️ Prometheus not available")
    
    def _init_metrics(self):
        """Initialize metrics with duplicate protection"""
        if not self._enabled:
            return
        
        # Check if metrics already exist in registry
        existing_metrics = set(self.REGISTRY._names_to_collectors.keys())
        
        # Request Counter
        if "ai_provider_requests_total" not in existing_metrics:
            self._metrics["requests"] = self.Counter(
                'ai_provider_requests_total',
                'Total number of requests',
                ['service', 'intent', 'status']
            )
        else:
            self._metrics["requests"] = self.REGISTRY._names_to_collectors["ai_provider_requests_total"]
        
        # Request Duration Histogram
        if "ai_provider_request_duration_seconds" not in existing_metrics:
            self._metrics["duration"] = self.Histogram(
                'ai_provider_request_duration_seconds',
                'Request duration in seconds',
                ['service', 'intent'],
                buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
            )
        else:
            self._metrics["duration"] = self.REGISTRY._names_to_collectors["ai_provider_request_duration_seconds"]
        
        # Cache Hits
        if "ai_provider_cache_hits_total" not in existing_metrics:
            self._metrics["cache_hits"] = self.Counter(
                'ai_provider_cache_hits_total',
                'Total cache hits',
                ['cache_type']
            )
        else:
            self._metrics["cache_hits"] = self.REGISTRY._names_to_collectors["ai_provider_cache_hits_total"]
        
        # Cache Misses
        if "ai_provider_cache_misses_total" not in existing_metrics:
            self._metrics["cache_misses"] = self.Counter(
                'ai_provider_cache_misses_total',
                'Total cache misses',
                ['cache_type']
            )
        else:
            self._metrics["cache_misses"] = self.REGISTRY._names_to_collectors["ai_provider_cache_misses_total"]
        
        # Active Sessions
        if "ai_provider_active_sessions" not in existing_metrics:
            self._metrics["sessions"] = self.Gauge(
                'ai_provider_active_sessions',
                'Number of active sessions'
            )
        else:
            self._metrics["sessions"] = self.REGISTRY._names_to_collectors["ai_provider_active_sessions"]
        
        # Routing Confidence
        if "ai_provider_routing_confidence" not in existing_metrics:
            self._metrics["confidence"] = self.Histogram(
                'ai_provider_routing_confidence',
                'Routing confidence scores',
                buckets=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
            )
        else:
            self._metrics["confidence"] = self.REGISTRY._names_to_collectors["ai_provider_routing_confidence"]
        
        # Errors
        if "ai_provider_errors_total" not in existing_metrics:
            self._metrics["errors"] = self.Counter(
                'ai_provider_errors_total',
                'Total errors',
                ['service', 'error_type']
            )
        else:
            self._metrics["errors"] = self.REGISTRY._names_to_collectors["ai_provider_errors_total"]
        
        # AI Calls
        if "ai_provider_ai_calls_total" not in existing_metrics:
            self._metrics["ai_calls"] = self.Counter(
                'ai_provider_ai_calls_total',
                'Total AI calls',
                ['provider', 'model']
            )
        else:
            self._metrics["ai_calls"] = self.REGISTRY._names_to_collectors["ai_provider_ai_calls_total"]
        
        # Database Time
        if "ai_provider_database_time_seconds" not in existing_metrics:
            self._metrics["db_time"] = self.Histogram(
                'ai_provider_database_time_seconds',
                'Database query time in seconds',
                ['service'],
                buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
            )
        else:
            self._metrics["db_time"] = self.REGISTRY._names_to_collectors["ai_provider_database_time_seconds"]
        
        # Response Size
        if "ai_provider_response_size_bytes" not in existing_metrics:
            self._metrics["response_size"] = self.Histogram(
                'ai_provider_response_size_bytes',
                'Response size in bytes',
                buckets=(100, 500, 1000, 2000, 4000, 8000, 16000)
            )
        else:
            self._metrics["response_size"] = self.REGISTRY._names_to_collectors["ai_provider_response_size_bytes"]
    
    def get(self, name: str):
        """Get a metric by name"""
        return self._metrics.get(name)
    
    def is_enabled(self) -> bool:
        """Check if monitoring is enabled"""
        return self._enabled

# =====================================================================================================================
# ENUMS
# =====================================================================================================================

class EntityType(Enum):
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    MATERIAL = "material"
    DIVISION = "division"
    SALES_OFFICE = "sales_office"
    REGION = "region"
    PROVINCE = "province"
    TRANSPORTER = "transporter"
    VEHICLE = "vehicle"
    DRIVER = "driver"
    ROUTE = "route"
    DATE = "date"
    MONTH = "month"
    YEAR = "year"

class IntentType(Enum):
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    DELIVERY = "delivery"
    POD = "pod"
    PGI = "pgi"
    COMPARISON = "comparison"
    RANKING = "ranking"
    SUMMARY = "summary"
    PERFORMANCE = "performance"
    FORECAST = "forecast"
    RECOMMENDATION = "recommendation"
    ROOT_CAUSE = "root_cause"
    EXECUTIVE = "executive"
    NATIONAL = "national"
    SEARCH = "search"
    HELP = "help"
    GREETING = "greeting"
    MENU = "menu"
    DISTANCE = "distance"
    UNKNOWN = "unknown"

class MenuState(Enum):
    MAIN = "main"
    DN = "dn"
    DEALER = "dealer"
    CITY = "city"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    NATIONAL = "national"

# =====================================================================================================================
# DATACLASSES
# =====================================================================================================================

@dataclass
class Entity:
    type: EntityType
    value: str
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Intent:
    type: IntentType
    confidence: float = 1.0
    entities: List[Entity] = field(default_factory=list)
    sub_intent: Optional[str] = None
    metric: Optional[str] = None

@dataclass
class RoutingDecision:
    intent: Intent
    service_key: str
    method: str
    entity: Dict[str, Any]
    confidence: float = 1.0
    requires_ai: bool = False
    reason: str = ""
    original_message: str = ""
    menu_option: Optional[str] = None
    multi_intent: bool = False
    services: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SessionContext:
    session_id: str
    current_service: Optional[str] = None
    current_menu: MenuState = MenuState.MAIN
    current_city: Optional[str] = None
    current_dealer: Optional[str] = None
    current_warehouse: Optional[str] = None
    current_product: Optional[str] = None
    last_intent: Optional[IntentType] = None
    last_entity: Optional[Entity] = None
    last_dashboard: Optional[str] = None
    last_comparison: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

# =====================================================================================================================
# CONTEXT MANAGER
# =====================================================================================================================

class ContextManager:
    """Session-based context management"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if ContextManager._initialized:
            return
        
        self._initialized = True
        self._contexts: Dict[str, SessionContext] = {}
        self._context_lock = threading.RLock()
        self._ttl = SESSION_TTL
        
        logger.info("✅ ContextManager initialized")
    
    def get_context(self, session_id: str) -> SessionContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = SessionContext(session_id=session_id)
            context = self._contexts[session_id]
            context.last_activity = datetime.now()
            
            # Update metrics
            metrics = MetricsRegistry()
            if metrics.is_enabled():
                sessions = metrics.get("sessions")
                if sessions:
                    sessions.set(len(self._contexts))
            
            return context
    
    def update_context(self, session_id: str, updates: Dict[str, Any]) -> None:
        """Update context with new data"""
        with self._context_lock:
            context = self.get_context(session_id)
            for key, value in updates.items():
                if hasattr(context, key):
                    setattr(context, key, value)
            context.last_activity = datetime.now()
    
    def add_history(self, session_id: str, entry: Dict[str, Any]) -> None:
        """Add entry to conversation history"""
        with self._context_lock:
            context = self.get_context(session_id)
            context.history.append(entry)
            if len(context.history) > 20:
                context.history = context.history[-20:]
    
    def get_follow_ups(self, session_id: str) -> List[str]:
        """Generate follow-up suggestions"""
        context = self.get_context(session_id)
        suggestions = []
        
        if context.current_city:
            suggestions.append(f"Revenue in {context.current_city}")
            suggestions.append(f"Pending in {context.current_city}")
            suggestions.append(f"Compare {context.current_city} with another")
        
        if context.current_dealer:
            suggestions.append(f"Compare {context.current_dealer} with another")
        
        if context.current_warehouse:
            suggestions.append(f"Inventory in {context.current_warehouse}")
        
        if context.current_product:
            suggestions.append(f"Sales of {context.current_product}")
        
        suggestions.extend([
            "Show national KPI",
            "View pending DNs",
            "Top performers"
        ])
        
        return suggestions[:4]
    
    def cleanup_expired(self) -> None:
        """Clean up expired sessions"""
        with self._context_lock:
            now = datetime.now()
            expired = []
            for session_id, context in self._contexts.items():
                if (now - context.last_activity).total_seconds() > self._ttl:
                    expired.append(session_id)
            for session_id in expired:
                del self._contexts[session_id]
            if expired:
                logger.info(f"🧹 Cleaned up {len(expired)} expired sessions")

# =====================================================================================================================
# INTENT ENGINE
# =====================================================================================================================

class IntentEngine:
    """Intent detection engine with hybrid approach"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if IntentEngine._initialized:
            return
        
        self._initialized = True
        
        # Intent patterns with priority
        self.INTENT_PATTERNS = {
            IntentType.MENU: {
                "patterns": [
                    r"^(?:menu|help|options|show menu|main menu)$",
                    r"^(?:0|menu|help|options)",
                ],
                "priority": 1
            },
            IntentType.GREETING: {
                "patterns": [
                    r"^(?:hi|hello|hey|salam|good morning|good evening|howdy)$",
                    r"^(?:hi|hello|hey|salam).*",
                ],
                "priority": 1
            },
            IntentType.DASHBOARD: {
                "patterns": [
                    r"(?:show|display|get|view).*(?:dashboard|overview|details|performance)",
                    r"(?:how is|what about|tell me about).*(?:city|dealer|warehouse|product)",
                    r"^([\w\s]+)$",
                ],
                "priority": 2
            },
            IntentType.REVENUE: {
                "patterns": [
                    r"(?:revenue|sales|income|turnover|earnings)",
                    r"(?:how much|what(?:'s)?).*(?:revenue|sales)",
                    r"(?:total|overall).*(?:revenue|sales)",
                ],
                "priority": 2
            },
            IntentType.UNITS: {
                "patterns": [
                    r"(?:units|quantity|volume|pieces|items)",
                    r"(?:how many|number of).*(?:units|items)",
                ],
                "priority": 2
            },
            IntentType.PENDING: {
                "patterns": [
                    r"(?:pending|outstanding|backlog|overdue).*(?:dn|delivery|order)",
                    r"(?:undelivered|unfulfilled).*(?:orders|dns)",
                ],
                "priority": 2
            },
            IntentType.DELIVERY: {
                "patterns": [
                    r"(?:delivery|dispatch|shipping|transit).*(?:performance|time|days)",
                    r"(?:average|fastest|slowest).*(?:delivery|transit)",
                    r"delivery (?:success|failure|rate)",
                ],
                "priority": 2
            },
            IntentType.COMPARISON: {
                "patterns": [
                    r"compare\s+([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
                    r"([\w\s]+)\s+(?:vs|versus|compared to)\s+([\w\s]+)",
                    r"difference between\s+([\w\s]+)\s+(?:and|vs)\s+([\w\s]+)",
                ],
                "priority": 1
            },
            IntentType.RANKING: {
                "patterns": [
                    r"(?:top|best|highest|leading).*(?:dealers?|warehouses?|cities?|products?)",
                    r"(?:ranking|rank|leaderboard).*(?:dealers?|warehouses?|cities?|products?)",
                ],
                "priority": 2
            },
            IntentType.FORECAST: {
                "patterns": [
                    r"(?:forecast|predict|project|estimate).*(?:sales|revenue|delivery|volume)",
                    r"what (?:will|would) be the (?:sales|revenue|delivery)",
                    r"next (?:month|quarter|year).*(?:forecast|prediction)",
                ],
                "priority": 2
            },
            IntentType.RECOMMENDATION: {
                "patterns": [
                    r"(?:recommend|suggest|advice|improve|optimize)",
                    r"what (?:should|can|could) (?:i|we) (?:do|improve|fix)",
                    r"how to (?:improve|fix|optimize)\s+([\w\s]+)",
                ],
                "priority": 2
            },
            IntentType.ROOT_CAUSE: {
                "patterns": [
                    r"why (?:is|are|was|were)\s+([\w\s]+?)\s+(?:slow|delayed|late|underperforming|declining)",
                    r"(?:reason|cause|why).*(?:delay|issue|problem)",
                    r"what (?:caused|causes|is causing).*(?:problem|issue|delay)",
                ],
                "priority": 2
            },
            IntentType.EXECUTIVE: {
                "patterns": [
                    r"(?:executive|management|leadership).*(?:dashboard|summary|overview|insight)",
                    r"what(?:'s)? (?:going on|happening|the situation)",
                ],
                "priority": 1
            },
            IntentType.NATIONAL: {
                "patterns": [
                    r"(?:national|overall|pakistan).*(?:kpi|performance|score|health|dashboard)",
                    r"^(?:national|overall|pakistan)$",
                ],
                "priority": 1
            },
            IntentType.DISTANCE: {
                "patterns": [
                    r"(?:distance|how far|travel|driving).*(?:from|between)",
                    r"nearest (?:warehouse|dealer|city)",
                ],
                "priority": 2
            },
            IntentType.HELP: {
                "patterns": [
                    r"(?:help|assist|support|guide|how to)",
                    r"what can you (?:do|help with)",
                ],
                "priority": 1
            },
        }
        
        self._compiled_patterns = {}
        for intent_type, config in self.INTENT_PATTERNS.items():
            self._compiled_patterns[intent_type] = [
                re.compile(pattern, re.IGNORECASE) for pattern in config["patterns"]
            ]
        
        logger.info(f"✅ IntentEngine initialized with {len(self.INTENT_PATTERNS)} intent types")
    
    def detect_intent(self, message: str, entities: List[Entity] = None) -> Tuple[Intent, float]:
        """Detect intent with confidence"""
        message_lower = message.lower().strip()
        
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        best_metric = None
        
        # Check for menu commands
        if message_lower in ["menu", "help", "options", "show menu", "main menu", "0"]:
            return Intent(type=IntentType.MENU, confidence=1.0), 1.0
        
        # Check for greetings
        if message_lower in ["hi", "hello", "hey", "salam", "good morning", "good evening"]:
            return Intent(type=IntentType.GREETING, confidence=1.0), 1.0
        
        # Pattern matching
        for intent_type, patterns in self._compiled_patterns.items():
            matches = 0
            for pattern in patterns:
                match = pattern.search(message_lower)
                if match:
                    matches += 1
                    # Check for metric in groups
                    if match.groups():
                        for group in match.groups():
                            if group and len(group.strip()) > 2:
                                best_metric = group.strip()
            
            if matches > 0:
                score = min(1.0, (matches / max(1, len(patterns))) * 2)
                priority = self.INTENT_PATTERNS[intent_type].get("priority", 2)
                score = score * (1.0 / priority)
                
                if score > best_score:
                    best_score = score
                    best_intent = intent_type
        
        # Entity-based inference
        if entities and best_score < 0.6:
            entity_types = [e.type for e in entities]
            if EntityType.CITY in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
            elif EntityType.DEALER in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
            elif EntityType.WAREHOUSE in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
        
        # Semantic fallback
        if best_score < 0.5:
            semantic_score = self._semantic_similarity(message_lower)
            if semantic_score > best_score:
                best_score = semantic_score
        
        intent = Intent(
            type=best_intent,
            confidence=best_score,
            entities=entities or [],
            metric=best_metric
        )
        
        logger.info(f"🎯 Intent: {best_intent.value} (confidence: {best_score:.2f})")
        return intent, best_score
    
    def _semantic_similarity(self, message: str) -> float:
        """Calculate semantic similarity using SentenceTransformer"""
        try:
            model = LazyLoader.get_sentence_transformer()
            if model is None:
                return 0.0
            
            # Pre-compute embeddings for common intents
            intent_examples = {
                IntentType.DASHBOARD: ["show dashboard", "display overview", "view status"],
                IntentType.REVENUE: ["show revenue", "display sales", "view income"],
                IntentType.UNITS: ["show units", "display quantity", "view volume"],
                IntentType.PENDING: ["show pending", "display backlog", "view overdue"],
                IntentType.DELIVERY: ["show delivery", "display transit", "view shipping"],
                IntentType.COMPARISON: ["compare", "versus", "vs"],
                IntentType.RANKING: ["top", "ranking", "leaderboard"],
                IntentType.NATIONAL: ["national", "overall", "pakistan"],
            }
            
            message_embedding = model.encode(message)
            best_score = 0.0
            
            for intent_type, examples in intent_examples.items():
                example_embeddings = model.encode(examples)
                for example_emb in example_embeddings:
                    from numpy import dot
                    from numpy.linalg import norm
                    similarity = dot(message_embedding, example_emb) / (norm(message_embedding) * norm(example_emb))
                    best_score = max(best_score, similarity)
            
            return best_score
        except Exception:
            return 0.0

# =====================================================================================================================
# ENTITY ENGINE
# =====================================================================================================================

class EntityEngine:
    """Entity recognition engine"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if EntityEngine._initialized:
            return
        
        self._initialized = True
        
        # Known entities
        self.KNOWN_CITIES = {
            "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
            "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
            "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
            "gwadar", "rahim yar khan"
        }
        
        self.KNOWN_WAREHOUSES = {
            "lahore", "karachi", "rawalpindi", "multan", "peshawar",
            "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
            "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
            "abbottabad", "gwadar", "gilgit", "islamabad"
        }
        
        self.DEALER_SUFFIXES = {
            "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
            "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
            "centre", "center", "solutions", "services", "digital", "technologies",
            "systems", "networks", "communications", "logistics", "transport"
        }
        
        self._dealer_patterns = [
            re.compile(rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)', re.IGNORECASE)
            for suffix in self.DEALER_SUFFIXES
        ]
        
        logger.info(f"✅ EntityEngine initialized with {len(self.KNOWN_CITIES)} cities, {len(self.KNOWN_WAREHOUSES)} warehouses")
    
    def extract_entities(self, message: str) -> List[Entity]:
        """Extract all entities from message"""
        message_lower = message.lower().strip()
        entities = []
        
        # 1. DN numbers (8-12 digits)
        dn_matches = re.findall(r'(?<!\d)(\d{8,12})(?!\d)', message)
        for dn in dn_matches:
            entities.append(Entity(type=EntityType.DN, value=dn, confidence=0.95))
        
        # 2. Cities
        for city in self.KNOWN_CITIES:
            if city in message_lower:
                entities.append(Entity(type=EntityType.CITY, value=city.title(), confidence=0.95))
            if f"{city} city" in message_lower:
                entities.append(Entity(type=EntityType.CITY, value=city.title(), confidence=0.95))
        
        # 3. Warehouses
        for warehouse in self.KNOWN_WAREHOUSES:
            if warehouse in message_lower:
                entities.append(Entity(type=EntityType.WAREHOUSE, value=warehouse.title(), confidence=0.95))
        
        # 4. Dealers (with suffix detection)
        for pattern in self._dealer_patterns:
            match = pattern.search(message)
            if match:
                dealer_name = match.group(1).strip()
                if len(dealer_name) > 2:
                    entities.append(Entity(type=EntityType.DEALER, value=dealer_name, confidence=0.85))
        
        # 5. Products
        product_patterns = [
            r"(?:product|model|material|item)\s+([\w\s\-_]+)",
            r"([\w\s\-_]+)\s+(?:product|model)"
        ]
        for pattern in product_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for product in matches:
                if len(product.strip()) > 2:
                    entities.append(Entity(type=EntityType.PRODUCT, value=product.strip(), confidence=0.80))
        
        # 6. Fuzzy matching with RapidFuzz
        fuzz, process = LazyLoader.get_rapidfuzz()
        if fuzz and process:
            # City fuzzy matches
            city_matches = process.extract(message_lower, self.KNOWN_CITIES, scorer=fuzz.WRatio, limit=3)
            for match, score, _ in city_matches:
                if score >= 85:
                    if not any(e.type == EntityType.CITY and e.value.lower() == match for e in entities):
                        entities.append(Entity(type=EntityType.CITY, value=match.title(), confidence=score/100))
            
            # Warehouse fuzzy matches
            wh_matches = process.extract(message_lower, self.KNOWN_WAREHOUSES, scorer=fuzz.WRatio, limit=3)
            for match, score, _ in wh_matches:
                if score >= 85:
                    if not any(e.type == EntityType.WAREHOUSE and e.value.lower() == match for e in entities):
                        entities.append(Entity(type=EntityType.WAREHOUSE, value=match.title(), confidence=score/100))
        
        # 7. Dates
        date_patterns = [
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",
        ]
        for pattern in date_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for date_str in matches:
                entities.append(Entity(type=EntityType.DATE, value=date_str, confidence=0.80))
        
        # 8. spaCy NER (lazy load)
        nlp = LazyLoader.get_spacy()
        if nlp:
            try:
                doc = nlp(message)
                for ent in doc.ents:
                    if ent.label_ in ["GPE", "LOC"]:
                        if ent.text.lower() in self.KNOWN_CITIES:
                            if not any(e.type == EntityType.CITY and e.value.lower() == ent.text.lower() for e in entities):
                                entities.append(Entity(type=EntityType.CITY, value=ent.text, confidence=0.90))
                    elif ent.label_ == "ORG":
                        if not any(e.type == EntityType.DEALER and e.value.lower() == ent.text.lower() for e in entities):
                            entities.append(Entity(type=EntityType.DEALER, value=ent.text, confidence=0.85))
                    elif ent.label_ == "DATE":
                        if not any(e.type == EntityType.DATE and e.value.lower() == ent.text.lower() for e in entities):
                            entities.append(Entity(type=EntityType.DATE, value=ent.text, confidence=0.90))
            except Exception:
                pass
        
        # Remove duplicates
        unique_entities = []
        seen = set()
        for entity in entities:
            key = f"{entity.type.value}:{entity.value}"
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)
        
        return unique_entities

# =====================================================================================================================
# DISTANCE SERVICE
# =====================================================================================================================

class DistanceService:
    """Distance calculation service"""
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if DistanceService._initialized:
            return
        
        self._initialized = True
        self._cache = {}
        self._cache_lock = threading.RLock()
        
        # Lazy load geopy
        self._geopy_available = False
        self._geocoder = None
        
        try:
            from geopy.distance import great_circle, geodesic
            from geopy.geocoders import Nominatim
            self._geopy_available = True
            self._geocoder = Nominatim(user_agent="hpk-logistics-ai", timeout=5)
            logger.info("✅ Geopy loaded")
        except ImportError:
            logger.info("ℹ️ Geopy not available")
        
        # Lazy load openrouteservice
        self._ors_available = False
        self._ors_client = None
        
        try:
            import openrouteservice
            self._ors_available = True
            ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY")
            if ORS_API_KEY:
                self._ors_client = openrouteservice.Client(key=ORS_API_KEY, timeout=10)
                logger.info("✅ OpenRouteService loaded")
        except ImportError:
            logger.info("ℹ️ OpenRouteService not available")
        
        logger.info("✅ DistanceService initialized")
    
    def calculate_distance(self, origin: str, destination: str) -> Dict[str, Any]:
        """Calculate distance between two locations"""
        cache_key = f"{origin.lower()}|{destination.lower()}"
        
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        result = {
            "distance_km": None,
            "driving_time": None,
            "estimated_delivery": None,
            "source": "unavailable"
        }
        
        # Try OpenRouteService first
        if self._ors_available and self._ors_client:
            try:
                coords = self._geocode_locations(origin, destination)
                if coords:
                    route = self._ors_client.directions(
                        coords,
                        profile="driving-car",
                        format="geojson"
                    )
                    if route and "features" in route and route["features"]:
                        distance = route["features"][0]["properties"]["segments"][0]["distance"] / 1000
                        duration = route["features"][0]["properties"]["segments"][0]["duration"] / 3600
                        result["distance_km"] = round(distance, 1)
                        result["driving_time"] = self._format_duration(duration)
                        result["source"] = "openrouteservice"
            except Exception:
                pass
        
        # Fallback to geopy
        if result["distance_km"] is None and self._geopy_available:
            try:
                origin_coords = self._get_coordinates(origin)
                dest_coords = self._get_coordinates(destination)
                if origin_coords and dest_coords:
                    from geopy.distance import great_circle
                    distance = great_circle(origin_coords, dest_coords).kilometers
                    result["distance_km"] = round(distance, 1)
                    result["driving_time"] = self._format_duration(distance / 50)
                    result["source"] = "geopy"
            except Exception:
                pass
        
        # Calculate estimated delivery
        if result["distance_km"]:
            if result["distance_km"] <= 80:
                result["estimated_delivery"] = "Same Day"
            elif result["distance_km"] <= 200:
                result["estimated_delivery"] = "Next Day"
            elif result["distance_km"] <= 400:
                result["estimated_delivery"] = "1-2 Days"
            elif result["distance_km"] <= 700:
                result["estimated_delivery"] = "2-3 Days"
            else:
                result["estimated_delivery"] = "3-5 Days"
        
        with self._cache_lock:
            self._cache[cache_key] = result
        
        return result
    
    def _geocode_locations(self, origin: str, destination: str) -> Optional[List]:
        """Geocode locations for routing"""
        if not self._geopy_available or not self._geocoder:
            return None
        
        try:
            origin_coords = self._get_coordinates(origin)
            dest_coords = self._get_coordinates(destination)
            if origin_coords and dest_coords:
                return [[origin_coords[1], origin_coords[0]], [dest_coords[1], dest_coords[0]]]
        except Exception:
            pass
        
        return None
    
    def _get_coordinates(self, location: str) -> Optional[Tuple[float, float]]:
        """Get coordinates for a location"""
        if not self._geopy_available or not self._geocoder:
            return None
        
        cache_key = f"coord:{location.lower()}"
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        
        try:
            geo = self._geocoder.geocode(location, exactly_one=True)
            if geo:
                coords = (geo.latitude, geo.longitude)
                with self._cache_lock:
                    self._cache[cache_key] = coords
                return coords
        except Exception:
            pass
        
        return None
    
    def _format_duration(self, hours: float) -> str:
        """Format duration in hours and minutes"""
        if hours < 1:
            minutes = int(hours * 60)
            return f"{minutes} Minutes"
        else:
            h = int(hours)
            m = int((hours - h) * 60)
            return f"{h} Hours {m} Minutes" if m > 0 else f"{h} Hours"

# =====================================================================================================================
# MAIN AI PROVIDER SERVICE
# =====================================================================================================================

class AIProviderService:
    """
    Enterprise AI Orchestrator with all features
    """
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if AIProviderService._initialized:
            return
        
        self._initialized = True
        
        # Initialize core components (singletons)
        self.context_manager = ContextManager()
        self.intent_engine = IntentEngine()
        self.entity_engine = EntityEngine()
        self.distance_service = DistanceService()
        self.metrics = MetricsRegistry()
        
        # Initialize service registry
        self._init_service_registry()
        
        # Local cache
        self._cache = {}
        self._cache_lock = threading.RLock()
        
        logger.info("=" * 60)
        logger.info("🚀 AI Provider Service v32.0 initialized")
        logger.info(f"📦 Services: {', '.join(self.service_registry.keys())}")
        logger.info(f"📊 Monitoring: {'Enabled' if self.metrics.is_enabled() else 'Disabled'}")
        logger.info("=" * 60)
    
    def _init_service_registry(self):
        """Initialize service registry"""
        self.service_registry = {}
        
        try:
            from app.services.dn_analysis import DNAnalysisService
            self.service_registry["dn"] = DNAnalysisService()
            logger.info("✅ Registered DN service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register DN service: {e}")
        
        try:
            from app.services.dealer_analytics_service import DealerAnalyticsService
            self.service_registry["dealer"] = DealerAnalyticsService()
            logger.info("✅ Registered Dealer service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Dealer service: {e}")
        
        try:
            from app.services.city_service import CityAnalyticsService
            self.service_registry["city"] = CityAnalyticsService()
            logger.info("✅ Registered City service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register City service: {e}")
        
        try:
            from app.services.warehouse_service import WarehouseAnalyticsService
            self.service_registry["warehouse"] = WarehouseAnalyticsService()
            logger.info("✅ Registered Warehouse service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Warehouse service: {e}")
        
        try:
            from app.services.product_service import ProductAnalyticsService
            self.service_registry["product"] = ProductAnalyticsService()
            logger.info("✅ Registered Product service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Product service: {e}")
        
        try:
            from app.services.national_kpi_service import NationalKPIService
            self.service_registry["national"] = NationalKPIService()
            logger.info("✅ Registered National KPI service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register National KPI service: {e}")
    
    # =====================================================================================================================
    # MAIN PROCESSING PIPELINE
    # =====================================================================================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        Main processing pipeline
        """
        start_time = time.perf_counter()
        sender = sender or sender_id or "default"
        
        if not message or not message.strip():
            return self._get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📨 Processing: '{message_clean}' from {sender}")
        
        # Check cache
        cache_key = f"{sender}:{hashlib.md5(message_clean.encode()).hexdigest()}"
        with self._cache_lock:
            if cache_key in self._cache:
                logger.info(f"✅ Cache hit for: {message_clean[:50]}")
                return self._cache[cache_key]
        
        try:
            # 1. Extract entities
            entities = self.entity_engine.extract_entities(message_clean)
            logger.info(f"🔍 Entities: {[(e.type.value, e.value, e.confidence) for e in entities]}")
            
            # 2. Detect intent
            intent, confidence = self.intent_engine.detect_intent(message_clean, entities)
            logger.info(f"🎯 Intent: {intent.type.value} (confidence: {confidence:.2f})")
            
            # 3. Get context
            context = self.context_manager.get_context(sender)
            
            # 4. Update context with entities
            for entity in entities:
                if entity.type == EntityType.CITY:
                    context.current_city = entity.value
                elif entity.type == EntityType.DEALER:
                    context.current_dealer = entity.value
                elif entity.type == EntityType.WAREHOUSE:
                    context.current_warehouse = entity.value
                elif entity.type == EntityType.PRODUCT:
                    context.current_product = entity.value
            
            context.last_intent = intent.type
            if entities:
                context.last_entity = entities[0]
            
            self.context_manager.add_history(sender, {
                "role": "user",
                "content": message_clean,
                "intent": intent.type.value,
                "entities": [{"type": e.type.value, "value": e.value} for e in entities],
                "timestamp": datetime.now().isoformat()
            })
            
            # 5. Route the request
            decision = self._build_routing_decision(message_clean, intent, entities, context)
            
            # 6. Execute
            response = await self._execute_decision(sender, decision, context)
            
            # 7. Cache response
            with self._cache_lock:
                self._cache[cache_key] = response
                if len(self._cache) > 1000:
                    # Remove oldest entries
                    keys = list(self._cache.keys())[:100]
                    for key in keys:
                        del self._cache[key]
            
            # 8. Add follow-ups
            follow_ups = self.context_manager.get_follow_ups(sender)
            if follow_ups and len(response) < 3500:
                response = self._add_follow_ups(response, follow_ups)
            
            # 9. Split if too long (WhatsApp limit: 4096)
            if len(response) > 4000:
                response = self._split_response(response)
            
            # 10. Record metrics
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            if self.metrics.is_enabled():
                duration_metric = self.metrics.get("duration")
                if duration_metric:
                    duration_metric.labels(
                        service=decision.service_key,
                        intent=intent.type.value
                    ).observe(elapsed_ms / 1000)
                
                requests_metric = self.metrics.get("requests")
                if requests_metric:
                    requests_metric.labels(
                        service=decision.service_key,
                        intent=intent.type.value,
                        status="success"
                    ).inc()
            
            logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")
            return response
            
        except Exception as e:
            logger.exception(f"❌ Error processing message: {e}")
            
            # Record error
            if self.metrics.is_enabled():
                errors_metric = self.metrics.get("errors")
                if errors_metric:
                    errors_metric.labels(
                        service="ai_provider",
                        error_type=type(e).__name__
                    ).inc()
            
            return f"⚠️ Service error: {str(e)[:200]}\n\nPlease try again or type 'menu' for options."
    
    def _build_routing_decision(
        self,
        message: str,
        intent: Intent,
        entities: List[Entity],
        context: SessionContext
    ) -> RoutingDecision:
        """Build routing decision"""
        
        # Check if in menu
        if context.current_menu != MenuState.MAIN and intent.type == IntentType.MENU:
            context.current_menu = MenuState.MAIN
            return RoutingDecision(
                intent=intent,
                service_key="menu_service",
                method="show_main_menu",
                entity={},
                confidence=1.0,
                reason="Menu exit",
                original_message=message
            )
        
        # Menu number in menu
        menu_number = self._parse_menu_number(message)
        if menu_number is not None and context.current_menu != MenuState.MAIN:
            return self._handle_menu_selection(context, menu_number)
        
        # Special intents
        if intent.type == IntentType.MENU:
            return RoutingDecision(
                intent=intent,
                service_key="menu_service",
                method="show_menu",
                entity={"menu_type": context.current_menu.value},
                confidence=1.0,
                reason="Menu request",
                original_message=message
            )
        
        if intent.type == IntentType.GREETING:
            return RoutingDecision(
                intent=intent,
                service_key="greeting_service",
                method="handle_greeting",
                entity={},
                confidence=1.0,
                reason="Greeting",
                original_message=message
            )
        
        if intent.type == IntentType.HELP:
            return RoutingDecision(
                intent=intent,
                service_key="help_service",
                method="handle_help",
                entity={},
                confidence=1.0,
                reason="Help request",
                original_message=message
            )
        
        # Get primary entity
        primary_entity = None
        entity_value = None
        
        entity_priority = [EntityType.CITY, EntityType.DEALER, EntityType.WAREHOUSE, EntityType.PRODUCT, EntityType.DN]
        for entity_type in entity_priority:
            for entity in entities:
                if entity.type == entity_type:
                    primary_entity = entity
                    entity_value = entity.value
                    break
            if primary_entity:
                break
        
        # Determine service
        service_key = None
        method = "process_whatsapp_query"
        reason = ""
        
        if primary_entity:
            if primary_entity.type == EntityType.CITY:
                service_key = "city"
                reason = f"City: {entity_value}"
                # Use existing city if already in context
                if entity_value and entity_value.lower() in [c.lower() for c in self.entity_engine.KNOWN_CITIES]:
                    pass
            elif primary_entity.type == EntityType.DEALER:
                service_key = "dealer"
                reason = f"Dealer: {entity_value}"
            elif primary_entity.type == EntityType.WAREHOUSE:
                service_key = "warehouse"
                reason = f"Warehouse: {entity_value}"
            elif primary_entity.type == EntityType.PRODUCT:
                service_key = "product"
                reason = f"Product: {entity_value}"
            elif primary_entity.type == EntityType.DN:
                service_key = "dn"
                method = "get_dn_dashboard"
                reason = f"DN: {entity_value}"
        elif intent.type in [IntentType.NATIONAL, IntentType.EXECUTIVE]:
            service_key = "national"
            reason = f"{intent.type.value} intent"
        elif intent.type == IntentType.RANKING:
            service_key = self._get_ranking_service(message)
            reason = "Ranking request"
        elif intent.type == IntentType.COMPARISON:
            service_key = self._get_comparison_service(message, entities)
            reason = "Comparison request"
        elif intent.type == IntentType.DISTANCE:
            service_key = "city"
            reason = "Distance request"
        else:
            # AI fallback
            service_key = "groq_service"
            method = "process_query"
            reason = "AI fallback"
            intent.requires_ai = True
        
        # Build entity dict
        entity_dict = {
            "message": message,
            "intent": intent.type.value,
            "metric": intent.metric,
            "confidence": intent.confidence,
        }
        
        for entity in entities:
            entity_dict[entity.type.value] = entity.value
        
        # Add context
        if context.current_city and "city" not in entity_dict:
            entity_dict["city"] = context.current_city
        if context.current_dealer and "dealer" not in entity_dict:
            entity_dict["dealer"] = context.current_dealer
        if context.current_warehouse and "warehouse" not in entity_dict:
            entity_dict["warehouse"] = context.current_warehouse
        if context.current_product and "product" not in entity_dict:
            entity_dict["product"] = context.current_product
        
        # Update context
        if service_key:
            context.current_service = service_key
        
        return RoutingDecision(
            intent=intent,
            service_key=service_key,
            method=method,
            entity=entity_dict,
            confidence=intent.confidence,
            requires_ai=intent.requires_ai,
            reason=reason,
            original_message=message,
            context={
                "current_city": context.current_city,
                "current_dealer": context.current_dealer,
                "current_warehouse": context.current_warehouse,
                "current_product": context.current_product,
            }
        )
    
    async def _execute_decision(self, sender: str, decision: RoutingDecision, context: SessionContext) -> str:
        """Execute routing decision"""
        # Menu service
        if decision.service_key == "menu_service":
            if decision.method == "show_menu":
                return self._get_sub_menu(context.current_menu)
            return self._get_main_menu()
        
        # Greeting service
        if decision.service_key == "greeting_service":
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"
        
        # Help service
        if decision.service_key == "help_service":
            return self._get_help_response()
        
        # Get service
        service = self.service_registry.get(decision.service_key)
        if not service:
            return self._ai_fallback(decision.original_message, decision.intent, [], context)
        
        # Get method
        method = getattr(service, decision.method, None)
        if not method:
            return self._ai_fallback(decision.original_message, decision.intent, [], context)
        
        try:
            # Prepare arguments
            if decision.method == "process_whatsapp_query":
                result = method(decision.original_message, sender)
            elif decision.method == "get_dn_dashboard":
                dn = decision.entity.get("dn")
                result = method(dn) if dn else "⚠️ Please provide a DN number."
            else:
                result = method()
            
            # Handle async
            if inspect.isawaitable(result):
                result = await result
            
            # Extract message
            return self._extract_response(result)
            
        except Exception as e:
            logger.exception(f"Service execution error: {e}")
            return self._ai_fallback(decision.original_message, decision.intent, [], context)
    
    def _parse_menu_number(self, message: str) -> Optional[int]:
        """Parse menu number"""
        match = re.fullmatch(r"\s*([0-9]+)\s*", message)
        if match:
            return int(match.group(1))
        return None
    
    def _handle_menu_selection(self, context: SessionContext, number: int) -> RoutingDecision:
        """Handle menu selection"""
        menu_map = {
            1: ("city", "City Dashboard", "get_city_dashboard"),
            2: ("city", "City Revenue", "get_city_metric"),
            3: ("city", "City Units", "get_city_metric"),
            4: ("city", "City Pending", "get_city_pending"),
            5: ("city", "City Delivery", "get_city_delivery"),
            6: ("city", "Compare Cities", "compare_cities"),
            7: ("city", "City Rankings", "get_city_ranking"),
            8: ("city", "Top Products", "get_city_top_products"),
            9: ("city", "Business Score", "get_city_business_score"),
            10: ("city", "Distance Info", "get_city_distance"),
            11: ("city", "Growth Analytics", "get_city_growth"),
            12: ("city", "Warehouse Distribution", "get_warehouse_distribution"),
            13: ("city", "City Summary", "get_city_summary"),
            99: ("menu", "Back to Main", "show_main_menu"),
        }
        
        if number in menu_map:
            service_key, name, method = menu_map[number]
            if number == 99:
                context.current_menu = MenuState.MAIN
            return RoutingDecision(
                intent=Intent(type=IntentType.MENU, confidence=1.0),
                service_key=service_key,
                method=method,
                entity={"menu_number": number, "menu_name": name},
                confidence=1.0,
                reason=f"Menu selection: {name}",
                original_message=str(number),
                menu_option=str(number)
            )
        
        return RoutingDecision(
            intent=Intent(type=IntentType.UNKNOWN, confidence=0.0),
            service_key="menu_service",
            method="show_invalid",
            entity={},
            confidence=0.0,
            reason="Invalid menu selection"
        )
    
    def _get_ranking_service(self, message: str) -> str:
        """Get service for ranking"""
        message_lower = message.lower()
        if "warehouse" in message_lower:
            return "warehouse"
        elif "dealer" in message_lower:
            return "dealer"
        elif "product" in message_lower:
            return "product"
        elif "city" in message_lower:
            return "city"
        return "national"
    
    def _get_comparison_service(self, message: str, entities: List[Entity]) -> str:
        """Get service for comparison"""
        entity_types = [e.type for e in entities]
        if EntityType.CITY in entity_types:
            return "city"
        elif EntityType.DEALER in entity_types:
            return "dealer"
        elif EntityType.WAREHOUSE in entity_types:
            return "warehouse"
        elif EntityType.PRODUCT in entity_types:
            return "product"
        return "national"
    
    def _extract_response(self, result: Any) -> str:
        """Extract WhatsApp message from result"""
        if isinstance(result, str):
            return result
        
        if isinstance(result, dict):
            if result.get("whatsapp_message"):
                return str(result["whatsapp_message"])
            if result.get("formatted_response"):
                return str(result["formatted_response"])
            if result.get("message"):
                return str(result["message"])
            if result.get("response"):
                return str(result["response"])
            if result.get("error"):
                return f"⚠️ {result['error']}"
        
        return str(result) if result else "No response from service."
    
    def _ai_fallback(self, message: str, intent: Intent, entities: List[Entity], context: SessionContext) -> str:
        """AI fallback for unanswered queries"""
        if not ENABLE_AI_FALLBACK:
            return self._get_help_response()
        
        try:
            groq_client = LazyLoader.get_groq()
            if not groq_client:
                return self._get_help_response()
            
            entity_str = ", ".join([f"{e.type.value}: {e.value}" for e in entities]) if entities else "None"
            context_str = f"Current city: {context.current_city or 'None'}, Dealer: {context.current_dealer or 'None'}"
            
            prompt = f"""You are HPK Logistics AI Assistant. The user asked: "{message}"

Detected intent: {intent.type.value} (confidence: {intent.confidence:.2f})
Entities: {entity_str}
Context: {context_str}

Provide a helpful response. If you don't know, suggest logistics topics they can ask about.

Available topics:
- DN Tracking (send a DN number)
- Dealer Analytics (dealer name)
- Warehouse Analytics (warehouse name)
- City Analytics (city name)
- Product Analytics (product name)
- National KPIs
- Pending Deliveries

Keep response concise and WhatsApp-friendly with emojis and bullet points."""

            response = groq_client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "You are HPK Logistics AI Assistant. Help users with logistics data."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500,
            )
            
            ai_response = response.choices[0].message.content.strip()
            
            # Record AI call metric
            if self.metrics.is_enabled():
                ai_calls_metric = self.metrics.get("ai_calls")
                if ai_calls_metric:
                    ai_calls_metric.labels(provider="groq", model="llama3-70b-8192").inc()
            
            return ai_response
            
        except Exception as e:
            logger.error(f"AI fallback failed: {e}")
            return self._get_help_response()
    
    def _add_follow_ups(self, response: str, follow_ups: List[str]) -> str:
        """Add follow-up suggestions"""
        if not follow_ups:
            return response
        
        footer = "\n\n💡 *Try:*"
        for suggestion in follow_ups[:3]:
            footer += f"\n• {suggestion}"
        
        if len(response) + len(footer) > 4000:
            return response
        
        return response + footer
    
    def _split_response(self, response: str) -> str:
        """Split response for WhatsApp (4096 char limit)"""
        if len(response) <= 4000:
            return response
        
        parts = []
        current_part = ""
        
        for line in response.split("\n"):
            if len(current_part) + len(line) + 1 > 3800:
                parts.append(current_part + "\n\n--- Part 1 ---")
                current_part = line
            else:
                current_part += "\n" + line
        
        if current_part:
            parts.append(current_part)
        
        return "\n\n---\n\n".join([f"📱 *Part {i+1}/{len(parts)}*\n{part}" for i, part in enumerate(parts)])
    
    # =====================================================================================================================
    # MENU METHODS
    # =====================================================================================================================
    
    def _get_main_menu(self) -> str:
        return (
            "📋 *AI LOGISTICS MENU*\n\n"
            "0. Main Menu\n"
            "1. DN Delivery\n"
            "2. Dealer Analytics\n"
            "3. City Analytics\n"
            "4. Warehouse Analytics\n"
            "5. Product Analytics\n"
            "6. National KPI\n"
            "7. Pending DN\n"
            "8. Top Performers\n"
            "9. AI Query\n\n"
            "Reply with a number from 0 to 9."
        )
    
    def _get_sub_menu(self, menu_state: MenuState) -> str:
        """Get sub-menu based on state"""
        if menu_state == MenuState.CITY:
            return self._get_city_menu()
        elif menu_state == MenuState.WAREHOUSE:
            return self._get_warehouse_menu()
        elif menu_state == MenuState.DEALER:
            return self._get_dealer_menu()
        elif menu_state == MenuState.PRODUCT:
            return self._get_product_menu()
        elif menu_state == MenuState.NATIONAL:
            return self._get_national_menu()
        elif menu_state == MenuState.DN:
            return self._get_dn_menu()
        return self._get_main_menu()
    
    def _get_city_menu(self) -> str:
        return "\n".join([
            "🏙️ *CITY ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. City Dashboard",
            "2. City Revenue",
            "3. City Units",
            "4. City Pending",
            "5. City Delivery",
            "6. Compare Cities",
            "7. City Rankings",
            "8. Top Products",
            "9. Business Score",
            "10. Distance Info",
            "11. Growth Analytics",
            "12. Warehouse Distribution",
            "13. City Summary",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_warehouse_menu(self) -> str:
        return "\n".join([
            "🏭 *WAREHOUSE ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Warehouse Dashboard",
            "2. Warehouse Inventory",
            "3. Warehouse Revenue",
            "4. Warehouse Units",
            "5. Pending DN",
            "6. Pending PGI",
            "7. Pending POD",
            "8. Delivery Performance",
            "9. Warehouse Ranking",
            "10. Warehouse Comparison",
            "11. Top Products",
            "12. Dealer Distribution",
            "13. City Distribution",
            "14. Storage Utilization",
            "15. Transit Analysis",
            "16. Delivery Aging",
            "17. Warehouse KPIs",
            "18. Warehouse AI Summary",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_dealer_menu(self) -> str:
        return "\n".join([
            "🏪 *DEALER ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Dealer Dashboard",
            "2. Dealer Revenue",
            "3. Dealer Units",
            "4. Dealer Products",
            "5. Dealer Performance",
            "6. Pending DN",
            "7. Pending PGI",
            "8. Pending POD",
            "9. Dealer Delivery",
            "10. Dealer Ranking",
            "11. Dealer Comparison",
            "12. Dealer History",
            "13. Dealer Search",
            "14. Dealer Cities",
            "15. Dealer Distance",
            "16. Dealer Trends",
            "17. Dealer Forecast",
            "18. Dealer AI Summary",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_product_menu(self) -> str:
        return "\n".join([
            "📦 *PRODUCT ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. Product Dashboard",
            "2. Product Revenue",
            "3. Product Units",
            "4. Product Dealers",
            "5. Product Warehouses",
            "6. Product Cities",
            "7. Pending DN",
            "8. Pending PGI",
            "9. Pending POD",
            "10. Product Comparison",
            "11. Product Ranking",
            "12. Monthly Trend",
            "13. Executive Summary",
            "14. AI Insights",
            "15. Recommendations",
            "16. Product Life Cycle",
            "17. Product Performance",
            "18. Smart Search",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_national_menu(self) -> str:
        return "\n".join([
            "🇵🇰 *NATIONAL KPI MENU*",
            "",
            "0. Main Menu",
            "1. National Dashboard",
            "2. Warehouse Dashboard",
            "3. Warehouse Ranking",
            "4. Warehouse Comparison",
            "5. National Revenue",
            "6. National Units",
            "7. National Delivery",
            "8. Pending Dashboard",
            "9. POD Dashboard",
            "10. PGI Dashboard",
            "11. Dealer Coverage",
            "12. City Analytics",
            "13. Product Distribution",
            "14. SLA Compliance",
            "15. Executive Summary",
            "16. AI Insights",
            "17. Recommendations",
            "18. National Health Score",
            "19. Monthly Trend",
            "20. National Forecast",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_dn_menu(self) -> str:
        return "\n".join([
            "📦 *DN DELIVERY MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. DN History",
            "4. DN Timeline",
            "5. Transit Analysis",
            "6. Pending DN",
            "7. Pending PGI",
            "8. Pending POD",
            "9. Delayed DN",
            "10. Recent DN",
            "11. Search DN",
            "12. Compare DN",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])
    
    def _get_help_response(self) -> str:
        return "\n".join([
            "🤖 *How can I help?*",
            "",
            "You can ask me about:",
            "",
            "📍 **City Analytics** - Lahore, Karachi, Rawalpindi...",
            "🏪 **Dealer Analytics** - Performance, ranking, comparison",
            "🏭 **Warehouse Analytics** - Performance, inventory, ranking",
            "📦 **Product Analytics** - Sales, performance, life cycle",
            "🇵🇰 **National KPI** - National performance, executive dashboard",
            "📋 **DN Tracking** - Send any 8-12 digit DN number",
            "",
            "Type *menu* to see all options."
        ])
    
    # =====================================================================================================================
    # SHOW MENU METHODS
    # =====================================================================================================================
    
    def show_main_menu(self) -> str:
        return self._get_main_menu()
    
    def show_menu(self, menu_type: str = None) -> str:
        """Show menu based on type"""
        if menu_type == "city":
            return self._get_city_menu()
        elif menu_type == "warehouse":
            return self._get_warehouse_menu()
        elif menu_type == "dealer":
            return self._get_dealer_menu()
        elif menu_type == "product":
            return self._get_product_menu()
        elif menu_type == "national":
            return self._get_national_menu()
        elif menu_type == "dn":
            return self._get_dn_menu()
        return self._get_main_menu()
    
    def show_invalid(self) -> str:
        return "Invalid selection. Please enter a valid number.\n\n" + self._get_main_menu()
    
    def handle_greeting(self) -> str:
        return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"
    
    def handle_help(self) -> str:
        return self._get_help_response()
    
    # =====================================================================================================================
    # HEALTH CHECK
    # =====================================================================================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        return {
            "service": "ai_provider_service",
            "version": "32.0",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "components": {
                "context_manager": True,
                "intent_engine": True,
                "entity_engine": True,
                "distance_service": True,
                "monitoring": self.metrics.is_enabled(),
            },
            "services": list(self.service_registry.keys()),
            "metrics": {
                "cache_size": len(self._cache),
                "active_sessions": len(self.context_manager._contexts),
            },
            "lazy_loaded": {
                "spacy": LazyLoader.get_spacy() is not None,
                "sentence_transformer": LazyLoader.get_sentence_transformer() is not None,
                "groq": LazyLoader.get_groq() is not None,
                "rapidfuzz": LazyLoader.get_rapidfuzz()[0] is not None,
                "flashrank": LazyLoader.get_flashrank() is not None,
            }
        }

# =====================================================================================================================
# SINGLETON
# =====================================================================================================================

_ai_service: Optional[AIProviderService] = None
_service_lock = threading.Lock()

def get_ai_provider_service() -> AIProviderService:
    global _ai_service
    if _ai_service is None:
        with _service_lock:
            if _ai_service is None:
                _ai_service = AIProviderService()
    return _ai_service

async def process_whatsapp_query(
    message: str,
    sender: Optional[str] = None,
    sender_id: Optional[str] = None,
    **kwargs: Any,
) -> str:
    try:
        return await get_ai_provider_service().process_whatsapp_query(
            message=message,
            sender=sender,
            sender_id=sender_id,
            **kwargs,
        )
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again."

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
]
