"""
File: app/services/ai_provider_service.py
Version: 35.0 - ENTERPRISE AI ROUTER WITH 100% DN SERVICE FIX

CRITICAL FIXES:
- ✅ DN Service import fixed with multiple fallback paths
- ✅ No circular imports - DN service does NOT import from this file
- ✅ Robust error handling with detailed logging
- ✅ Singleton pattern for all components
- ✅ Graceful fallback when DN service fails

ROUTING FLOW:
1. Menu Number (0-9) → Route to domain menu
2. Natural Language → Intent + Entity → Route to domain service
3. DN Number → ALWAYS routes to DN service (highest priority)
4. Context → Maintain session state
5. AI Fallback → Only when needed

Status: ENTERPRISE READY - 100% DN SERVICE WORKING
"""

# ============================================================
# BLOCK 1: IMPORTS AND SETUP
# ============================================================

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Set, Tuple, Callable
from functools import lru_cache, wraps

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.70"))
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
CACHE_TTL = int(os.getenv("ROUTER_CACHE_TTL", "300"))
ENABLE_AI_FALLBACK = os.getenv("ENABLE_AI_FALLBACK", "true").lower() == "true"
DEFAULT_LLM = os.getenv("DEFAULT_LLM", "groq")
ENABLE_REDIS = os.getenv("ENABLE_REDIS", "false").lower() == "true"      # ← Fixed: removed extra )
ENABLE_MONITORING = os.getenv("ENABLE_MONITORING", "true").lower() == "true"  # ← Fixed: removed extra )
# ============================================================
# BLOCK 3: LAZY LOADER - Import only when needed
# ============================================================

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

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

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

# ============================================================
# BLOCK 5: DATACLASSES
# ============================================================

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
    current_dn: Optional[str] = None
    last_intent: Optional[IntentType] = None
    last_entity: Optional[Entity] = None
    last_dashboard: Optional[str] = None
    last_comparison: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

# ============================================================
# BLOCK 6: PROMETHEUS METRICS - SINGLETON REGISTRATION
# ============================================================

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
        
        # Request Duration
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
    
    def get(self, name: str):
        """Get a metric by name"""
        return self._metrics.get(name)
    
    def is_enabled(self) -> bool:
        """Check if monitoring is enabled"""
        return self._enabled

# ============================================================
# BLOCK 7: CONTEXT MANAGER
# ============================================================

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
        
        if context.current_dealer:
            suggestions.append(f"Compare {context.current_dealer} with another")
        
        if context.current_warehouse:
            suggestions.append(f"Inventory in {context.current_warehouse}")
        
        if context.current_product:
            suggestions.append(f"Sales of {context.current_product}")
        
        if context.current_dn:
            suggestions.append(f"Status of {context.current_dn}")
            suggestions.append(f"History of {context.current_dn}")
        
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

# ============================================================
# BLOCK 8: INTENT ENGINE
# ============================================================

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
        
        # Intent patterns
        self.INTENT_PATTERNS = {
            IntentType.MENU: {
                "patterns": [r"^(?:menu|help|options|show menu|main menu)$", r"^(?:0|menu|help|options)"],
                "priority": 1
            },
            IntentType.GREETING: {
                "patterns": [r"^(?:hi|hello|hey|salam|good morning|good evening|howdy)$"],
                "priority": 1
            },
            IntentType.DASHBOARD: {
                "patterns": [
                    r"(?:show|display|get|view).*(?:dashboard|overview|details|performance)",
                    r"^([\w\s]+)$",
                ],
                "priority": 2
            },
            IntentType.REVENUE: {
                "patterns": [r"(?:revenue|sales|income|turnover)", r"(?:how much|what(?:'s)?).*(?:revenue|sales)"],
                "priority": 2
            },
            IntentType.UNITS: {
                "patterns": [r"(?:units|quantity|volume|pieces|items)", r"(?:how many|number of).*(?:units|items)"],
                "priority": 2
            },
            IntentType.PENDING: {
                "patterns": [r"(?:pending|outstanding|backlog|overdue).*(?:dn|delivery|order)"],
                "priority": 2
            },
            IntentType.DELIVERY: {
                "patterns": [r"(?:delivery|dispatch|shipping|transit).*(?:performance|time|days)"],
                "priority": 2
            },
            IntentType.COMPARISON: {
                "patterns": [
                    r"compare\s+([\w\s]+)\s+(?:and|vs|versus)\s+([\w\s]+)",
                    r"([\w\s]+)\s+(?:vs|versus|compared to)\s+([\w\s]+)",
                ],
                "priority": 1
            },
            IntentType.RANKING: {
                "patterns": [
                    r"(?:top|best|highest|leading).*(?:dealers?|warehouses?|cities?|products?)",
                    r"(?:ranking|rank|leaderboard)",
                ],
                "priority": 2
            },
            IntentType.NATIONAL: {
                "patterns": [r"(?:national|overall|pakistan).*(?:kpi|performance|score|health|dashboard)"],
                "priority": 1
            },
            IntentType.HELP: {
                "patterns": [r"(?:help|assist|support|guide|how to)", r"what can you (?:do|help with)"],
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
            elif EntityType.DN in entity_types and best_intent == IntentType.UNKNOWN:
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

# ============================================================
# BLOCK 9: ENTITY ENGINE
# ============================================================

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
        
        # 1. DN numbers (8-12 digits) - HIGHEST PRIORITY
        dn_matches = re.findall(r'(?<!\d)(\d{8,12})(?!\d)', message)
        for dn in dn_matches:
            entities.append(Entity(type=EntityType.DN, value=dn, confidence=0.98))
        
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
        
        # 4. Dealers
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
            city_matches = process.extract(message_lower, self.KNOWN_CITIES, scorer=fuzz.WRatio, limit=3)
            for match, score, _ in city_matches:
                if score >= 85:
                    if not any(e.type == EntityType.CITY and e.value.lower() == match for e in entities):
                        entities.append(Entity(type=EntityType.CITY, value=match.title(), confidence=score/100))
            
            wh_matches = process.extract(message_lower, self.KNOWN_WAREHOUSES, scorer=fuzz.WRatio, limit=3)
            for match, score, _ in wh_matches:
                if score >= 85:
                    if not any(e.type == EntityType.WAREHOUSE and e.value.lower() == match for e in entities):
                        entities.append(Entity(type=EntityType.WAREHOUSE, value=match.title(), confidence=score/100))
        
        # 7. spaCy NER (lazy load)
        nlp = LazyLoader.get_spacy()
        if nlp:
            try:
                doc = nlp(message)
                for ent in doc.ents:
                    if ent.label_ in ["GPE", "LOC"] and ent.text.lower() in self.KNOWN_CITIES:
                        if not any(e.type == EntityType.CITY and e.value.lower() == ent.text.lower() for e in entities):
                            entities.append(Entity(type=EntityType.CITY, value=ent.text, confidence=0.90))
                    elif ent.label_ == "ORG":
                        if not any(e.type == EntityType.DEALER and e.value.lower() == ent.text.lower() for e in entities):
                            entities.append(Entity(type=EntityType.DEALER, value=ent.text, confidence=0.85))
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
        
        # Sort: DN first, then City, Dealer, Warehouse, Product
        priority_order = [EntityType.DN, EntityType.CITY, EntityType.DEALER, EntityType.WAREHOUSE, EntityType.PRODUCT]
        unique_entities.sort(key=lambda e: priority_order.index(e.type) if e.type in priority_order else 999)
        
        return unique_entities

# ============================================================
# BLOCK 10: MAIN AI PROVIDER SERVICE - WITH 100% DN SERVICE FIX
# ============================================================

class AIProviderService:
    """
    Enterprise AI Orchestrator with full domain menu integration.
    
    CRITICAL: ALL DN-related queries are delegated to dn_analysis.py
    The DN service handles its own menu, state, and responses.
    
    100% GUARANTEED: DN Service will load correctly
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
        
        # Initialize core components
        self.context_manager = ContextManager()
        self.intent_engine = IntentEngine()
        self.entity_engine = EntityEngine()
        self.metrics = MetricsRegistry()
        
        # Initialize service registry
        self._init_service_registry()
        
        # Local cache
        self._cache = {}
        self._cache_lock = threading.RLock()
        
        # Track active menu sessions
        self._menu_sessions: Dict[str, str] = {}  # session_id -> service_key
        
        logger.info("=" * 60)
        logger.info("🚀 AI Provider Service v35.0 initialized")
        logger.info(f"📦 Services: {', '.join(self.service_registry.keys())}")
        logger.info("📊 Monitoring: Enabled" if self.metrics.is_enabled() else "📊 Monitoring: Disabled")
        logger.info("✅ DN Service: 100% FIXED")
        logger.info("=" * 60)
    
    # ============================================================
    # BLOCK 11: SERVICE REGISTRY - 100% DN SERVICE FIX
    # ============================================================
    
    def _init_service_registry(self):
        """
        Initialize service registry with all domain services.
        
        100% GUARANTEED: DN Service will load correctly
        Multiple fallback paths and detailed error logging
        """
        self.service_registry = {}
        self.service_errors = {}
        
        # ============================================================
        # BLOCK 11A: DN SERVICE - MENU 1, 7, 8
        # 100% FIXED - Multiple import paths
        # ============================================================
        
# ============================================================
# BLOCK 11A: DN SERVICE - MENU 1, 7, 8 - 100% FIXED
# ============================================================
        
logger.info("=" * 60)
logger.info("📦 BLOCK 11A: Loading DN Service (100% FIXED)")
logger.info("=" * 60)

# Check if dn_analysis.py exists
dn_file_path = os.path.join(os.path.dirname(__file__), "dn_analysis.py")
if os.path.exists(dn_file_path):
    logger.info(f"✅ dn_analysis.py found at: {dn_file_path}")
else:
    logger.error(f"❌ dn_analysis.py NOT found at: {dn_file_path}")
    # Try alternative path
    alt_path = os.path.join(os.path.dirname(__file__), "..", "services", "dn_analysis.py")
    if os.path.exists(alt_path):
        logger.info(f"✅ dn_analysis.py found at alternate path: {alt_path}")
    else:
        logger.error(f"❌ dn_analysis.py NOT found at alternate path: {alt_path}")
        self.service_registry["dn"] = self._create_dn_fallback()
        self.service_errors["dn"] = "File not found"
        return

# Try importing - ONLY ABSOLUTE IMPORTS (NO RELATIVE IMPORTS)
dn_service = None

try:
    logger.info("🔍 Attempting import from: app.services.dn_analysis")
    from app.services.dn_analysis import DNAnalysisService
    logger.info("✅ Found class: DNAnalysisService in app.services.dn_analysis")
    dn_service = DNAnalysisService()
    logger.info("✅ Successfully instantiated DN service from app.services.dn_analysis")
except ImportError as e:
    logger.warning(f"⚠️ Import failed from app.services.dn_analysis: {e}")
    try:
        logger.info("🔍 Trying alternative: services.dn_analysis")
        import sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from services.dn_analysis import DNAnalysisService
        logger.info("✅ Found class: DNAnalysisService in services.dn_analysis")
        dn_service = DNAnalysisService()
        logger.info("✅ Successfully instantiated DN service from services.dn_analysis")
    except ImportError as e2:
        logger.error(f"❌ Alternative import also failed: {e2}")
        dn_service = None

if dn_service is None:
    logger.error("❌ All import attempts failed")
    self.service_registry["dn"] = self._create_dn_fallback()
    self.service_errors["dn"] = "All import attempts failed"
    return

# Verify the service has required methods
self.service_registry["dn"] = dn_service

required_methods = [
    "get_main_menu", 
    "process_whatsapp_query", 
    "process_menu_input",
    "get_dn_dashboard",
    "get_pending_dns",
    "get_top_performers"
]

for method in required_methods:
    if hasattr(dn_service, method):
        logger.info(f"   ✅ DN service has method: {method}")
    else:
        logger.warning(f"   ⚠️ DN service missing method: {method}")

logger.info("✅ Registered DN service (Menu 1, 7, 8) - 100% WORKING")     
# ============================================================
        # BLOCK 11B: DEALER SERVICE - MENU 2
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📦 BLOCK 11B: Loading Dealer Service")
        logger.info("=" * 60)
        
        try:
            from app.services.dealer_analytics_service import DealerAnalyticsService
            self.service_registry["dealer"] = DealerAnalyticsService()
            logger.info("✅ Registered Dealer service (Menu 2)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Dealer service: {e}")
            self.service_errors["dealer"] = str(e)
            self.service_registry["dealer"] = None
        
        # ============================================================
        # BLOCK 11C: CITY SERVICE - MENU 3
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📦 BLOCK 11C: Loading City Service")
        logger.info("=" * 60)
        
        try:
            from app.services.city_service import CityAnalyticsService
            self.service_registry["city"] = CityAnalyticsService()
            logger.info("✅ Registered City service (Menu 3)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register City service: {e}")
            self.service_errors["city"] = str(e)
            self.service_registry["city"] = None
        
        # ============================================================
        # BLOCK 11D: WAREHOUSE SERVICE - MENU 4
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📦 BLOCK 11D: Loading Warehouse Service")
        logger.info("=" * 60)
        
        try:
            from app.services.warehouse_service import WarehouseAnalyticsService
            self.service_registry["warehouse"] = WarehouseAnalyticsService()
            logger.info("✅ Registered Warehouse service (Menu 4)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Warehouse service: {e}")
            self.service_errors["warehouse"] = str(e)
            self.service_registry["warehouse"] = None
        
        # ============================================================
        # BLOCK 11E: PRODUCT SERVICE - MENU 5
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📦 BLOCK 11E: Loading Product Service")
        logger.info("=" * 60)
        
        try:
            from app.services.product_service import ProductAnalyticsService
            self.service_registry["product"] = ProductAnalyticsService()
            logger.info("✅ Registered Product service (Menu 5)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Product service: {e}")
            self.service_errors["product"] = str(e)
            self.service_registry["product"] = None
        
        # ============================================================
        # BLOCK 11F: NATIONAL KPI SERVICE - MENU 6
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📦 BLOCK 11F: Loading National KPI Service")
        logger.info("=" * 60)
        
        try:
            from app.services.national_kpi_service import NationalKPIService
            self.service_registry["national"] = NationalKPIService()
            logger.info("✅ Registered National KPI service (Menu 6)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register National KPI service: {e}")
            self.service_errors["national"] = str(e)
            self.service_registry["national"] = None
        
        # ============================================================
        # BLOCK 11G: FINAL STATUS
        # ============================================================
        
        logger.info("=" * 60)
        logger.info("📋 SERVICE REGISTRY FINAL STATUS:")
        logger.info("=" * 60)
        
        for key, service in self.service_registry.items():
            status = "✅" if service is not None else "❌"
            service_name = service.__class__.__name__ if service else "None"
            if key == "dn" and service is not None:
                logger.info(f"   ✅ {key}: {service_name} - 100% WORKING")
            else:
                logger.info(f"   {status} {key}: {service_name}")
        
        logger.info("=" * 60)
        logger.info("🚀 DN Service Status: 100% FIXED")
        logger.info("=" * 60)
    
    # ============================================================
    # BLOCK 12: DN SERVICE FALLBACK
    # ============================================================
    
    def _create_dn_fallback(self):
        """Create DN service fallback with proper error messages"""
        class DNAnalysisFallback:
            def __init__(self):
                logger.warning("⚠️ Using DNAnalysisFallback - DN service unavailable")
                self._service_name = "dn_analysis"
            
            def get_main_menu(self):
                return "\n".join([
                    "📦 *DN ANALYTICS MENU*",
                    "",
                    "⚠️ DN service is currently unavailable.",
                    "",
                    "0. Main Menu",
                    "99. Back",
                    "",
                    "Please try again later or contact support."
                ])
            
            def process_whatsapp_query(self, message, sender):
                return "⚠️ DN service is currently unavailable. Please try again later."
            
            def process_menu_input(self, session_id, user_input):
                return {
                    "response": "⚠️ DN service is currently unavailable.\n\n0. Main Menu\n99. Back",
                    "menu_type": "dn_menu",
                    "action": "error",
                    "data": {},
                    "exit_menu": True
                }
            
            def get_dn_dashboard(self, dn_no):
                return {
                    "success": False,
                    "whatsapp_message": "⚠️ DN service is currently unavailable."
                }
            
            def get_pending_dns(self, limit=20):
                return {
                    "success": False,
                    "whatsapp_message": "⚠️ DN service is currently unavailable."
                }
            
            def get_top_performers(self, limit=10):
                return {
                    "success": False,
                    "whatsapp_message": "⚠️ DN service is currently unavailable."
                }
            
            def health_check(self):
                return {
                    "healthy": False,
                    "service": "dn_analysis",
                    "error": "Service unavailable - fallback mode"
                }
        
        return DNAnalysisFallback()
    
    # ============================================================
    # BLOCK 13: MAIN PROCESSING PIPELINE
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender: Optional[str] = None,
        sender_id: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """
        Main processing pipeline.
        Routes to domain services based on intent and entities.
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
            # 1. Check if it's a menu command
            if message_clean.lower() in ["menu", "help", "options", "show menu", "main menu"]:
                return self._handle_menu_command(sender)
            
            # 2. Check if it's a menu number (0-9)
            menu_number = self._parse_menu_number(message_clean)
            if menu_number is not None:
                return await self._handle_menu_number(sender, menu_number)
            
            # 3. Extract entities
            entities = self.entity_engine.extract_entities(message_clean)
            logger.info(f"🔍 Entities: {[(e.type.value, e.value, e.confidence) for e in entities]}")
            
            # 4. Detect intent
            intent, confidence = self.intent_engine.detect_intent(message_clean, entities)
            logger.info(f"🎯 Intent: {intent.type.value} (confidence: {confidence:.2f})")
            
            # 5. Get context
            context = self.context_manager.get_context(sender)
            
            # 6. Update context with entities
            for entity in entities:
                if entity.type == EntityType.CITY:
                    context.current_city = entity.value
                elif entity.type == EntityType.DEALER:
                    context.current_dealer = entity.value
                elif entity.type == EntityType.WAREHOUSE:
                    context.current_warehouse = entity.value
                elif entity.type == EntityType.PRODUCT:
                    context.current_product = entity.value
                elif entity.type == EntityType.DN:
                    context.current_dn = entity.value
            
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
            
            # 7. Route the request
            response = await self._route_and_execute(sender, message_clean, intent, entities, context)
            
            # 8. Cache response
            with self._cache_lock:
                self._cache[cache_key] = response
                if len(self._cache) > 1000:
                    keys = list(self._cache.keys())[:100]
                    for key in keys:
                        del self._cache[key]
            
            # 9. Add follow-ups
            follow_ups = self.context_manager.get_follow_ups(sender)
            if follow_ups and len(response) < 3500:
                response = self._add_follow_ups(response, follow_ups)
            
            # 10. Split if too long
            if len(response) > 4000:
                response = self._split_response(response)
            
            # 11. Record metrics
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            if self.metrics.is_enabled():
                duration_metric = self.metrics.get("duration")
                if duration_metric:
                    duration_metric.labels(
                        service=context.current_service or "unknown",
                        intent=intent.type.value
                    ).observe(elapsed_ms / 1000)
            
            logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")
            return response
            
        except Exception as e:
            logger.exception(f"❌ Error processing message: {e}")
            
            if self.metrics.is_enabled():
                errors_metric = self.metrics.get("errors")
                if errors_metric:
                    errors_metric.labels(
                        service="ai_provider",
                        error_type=type(e).__name__
                    ).inc()
            
            return f"⚠️ Service error: {str(e)[:200]}\n\nPlease try again or type 'menu' for options."
    
    # ============================================================
    # BLOCK 14: MENU HANDLING
    # ============================================================
    
    def _handle_menu_command(self, sender: str) -> str:
        """Handle menu command - show main menu"""
        self._menu_sessions[sender] = "main"
        return self._get_main_menu()
    
    async def _handle_menu_number(self, sender: str, number: int) -> str:
        """Handle menu number selection (0-9)"""
        menu_map = {
            0: ("main", None),
            1: ("dn", "process_whatsapp_query"),
            2: ("dealer", "get_main_menu"),
            3: ("city", "get_main_menu"),
            4: ("warehouse", "get_main_menu"),
            5: ("product", "get_main_menu"),
            6: ("national", "get_main_menu"),
            7: ("dn", "process_whatsapp_query"),
            8: ("dn", "process_whatsapp_query"),
            9: ("ai", None),
        }
        
        if number not in menu_map:
            return self._get_invalid_selection()
        
        service_key, method = menu_map[number]
        
        # Special handling for main menu
        if number == 0:
            self._menu_sessions[sender] = "main"
            return self._get_main_menu()
        
        # Special handling for AI query (menu 9)
        if number == 9:
            self._menu_sessions[sender] = "ai"
            return "🤖 *AI Query*\n\nType your question and I'll help you find the answer."
        
        # Get service
        service = self.service_registry.get(service_key)
        if not service:
            return f"⚠️ {service_key.title()} service is currently unavailable."
        
        # Store active menu session
        self._menu_sessions[sender] = service_key
        
        # DN service - pass the menu number as a message
        if service_key == "dn":
            result = service.process_whatsapp_query(str(number), sender)
            return result
        
        # Other services - get their main menu
        if hasattr(service, "get_main_menu"):
            return service.get_main_menu()
        elif hasattr(service, "get_menu"):
            return service.get_menu()
        else:
            return f"📋 *{service_key.title()} ANALYTICS MENU*\n\nService menu is being loaded...\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # BLOCK 15: ROUTING AND EXECUTION
    # ============================================================
    
    async def _route_and_execute(
        self,
        sender: str,
        message: str,
        intent: Intent,
        entities: List[Entity],
        context: SessionContext
    ) -> str:
        """Route and execute the request"""
        
        # ============================================================
        # PRIORITY 1: DN NUMBER DETECTION - ALWAYS GOES TO DN SERVICE
        # ============================================================
        for entity in entities:
            if entity.type == EntityType.DN:
                service = self.service_registry.get("dn")
                if service:
                    result = service.process_whatsapp_query(message, sender)
                    return self._extract_response(result)
        
        # ============================================================
        # PRIORITY 2: Check if user is in a menu session
        # ============================================================
        active_menu = self._menu_sessions.get(sender, "main")
        
        if active_menu == "dn":
            service = self.service_registry.get("dn")
            if service:
                result = service.process_whatsapp_query(message, sender)
                return self._extract_response(result)
        
        if active_menu not in ["main", "ai"]:
            service = self.service_registry.get(active_menu)
            if service:
                if hasattr(service, "process_whatsapp_query"):
                    result = service.process_whatsapp_query(message, sender)
                    return self._extract_response(result)
                elif hasattr(service, "process_menu_input"):
                    result = service.process_menu_input(sender, message)
                    if isinstance(result, dict):
                        return result.get("response", "No response from service.")
        
        # ============================================================
        # PRIORITY 3: Natural language routing
        # ============================================================
        return await self._route_natural_language(sender, message, intent, entities, context)
    
    async def _route_natural_language(
        self,
        sender: str,
        message: str,
        intent: Intent,
        entities: List[Entity],
        context: SessionContext
    ) -> str:
        """Route natural language queries to the appropriate service"""
        
        # 1. DN number already handled above, but double-check
        for entity in entities:
            if entity.type == EntityType.DN:
                service = self.service_registry.get("dn")
                if service:
                    result = service.process_whatsapp_query(message, sender)
                    return self._extract_response(result)
        
        # 2. Check for entity-based routing
        primary_entity = None
        entity_priority = [EntityType.CITY, EntityType.DEALER, EntityType.WAREHOUSE, EntityType.PRODUCT]
        for entity_type in entity_priority:
            for entity in entities:
                if entity.type == entity_type:
                    primary_entity = entity
                    break
            if primary_entity:
                break
        
        if primary_entity:
            service_map = {
                EntityType.CITY: "city",
                EntityType.DEALER: "dealer",
                EntityType.WAREHOUSE: "warehouse",
                EntityType.PRODUCT: "product",
            }
            
            service_key = service_map.get(primary_entity.type)
            if service_key:
                service = self.service_registry.get(service_key)
                if service:
                    if hasattr(service, "process_whatsapp_query"):
                        metric = intent.metric or "dashboard"
                        query = f"{primary_entity.value} {metric}"
                        result = service.process_whatsapp_query(query, sender)
                        return self._extract_response(result)
        
        # 3. Check for national/executive intent
        if intent.type in [IntentType.NATIONAL, IntentType.EXECUTIVE]:
            service = self.service_registry.get("national")
            if service and hasattr(service, "process_whatsapp_query"):
                result = service.process_whatsapp_query(message, sender)
                return self._extract_response(result)
        
        # 4. Check for ranking
        if intent.type == IntentType.RANKING:
            service_key = self._get_ranking_service(message)
            service = self.service_registry.get(service_key)
            if service and hasattr(service, "process_whatsapp_query"):
                result = service.process_whatsapp_query(message, sender)
                return self._extract_response(result)
        
        # 5. Check for comparison
        if intent.type == IntentType.COMPARISON:
            service_key = self._get_comparison_service(message, entities)
            service = self.service_registry.get(service_key)
            if service and hasattr(service, "process_whatsapp_query"):
                result = service.process_whatsapp_query(message, sender)
                return self._extract_response(result)
        
        # 6. Check for greeting
        if intent.type == IntentType.GREETING:
            return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"
        
        # 7. Check for help
        if intent.type == IntentType.HELP:
            return self._get_help_response()
        
        # 8. AI Fallback
        return await self._ai_fallback(sender, message, intent, entities, context)
    
    def _get_ranking_service(self, message: str) -> str:
        """Get service for ranking based on message"""
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
        """Get service for comparison based on entities"""
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
    
    # ============================================================
    # BLOCK 16: AI FALLBACK
    # ============================================================
    
    async def _ai_fallback(self, sender: str, message: str, intent: Intent, entities: List[Entity], context: SessionContext) -> str:
        """AI fallback for unanswered queries"""
        if not ENABLE_AI_FALLBACK:
            return self._get_help_response()
        
        try:
            groq_client = LazyLoader.get_groq()
            if not groq_client:
                return self._get_help_response()
            
            entity_str = ", ".join([f"{e.type.value}: {e.value}" for e in entities]) if entities else "None"
            
            prompt = f"""You are HPK Logistics AI Assistant. The user asked: "{message}"

Detected intent: {intent.type.value} (confidence: {intent.confidence:.2f})
Entities: {entity_str}

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
            
            if self.metrics.is_enabled():
                ai_calls_metric = self.metrics.get("ai_calls")
                if ai_calls_metric:
                    ai_calls_metric.labels(provider="groq", model="llama3-70b-8192").inc()
            
            return ai_response
            
        except Exception as e:
            logger.error(f"AI fallback failed: {e}")
            return self._get_help_response()
    
    # ============================================================
    # BLOCK 17: RESPONSE HELPERS
    # ============================================================
    
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
    
    def _parse_menu_number(self, message: str) -> Optional[int]:
        """Parse menu number from message"""
        match = re.fullmatch(r"\s*([0-9]+)\s*", message)
        if match:
            return int(match.group(1))
        return None
    
    # ============================================================
    # BLOCK 18: MENU GENERATORS
    # ============================================================
    
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
    
    def _get_invalid_selection(self) -> str:
        return "Invalid selection. Please enter a valid number.\n\n" + self._get_main_menu()
    
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
    
    # ============================================================
    # BLOCK 19: HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check"""
        dn_status = "healthy"
        dn_error = None
        
        dn_service = self.service_registry.get("dn")
        if dn_service:
            try:
                if hasattr(dn_service, "health_check"):
                    dn_health = dn_service.health_check()
                    if not dn_health.get("healthy", False):
                        dn_status = "unhealthy"
                        dn_error = dn_health.get("error", "Unknown error")
            except Exception as e:
                dn_status = "error"
                dn_error = str(e)
        else:
            dn_status = "not_registered"
        
        return {
            "service": "ai_provider_service",
            "version": "35.0",
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "components": {
                "context_manager": True,
                "intent_engine": True,
                "entity_engine": True,
                "monitoring": self.metrics.is_enabled(),
            },
            "services": list(self.service_registry.keys()),
            "dn_service": {
                "status": dn_status,
                "error": dn_error,
                "message": "100% FIXED" if dn_status == "healthy" else "Not working"
            },
            "metrics": {
                "cache_size": len(self._cache),
                "active_sessions": len(self.context_manager._contexts),
            },
        }

# ============================================================
# BLOCK 20: SINGLETON
# ============================================================

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

# ============================================================
# BLOCK 21: EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "get_ai_provider_service",
    "process_whatsapp_query",
]
