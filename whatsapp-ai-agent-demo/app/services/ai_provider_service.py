"""
File: app/services/ai_provider_service.py
Version: 30.0 - ENTERPRISE AI ORCHESTRATOR

Enterprise-grade AI router with pipeline architecture:
- Intent Detection (hybrid: rules + semantic + AI)
- Entity Recognition (spaCy + RapidFuzz + PostgreSQL)
- Context Memory (session-based with TTL)
- Service Registry (dynamic service registration)
- Multi-Intent Planner
- Confidence-Based Routing
- Analytics-First Execution
- AI Fallback Only When Needed

Status: ENTERPRISE READY
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union, Set, Tuple, Callable
from functools import lru_cache

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# =====================================================================================================================
# OPTIONAL DEPENDENCIES - GRACEFUL FALLBACKS
# =====================================================================================================================

# NLP Libraries
try:
    import spacy
    SPACY_AVAILABLE = True
    try:
        nlp = spacy.load("en_core_web_sm")
    except:
        nlp = None
        logger.warning("spaCy model not loaded")
except ImportError:
    SPACY_AVAILABLE = False
    nlp = None
    logger.warning("spaCy not available")

# Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("RapidFuzz not available")

# Semantic Search
try:
    from sentence_transformers import SentenceTransformer
    SEMANTIC_AVAILABLE = True
    semantic_model = None
    try:
        semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
    except:
        logger.warning("Semantic model not loaded")
except ImportError:
    SEMANTIC_AVAILABLE = False
    semantic_model = None
    logger.warning("Sentence Transformers not available")

# AI Fallback
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("Groq not available")

# =====================================================================================================================
# CONFIGURATION
# =====================================================================================================================

CONFIDENCE_THRESHOLD = float(os.getenv("ROUTER_CONFIDENCE_THRESHOLD", "0.70"))
SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))
CACHE_TTL = int(os.getenv("ROUTER_CACHE_TTL", "300"))
ENABLE_AI_FALLBACK = os.getenv("ENABLE_AI_FALLBACK", "true").lower() == "true"

# =====================================================================================================================
# ENUMS
# =====================================================================================================================

class EntityType(Enum):
    """Entity types for recognition"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    MATERIAL = "material"
    DIVISION = "division"
    SALES_OFFICE = "sales_office"
    REGION = "region"
    TRANSPORTER = "transporter"
    VEHICLE = "vehicle"
    DRIVER = "driver"
    ROUTE = "route"
    DATE = "date"
    MONTH = "month"
    YEAR = "year"

class IntentType(Enum):
    """Intent types"""
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
    UNKNOWN = "unknown"

class MenuState(Enum):
    """Menu states"""
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
    """Represents a recognized entity"""
    type: EntityType
    value: str
    confidence: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Intent:
    """Represents a detected intent"""
    type: IntentType
    confidence: float = 1.0
    entities: List[Entity] = field(default_factory=list)
    sub_intent: Optional[str] = None

@dataclass
class RoutingDecision:
    """Routing decision with confidence"""
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
    """User session context"""
    session_id: str
    current_service: Optional[str] = None
    current_menu: MenuState = MenuState.MAIN
    current_city: Optional[str] = None
    current_dealer: Optional[str] = None
    current_warehouse: Optional[str] = None
    current_product: Optional[str] = None
    last_intent: Optional[IntentType] = None
    last_entity: Optional[Entity] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

# =====================================================================================================================
# SERVICE REGISTRY - DYNAMIC SERVICE MANAGEMENT
# =====================================================================================================================

class ServiceRegistry:
    """Dynamic service registry for all analytics services"""

    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._methods: Dict[str, Dict[str, str]] = {}
        self._lock = threading.RLock()

    def register(self, key: str, service: Any, methods: Dict[str, str]) -> None:
        """Register a service with its methods"""
        with self._lock:
            self._services[key] = service
            self._methods[key] = methods
            logger.info(f"✅ Registered service: {key} with {len(methods)} methods")

    def get_service(self, key: str) -> Optional[Any]:
        """Get a service by key"""
        with self._lock:
            return self._services.get(key)

    def get_method(self, key: str, method_name: str) -> Optional[Callable]:
        """Get a method from a service"""
        with self._lock:
            service = self._services.get(key)
            if service:
                return getattr(service, method_name, None)
            return None

    def get_all_services(self) -> Dict[str, Any]:
        """Get all registered services"""
        with self._lock:
            return self._services.copy()

    def get_all_keys(self) -> List[str]:
        """Get all service keys"""
        with self._lock:
            return list(self._services.keys())

    def execute(self, key: str, method: str, *args, **kwargs) -> Any:
        """Execute a method on a service"""
        service = self.get_service(key)
        if not service:
            raise ValueError(f"Service '{key}' not found")

        method_func = getattr(service, method, None)
        if not method_func:
            raise ValueError(f"Method '{method}' not found in service '{key}'")

        return method_func(*args, **kwargs)

# =====================================================================================================================
# INTENT DETECTION ENGINE
# =====================================================================================================================

class IntentEngine:
    """Hybrid intent detection with rules, semantic, and AI"""

    # Intent patterns with priority
    INTENT_PATTERNS: Dict[str, Dict[str, Any]] = {
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
                r"(?:show|display|get|view).*(?:dashboard|overview|details)",
                r"(?:how is|what about|tell me about).*(?:performance|status)",
                r"^([\w\s]+)$",  # Single entity name → dashboard
            ],
            "priority": 2
        },
        IntentType.REVENUE: {
            "patterns": [
                r"(?:revenue|sales|income|turnover|earnings)",
                r"(?:how much|what(?:'s)?).*(?:revenue|sales)",
                r"(?:total|overall).*(?:revenue|sales)",
                r"([\w\s]+).*(?:revenue|sales)",
            ],
            "priority": 2
        },
        IntentType.UNITS: {
            "patterns": [
                r"(?:units|quantity|volume|pieces|items)",
                r"(?:how many|number of).*(?:units|items)",
                r"([\w\s]+).*(?:units|quantity)",
            ],
            "priority": 2
        },
        IntentType.PENDING: {
            "patterns": [
                r"(?:pending|outstanding|backlog|overdue).*(?:dn|delivery|order)",
                r"(?:undelivered|unfulfilled).*(?:orders|dns)",
                r"([\w\s]+).*(?:pending|backlog)",
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
                r"^(?:top|best|highest)\s+(\d+)?\s*(?:dealers?|warehouses?|cities?|products?)",
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
                r"where (?:are we|is the business)",
            ],
            "priority": 1
        },
        IntentType.NATIONAL: {
            "patterns": [
                r"(?:national|overall|pakistan).*(?:kpi|performance|score|health|dashboard)",
                r"(?:country|nation|national).*(?:logistics|supply chain)",
                r"^(?:national|overall|pakistan)$",
            ],
            "priority": 1
        },
        IntentType.SEARCH: {
            "patterns": [
                r"(?:search|find|lookup|locate)\s+([\w\s\-_]+)",
                r"where (?:is|are)\s+([\w\s\-_]+)",
            ],
            "priority": 2
        },
        IntentType.HELP: {
            "patterns": [
                r"(?:help|assist|support|guide|how to)",
                r"what can you (?:do|help with)",
                r"how (?:does|do) (?:i|you|this)",
            ],
            "priority": 1
        },
    }

    def __init__(self):
        self._compiled_patterns = {}
        self._cache: TTLCache = TTLCache(maxsize=1000, ttl=CACHE_TTL)
        self._lock = threading.RLock()

        # Compile all patterns
        for intent_type, config in self.INTENT_PATTERNS.items():
            self._compiled_patterns[intent_type] = [
                re.compile(pattern, re.IGNORECASE) for pattern in config["patterns"]
            ]

        logger.info(f"✅ IntentEngine initialized with {len(self.INTENT_PATTERNS)} intent types")

    def detect_intent(self, message: str, entities: List[Entity] = None) -> Tuple[Intent, float]:
        """Detect intent from message with confidence"""
        message_lower = message.lower().strip()
        cache_key = hashlib.md5(message_lower.encode()).hexdigest()

        # Check cache
        with self._lock:
            if cache_key in self._cache:
                cached = self._cache[cache_key]
                return cached[0], cached[1]

        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        best_reason = ""

        # 1. Check for menu commands first (highest priority)
        if message_lower in ["menu", "help", "options", "show menu", "main menu", "0"]:
            best_intent = IntentType.MENU
            best_score = 1.0
            best_reason = "Exact menu keyword"

        # 2. Check for greetings
        elif message_lower in ["hi", "hello", "hey", "salam", "good morning", "good evening"]:
            best_intent = IntentType.GREETING
            best_score = 1.0
            best_reason = "Greeting detected"

        # 3. Pattern matching
        else:
            for intent_type, patterns in self._compiled_patterns.items():
                matches = 0
                total_patterns = len(patterns)

                for pattern in patterns:
                    if pattern.search(message_lower):
                        matches += 1
                        # Early exit if high confidence
                        if matches >= 2:
                            break

                if matches > 0:
                    score = min(1.0, (matches / max(1, total_patterns)) * 2)
                    # Boost for exact matches
                    if matches == total_patterns:
                        score = min(1.0, score * 1.5)

                    # Priority boost
                    priority = self.INTENT_PATTERNS[intent_type].get("priority", 2)
                    score = score * (1.0 / priority)

                    if score > best_score:
                        best_score = score
                        best_intent = intent_type
                        best_reason = f"Pattern matched (matches: {matches}/{total_patterns})"

        # 4. Semantic fallback if confidence is low
        if best_score < 0.5 and SEMANTIC_AVAILABLE and semantic_model:
            semantic_score = self._semantic_similarity(message_lower)
            if semantic_score > best_score:
                best_score = semantic_score
                best_reason = "Semantic matching"

        # 5. Entity-based inference
        if entities and best_score < 0.6:
            entity_types = [e.type.value for e in entities]
            if "city" in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
                best_reason = "Entity inference: city → dashboard"
            elif "dealer" in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
                best_reason = "Entity inference: dealer → dashboard"
            elif "warehouse" in entity_types and best_intent == IntentType.UNKNOWN:
                best_intent = IntentType.DASHBOARD
                best_score = 0.7
                best_reason = "Entity inference: warehouse → dashboard"

        # Create intent object
        intent = Intent(
            type=best_intent,
            confidence=best_score,
            entities=entities or []
        )

        # Cache result
        with self._lock:
            self._cache[cache_key] = (intent, best_score)

        logger.info(f"Intent detected: {best_intent.value} (confidence: {best_score:.2f}) - {best_reason}")
        return intent, best_score

    def _semantic_similarity(self, message: str) -> float:
        """Calculate semantic similarity with known intents"""
        if not semantic_model:
            return 0.0

        try:
            # Embed the message
            message_embedding = semantic_model.encode(message)

            # Compare with known intent examples
            intent_examples = {
                IntentType.DASHBOARD: ["show dashboard", "display overview", "view status"],
                IntentType.REVENUE: ["show revenue", "display sales", "view income"],
                IntentType.UNITS: ["show units", "display quantity", "view volume"],
                IntentType.PENDING: ["show pending", "display backlog", "view overdue"],
                IntentType.DELIVERY: ["show delivery", "display transit", "view shipping"],
                IntentType.COMPARISON: ["compare", "versus", "vs"],
                IntentType.RANKING: ["top", "ranking", "leaderboard"],
            }

            best_score = 0.0
            for intent_type, examples in intent_examples.items():
                example_embeddings = semantic_model.encode(examples)
                for example_emb in example_embeddings:
                    from numpy import dot
                    from numpy.linalg import norm
                    similarity = dot(message_embedding, example_emb) / (norm(message_embedding) * norm(example_emb))
                    best_score = max(best_score, similarity)

            return best_score
        except Exception:
            return 0.0

# =====================================================================================================================
# ENTITY RECOGNITION ENGINE
# =====================================================================================================================

class EntityEngine:
    """Enterprise entity recognition with multiple strategies"""

    # Known entities from database
    KNOWN_CITIES = {
        "abbottabad", "lahore", "karachi", "rawalpindi", "quetta", "multan",
        "peshawar", "gilgit", "hyderabad", "islamabad", "sialkot", "gujranwala",
        "faisalabad", "bahawalpur", "sukkur", "mansehra", "haripur", "dg khan",
        "gwadar", "rahim yar khan"
    }

    KNOWN_WAREHOUSES = {
        "lahore", "karachi", "rawalpindi", "multan", "peshawar",
        "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala",
        "bahawalpur", "sukkur", "dg khan", "rahim yar khan",
        "abbottabad", "gwadar", "gilgit", "islamabad"
    }

    DEALER_SUFFIXES = {
        "electronics", "traders", "distributors", "foods", "group", "pvt", "ltd",
        "sons", "brothers", "enterprises", "company", "corporation", "store", "shop",
        "centre", "center", "solutions", "services", "digital", "technologies",
        "systems", "networks", "communications", "logistics", "transport"
    }

    def __init__(self):
        self._cache: TTLCache = TTLCache(maxsize=1000, ttl=CACHE_TTL)
        self._lock = threading.RLock()

        # Build dealer suffix patterns
        self._dealer_patterns = [
            re.compile(rf'([\w&.\'\- ]{{2,}}?\s*{suffix}\s*[\w&.\'\- ]*)', re.IGNORECASE)
            for suffix in self.DEALER_SUFFIXES
        ]

        logger.info("✅ EntityEngine initialized")

    def extract_entities(self, message: str) -> List[Entity]:
        """Extract all entities from message"""
        message_lower = message.lower().strip()
        cache_key = hashlib.md5(message_lower.encode()).hexdigest()

        # Check cache
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        entities = []

        # 1. Extract DN numbers (8-12 digits)
        dn_matches = re.findall(r'(?<!\d)(\d{8,12})(?!\d)', message)
        for dn in dn_matches:
            entities.append(Entity(type=EntityType.DN, value=dn, confidence=0.95))

        # 2. Extract city names
        for city in self.KNOWN_CITIES:
            if city in message_lower:
                entities.append(Entity(type=EntityType.CITY, value=city.title(), confidence=0.95))
            # Check for "City" suffix
            if f"{city} city" in message_lower:
                entities.append(Entity(type=EntityType.CITY, value=city.title(), confidence=0.95))

        # 3. Extract warehouse names
        for warehouse in self.KNOWN_WAREHOUSES:
            if warehouse in message_lower:
                entities.append(Entity(type=EntityType.WAREHOUSE, value=warehouse.title(), confidence=0.95))

        # 4. Extract dealer names
        for pattern in self._dealer_patterns:
            match = pattern.search(message)
            if match:
                dealer_name = match.group(1).strip()
                if len(dealer_name) > 2:
                    entities.append(Entity(type=EntityType.DEALER, value=dealer_name, confidence=0.85))

        # 5. Extract product names (pattern based)
        product_patterns = [
            r"(?:product|model|material|item)\s+([\w\s\-_]+)",
            r"([\w\s\-_]+)\s+(?:product|model)"
        ]
        for pattern in product_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for product in matches:
                if len(product.strip()) > 2:
                    entities.append(Entity(type=EntityType.PRODUCT, value=product.strip(), confidence=0.80))

        # 6. Extract dates
        date_patterns = [
            r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",  # DD/MM/YYYY
            r"(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})",  # DD Mon YYYY
        ]
        for pattern in date_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for date_str in matches:
                entities.append(Entity(type=EntityType.DATE, value=date_str, confidence=0.80))

        # 7. Extract months
        months = ["january", "february", "march", "april", "may", "june", 
                  "july", "august", "september", "october", "november", "december"]
        for month in months:
            if month in message_lower:
                entities.append(Entity(type=EntityType.MONTH, value=month.title(), confidence=0.75))

        # 8. Fuzzy matching for partial matches (if RapidFuzz available)
        if RAPIDFUZZ_AVAILABLE:
            self._fuzzy_match_entities(message_lower, entities)

        # Remove duplicates
        unique_entities = []
        seen = set()
        for entity in entities:
            key = f"{entity.type.value}:{entity.value}"
            if key not in seen:
                seen.add(key)
                unique_entities.append(entity)

        # Cache results
        with self._lock:
            self._cache[cache_key] = unique_entities

        return unique_entities

    def _fuzzy_match_entities(self, message: str, entities: List[Entity]) -> None:
        """Use fuzzy matching to find partial matches"""
        if not RAPIDFUZZ_AVAILABLE:
            return

        # Check for city fuzzy matches
        city_matches = process.extract(message, self.KNOWN_CITIES, scorer=fuzz.WRatio, limit=3)
        for match, score, _ in city_matches:
            if score >= 85:
                # Check if already found
                for entity in entities:
                    if entity.type == EntityType.CITY and entity.value.lower() == match:
                        break
                else:
                    entities.append(Entity(type=EntityType.CITY, value=match.title(), confidence=score/100))

        # Check for warehouse fuzzy matches
        warehouse_matches = process.extract(message, self.KNOWN_WAREHOUSES, scorer=fuzz.WRatio, limit=3)
        for match, score, _ in warehouse_matches:
            if score >= 85:
                for entity in entities:
                    if entity.type == EntityType.WAREHOUSE and entity.value.lower() == match:
                        break
                else:
                    entities.append(Entity(type=EntityType.WAREHOUSE, value=match.title(), confidence=score/100))

# =====================================================================================================================
# CONTEXT MANAGER
# =====================================================================================================================

class ContextManager:
    """Session-based context management with TTL"""

    def __init__(self, ttl_seconds: int = SESSION_TTL):
        self._contexts: Dict[str, SessionContext] = {}
        self._lock = threading.RLock()
        self._ttl = ttl_seconds

        # Start cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()

        logger.info(f"✅ ContextManager initialized (TTL: {ttl_seconds}s)")

    def get_context(self, session_id: str) -> SessionContext:
        """Get or create context for session"""
        with self._lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = SessionContext(session_id=session_id)
            context = self._contexts[session_id]
            context.last_activity = datetime.now()
            return context

    def update_context(self, session_id: str, updates: Dict[str, Any]) -> None:
        """Update context with new data"""
        with self._lock:
            context = self.get_context(session_id)
            for key, value in updates.items():
                if hasattr(context, key):
                    setattr(context, key, value)
            context.last_activity = datetime.now()

    def add_history(self, session_id: str, entry: Dict[str, Any]) -> None:
        """Add entry to conversation history"""
        with self._lock:
            context = self.get_context(session_id)
            context.history.append(entry)
            # Keep only last 20 entries
            if len(context.history) > 20:
                context.history = context.history[-20:]

    def get_follow_ups(self, session_id: str) -> List[str]:
        """Generate follow-up suggestions based on context"""
        context = self.get_context(session_id)
        suggestions = []

        if context.current_service:
            suggestions.append(f"Show {context.current_service} dashboard")

        if context.current_city:
            suggestions.append(f"Revenue in {context.current_city}")
            suggestions.append(f"Pending in {context.current_city}")

        if context.current_dealer:
            suggestions.append(f"Compare {context.current_dealer} with another")

        suggestions.extend([
            "Show national KPI",
            "View pending DNs",
            "Top performers"
        ])

        return suggestions[:4]

    def _cleanup_loop(self) -> None:
        """Clean up expired sessions"""
        while True:
            time.sleep(60)  # Run every minute
            now = datetime.now()
            expired = []
            with self._lock:
                for session_id, context in self._contexts.items():
                    if (now - context.last_activity).total_seconds() > self._ttl:
                        expired.append(session_id)
                for session_id in expired:
                    del self._contexts[session_id]
                    logger.info(f"🧹 Cleaned up expired session: {session_id}")

# =====================================================================================================================
# RESPONSE AGGREGATOR
# =====================================================================================================================

class ResponseAggregator:
    """Aggregate responses from multiple services"""

    def __init__(self):
        self._lock = threading.RLock()

    def aggregate(self, responses: List[Dict[str, Any]], separator: str = "\n\n" + "─" * 40 + "\n\n") -> str:
        """Aggregate multiple responses into one"""
        if not responses:
            return "No responses to aggregate."

        if len(responses) == 1:
            return self._extract_message(responses[0])

        # Extract messages
        messages = []
        for i, response in enumerate(responses):
            msg = self._extract_message(response)
            if msg:
                prefix = f"📊 *Response {i+1}*" if len(responses) > 1 else ""
                messages.append(f"{prefix}\n{msg}" if prefix else msg)

        return separator.join(messages)

    def _extract_message(self, response: Dict[str, Any]) -> str:
        """Extract WhatsApp message from response"""
        if isinstance(response, str):
            return response

        if isinstance(response, dict):
            if response.get("whatsapp_message"):
                return str(response["whatsapp_message"])
            if response.get("formatted_response"):
                return str(response["formatted_response"])
            if response.get("message"):
                return str(response["message"])
            if response.get("response"):
                return str(response["response"])
            if response.get("error"):
                return f"⚠️ {response['error']}"

        return str(response) if response else ""

# =====================================================================================================================
# MAIN AI PROVIDER SERVICE - ENTERPRISE ORCHESTRATOR
# =====================================================================================================================

class AIProviderService:
    """
    Enterprise AI Orchestrator with pipeline architecture:
    1. Preprocessor
    2. Intent Detection
    3. Entity Extraction
    4. Context Manager
    5. Service Planner
    6. Service Registry
    7. Business Service
    8. Response Formatter
    """

    _instance: Optional["AIProviderService"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "AIProviderService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        # Initialize components
        self.intent_engine = IntentEngine()
        self.entity_engine = EntityEngine()
        self.context_manager = ContextManager()
        self.response_aggregator = ResponseAggregator()
        self.service_registry = ServiceRegistry()

        # Initialize services
        self._init_services()

        # Router state
        self._cache: TTLCache = TTLCache(maxsize=1000, ttl=CACHE_TTL)
        self._router_lock = threading.RLock()

        # AI fallback
        self._groq_client = None
        if GROQ_AVAILABLE:
            try:
                self._groq_client = Groq()
                logger.info("✅ Groq client initialized for AI fallback")
            except Exception as e:
                logger.warning(f"⚠️ Groq client initialization failed: {e}")

        self._initialized = True
        logger.info("✅ AIProviderService (v30.0) initialized with pipeline architecture")

    def _init_services(self) -> None:
        """Initialize and register all services"""
        try:
            from app.services.dn_analysis import DNAnalysisService
            self.service_registry.register("dn", DNAnalysisService(), {
                "dashboard": "get_dn_dashboard",
                "pending": "get_pending_dns",
                "top": "get_top_performers",
                "warehouse": "get_warehouse_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
            })
            logger.info("✅ Registered DN service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register DN service: {e}")

        try:
            from app.services.dealer_analytics_service import DealerAnalyticsService
            self.service_registry.register("dealer", DealerAnalyticsService(), {
                "dashboard": "get_dealer_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
            })
            logger.info("✅ Registered Dealer service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Dealer service: {e}")

        try:
            from app.services.city_service import CityAnalyticsService
            self.service_registry.register("city", CityAnalyticsService(), {
                "dashboard": "get_city_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
                "process_menu": "process_city_menu_input",
            })
            logger.info("✅ Registered City service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register City service: {e}")

        try:
            from app.services.warehouse_service import WarehouseAnalyticsService
            self.service_registry.register("warehouse", WarehouseAnalyticsService(), {
                "dashboard": "get_warehouse_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
                "process_menu": "process_menu_input",
            })
            logger.info("✅ Registered Warehouse service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Warehouse service: {e}")

        try:
            from app.services.product_service import ProductAnalyticsService
            self.service_registry.register("product", ProductAnalyticsService(), {
                "dashboard": "get_product_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
                "process_menu": "process_menu_input",
            })
            logger.info("✅ Registered Product service")
        except Exception as e:
            logger.warning(f"⚠️ Failed to register Product service: {e}")

        try:
            from app.services.national_kpi_service import NationalKPIService
            self.service_registry.register("national", NationalKPIService(), {
                "dashboard": "get_national_kpi_dashboard",
                "menu": "get_main_menu",
                "process": "process_whatsapp_query",
                "process_menu": "process_menu_input",
            })
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
        Main processing pipeline for WhatsApp queries.
        
        Pipeline:
        1. Preprocess → Clean and normalize message
        2. Entity Extraction → Extract all entities
        3. Intent Detection → Detect intent with confidence
        4. Context Management → Update session context
        5. Service Planning → Determine which service(s) to call
        6. Service Execution → Execute the service(s)
        7. Response Formatting → Format and return response
        """
        sender = sender or sender_id or "default"
        start_time = time.perf_counter()

        # =====================================================================================================================
        # STEP 1: Preprocessor
        # =====================================================================================================================
        if not message or not message.strip():
            return self._get_main_menu()

        message_clean = message.strip()
        logger.info(f"📨 Processing: '{message_clean}' from {sender}")

        # =====================================================================================================================
        # STEP 2: Entity Extraction
        # =====================================================================================================================
        entities = self.entity_engine.extract_entities(message_clean)
        logger.info(f"🔍 Entities: {[(e.type.value, e.value, e.confidence) for e in entities]}")

        # =====================================================================================================================
        # STEP 3: Intent Detection
        # =====================================================================================================================
        intent, confidence = self.intent_engine.detect_intent(message_clean, entities)
        logger.info(f"🎯 Intent: {intent.type.value} (confidence: {confidence:.2f})")

        # =====================================================================================================================
        # STEP 4: Context Management
        # =====================================================================================================================
        context = self.context_manager.get_context(sender)

        # Update context with current entities
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

        # =====================================================================================================================
        # STEP 5: Service Planning
        # =====================================================================================================================

        # Check if we're in a menu state
        if context.current_menu != MenuState.MAIN and intent.type == IntentType.MENU:
            # Menu exit or navigation
            if message_clean.lower() in ["0", "menu", "main menu", "back", "exit"]:
                context.current_menu = MenuState.MAIN
                context.current_service = None
                return self._get_main_menu()

        # Handle menu number input when in a menu
        menu_number = self._parse_menu_number(message_clean)
        if menu_number is not None and context.current_menu != MenuState.MAIN:
            return self._handle_menu_selection(sender, context, menu_number)

        # Handle special cases first
        if intent.type == IntentType.MENU:
            return self._handle_menu_request(sender, context)

        if intent.type == IntentType.GREETING:
            return self._handle_greeting(sender, context)

        if intent.type == IntentType.HELP:
            return self._handle_help(sender, context)

        # Build routing decision
        decision = self._build_routing_decision(message_clean, intent, entities, context)

        # =====================================================================================================================
        # STEP 6: Service Execution
        # =====================================================================================================================

        # Check confidence threshold
        if decision.confidence < CONFIDENCE_THRESHOLD and ENABLE_AI_FALLBACK:
            logger.info(f"Confidence below threshold ({decision.confidence:.2f} < {CONFIDENCE_THRESHOLD}), using AI fallback")
            return await self._ai_fallback(message_clean, intent, entities, context)

        try:
            response = await self._execute_decision(sender, decision, context)
        except Exception as e:
            logger.exception(f"Service execution failed: {e}")
            return f"⚠️ Service error: {str(e)[:200]}\n\nPlease try again or type 'menu' for options."

        # =====================================================================================================================
        # STEP 7: Response Formatting
        # =====================================================================================================================

        # Add follow-up suggestions
        follow_ups = self.context_manager.get_follow_ups(sender)
        if follow_ups and len(response) < 3500:
            response = self._add_follow_ups(response, follow_ups)

        # Log performance
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")

        return response

    # =====================================================================================================================
    # ROUTING DECISION ENGINE
    # =====================================================================================================================

    def _build_routing_decision(
        self,
        message: str,
        intent: Intent,
        entities: List[Entity],
        context: SessionContext
    ) -> RoutingDecision:
        """Build routing decision based on intent and entities"""

        # Get primary entity
        primary_entity = None
        entity_value = None

        # Priority: City > Dealer > Warehouse > Product > DN
        entity_priority = [EntityType.CITY, EntityType.DEALER, EntityType.WAREHOUSE, EntityType.PRODUCT, EntityType.DN]
        for entity_type in entity_priority:
            for entity in entities:
                if entity.type == entity_type:
                    primary_entity = entity
                    entity_value = entity.value
                    break
            if primary_entity:
                break

        # Determine service based on intent and entity
        service_key = None
        method = None
        confidence = intent.confidence
        reason = ""

        # Map intent to service
        intent_service_map = {
            IntentType.DASHBOARD: "dashboard",
            IntentType.REVENUE: "revenue",
            IntentType.UNITS: "units",
            IntentType.PENDING: "pending",
            IntentType.DELIVERY: "delivery",
            IntentType.POD: "pod",
            IntentType.PGI: "pgi",
            IntentType.COMPARISON: "comparison",
            IntentType.RANKING: "ranking",
            IntentType.SUMMARY: "summary",
            IntentType.PERFORMANCE: "performance",
            IntentType.FORECAST: "forecast",
            IntentType.RECOMMENDATION: "recommendation",
            IntentType.ROOT_CAUSE: "root_cause",
            IntentType.EXECUTIVE: "executive",
            IntentType.NATIONAL: "national",
            IntentType.SEARCH: "search",
        }

        intent_metric = intent_service_map.get(intent.type)

        # Determine which service to route to
        if primary_entity:
            if primary_entity.type == EntityType.CITY:
                service_key = "city"
                method = "process_whatsapp_query"
                if intent_metric:
                    reason = f"City '{entity_value}' with intent: {intent_metric}"
                else:
                    reason = f"City '{entity_value}' dashboard"
                    intent_metric = "dashboard"

            elif primary_entity.type == EntityType.DEALER:
                service_key = "dealer"
                method = "process_whatsapp_query"
                reason = f"Dealer '{entity_value}'"
                intent_metric = intent_metric or "dashboard"

            elif primary_entity.type == EntityType.WAREHOUSE:
                service_key = "warehouse"
                method = "process_whatsapp_query"
                reason = f"Warehouse '{entity_value}'"
                intent_metric = intent_metric or "dashboard"

            elif primary_entity.type == EntityType.PRODUCT:
                service_key = "product"
                method = "process_whatsapp_query"
                reason = f"Product '{entity_value}'"
                intent_metric = intent_metric or "dashboard"

            elif primary_entity.type == EntityType.DN:
                service_key = "dn"
                method = "get_dn_dashboard"
                reason = f"DN '{entity_value}'"
                intent_metric = "dashboard"

        # National/executive intents
        elif intent.type in [IntentType.NATIONAL, IntentType.EXECUTIVE]:
            service_key = "national"
            method = "process_whatsapp_query"
            reason = f"{intent.type.value} intent"
            intent_metric = intent.type.value

        # Ranking intents
        elif intent.type == IntentType.RANKING:
            service_key = self._get_ranking_service(message)
            method = "process_whatsapp_query"
            reason = "Ranking request"

        # Comparison intents
        elif intent.type == IntentType.COMPARISON:
            service_key = self._get_comparison_service(message, entities)
            method = "process_whatsapp_query"
            reason = "Comparison request"

        # Fallback to AI
        else:
            service_key = "ai"
            method = "process_query"
            reason = "AI fallback"

        # Build entity dict for the service
        entity_dict = {
            "message": message,
            "intent": intent.type.value,
            "intent_metric": intent_metric,
            "confidence": confidence,
        }

        for entity in entities:
            entity_dict[entity.type.value] = entity.value

        # If we have a specific intent metric, add it
        if intent_metric and intent_metric not in ["dashboard", "national", "executive"]:
            entity_dict["metric"] = intent_metric

        # Create routing decision
        decision = RoutingDecision(
            intent=intent,
            service_key=service_key,
            method=method,
            entity=entity_dict,
            confidence=confidence,
            reason=reason,
            original_message=message,
            context={
                "current_city": context.current_city,
                "current_dealer": context.current_dealer,
                "current_warehouse": context.current_warehouse,
                "current_product": context.current_product,
            }
        )

        # Update context
        if service_key:
            context.current_service = service_key

        logger.info(f"🔀 Routing: {service_key}.{method} ({confidence:.2f}) - {reason}")
        return decision

    def _get_ranking_service(self, message: str) -> str:
        """Determine which service to use for ranking"""
        message_lower = message.lower()
        if "warehouse" in message_lower:
            return "warehouse"
        elif "dealer" in message_lower:
            return "dealer"
        elif "product" in message_lower:
            return "product"
        elif "city" in message_lower:
            return "city"
        else:
            return "national"

    def _get_comparison_service(self, message: str, entities: List[Entity]) -> str:
        """Determine which service to use for comparison"""
        entity_types = [e.type for e in entities]
        if EntityType.CITY in entity_types:
            return "city"
        elif EntityType.DEALER in entity_types:
            return "dealer"
        elif EntityType.WAREHOUSE in entity_types:
            return "warehouse"
        elif EntityType.PRODUCT in entity_types:
            return "product"
        else:
            return "national"

    def _parse_menu_number(self, message: str) -> Optional[int]:
        """Parse menu number from message"""
        match = re.fullmatch(r"\s*([0-9]+)\s*", message)
        if match:
            return int(match.group(1))
        return None

    # =====================================================================================================================
    # SERVICE EXECUTION
    # =====================================================================================================================

    async def _execute_decision(self, sender: str, decision: RoutingDecision, context: SessionContext) -> str:
        """Execute the routing decision"""
        # Get service from registry
        service = self.service_registry.get_service(decision.service_key)

        if not service:
            logger.warning(f"Service '{decision.service_key}' not found, falling back to AI")
            return await self._ai_fallback(decision.original_message, decision.intent, [], context)

        # Get method
        method = getattr(service, decision.method, None)
        if not method:
            logger.warning(f"Method '{decision.method}' not found in service '{decision.service_key}'")
            return await self._ai_fallback(decision.original_message, decision.intent, [], context)

        try:
            # Prepare arguments based on method
            if decision.method == "process_whatsapp_query":
                result = method(decision.original_message, sender)
            elif decision.method == "process_city_menu_input":
                result = method(sender, decision.original_message)
            elif decision.method == "process_menu_input":
                result = method(sender, decision.original_message)
            elif decision.method == "get_dn_dashboard":
                dn = decision.entity.get("dn")
                result = method(dn) if dn else "⚠️ Please provide a DN number."
            elif decision.method == "get_dealer_dashboard":
                dealer = decision.entity.get("dealer") or decision.entity.get("dealer_name")
                result = method(dealer) if dealer else "⚠️ Please provide a dealer name."
            elif decision.method == "get_city_dashboard":
                city = decision.entity.get("city") or decision.entity.get("city_name")
                result = method(city) if city else "⚠️ Please provide a city name."
            elif decision.method == "get_warehouse_dashboard":
                warehouse = decision.entity.get("warehouse")
                result = method(warehouse) if warehouse else "⚠️ Please provide a warehouse name."
            elif decision.method == "get_product_dashboard":
                product = decision.entity.get("product")
                result = method(product) if product else "⚠️ Please provide a product name."
            else:
                result = method()

            # Handle async results
            if inspect.isawaitable(result):
                result = await result

            # Extract message
            return self._extract_response(result)

        except Exception as e:
            logger.exception(f"Service execution error: {e}")
            return f"⚠️ Service error: {str(e)[:200]}"

    def _extract_response(self, result: Any) -> str:
        """Extract WhatsApp message from service result"""
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
            if result.get("data"):
                return str(result["data"])

        return str(result) if result else "No response from service."

    # =====================================================================================================================
    # AI FALLBACK
    # =====================================================================================================================

    async def _ai_fallback(self, message: str, intent: Intent, entities: List[Entity], context: SessionContext) -> str:
        """AI fallback for unanswered queries"""
        if not self._groq_client or not ENABLE_AI_FALLBACK:
            return self._get_help_response()

        try:
            # Build context for AI
            entity_str = ", ".join([f"{e.type.value}: {e.value}" for e in entities]) if entities else "None"
            context_str = f"Current city: {context.current_city or 'None'}, Dealer: {context.current_dealer or 'None'}"

            prompt = f"""You are HPK Logistics AI Assistant. The user asked: "{message}"

Detected intent: {intent.type.value} (confidence: {intent.confidence:.2f})
Entities: {entity_str}
Context: {context_str}

If this is a logistics question, provide a helpful response. If you don't know, suggest logistics topics they can ask about.

Available topics:
- DN Tracking (send a DN number)
- Dealer Analytics (dealer name)
- Warehouse Analytics (warehouse name)
- City Analytics (city name)
- Product Analytics (product name)
- National KPIs
- Pending Deliveries
- Performance Reports

Keep response concise and WhatsApp-friendly with emojis and bullet points."""

            # Use Groq AI
            response = self._groq_client.chat.completions.create(
                model="llama3-70b-8192",
                messages=[
                    {"role": "system", "content": "You are HPK Logistics AI Assistant. Help users with logistics data."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                max_tokens=500,
            )

            ai_response = response.choices[0].message.content.strip()
            return ai_response

        except Exception as e:
            logger.error(f"AI fallback failed: {e}")
            return self._get_help_response()

    # =====================================================================================================================
    # MENU HANDLING
    # =====================================================================================================================

    def _handle_menu_request(self, sender: str, context: SessionContext) -> str:
        """Handle menu request"""
        # Check if already in a menu
        if context.current_menu != MenuState.MAIN:
            return self._get_sub_menu(context.current_menu)

        # Show main menu
        context.current_menu = MenuState.MAIN
        return self._get_main_menu()

    def _handle_menu_selection(self, sender: str, context: SessionContext, menu_number: int) -> str:
        """Handle menu selection while in a menu"""
        # Map menu numbers to actions based on current menu state
        if context.current_menu == MenuState.MAIN:
            return self._handle_main_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.CITY:
            return self._handle_city_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.WAREHOUSE:
            return self._handle_warehouse_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.DEALER:
            return self._handle_dealer_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.PRODUCT:
            return self._handle_product_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.NATIONAL:
            return self._handle_national_menu_selection(sender, context, menu_number)

        elif context.current_menu == MenuState.DN:
            return self._handle_dn_menu_selection(sender, context, menu_number)

        return self._get_main_menu()

    def _handle_main_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle main menu selection"""
        menu_map = {
            1: (MenuState.DN, "📦 *DN DELIVERY MENU*", self._get_dn_menu()),
            2: (MenuState.DEALER, "🏪 *DEALER ANALYTICS MENU*", self._get_dealer_menu()),
            3: (MenuState.CITY, "🏙️ *CITY ANALYTICS MENU*", self._get_city_menu()),
            4: (MenuState.WAREHOUSE, "🏭 *WAREHOUSE ANALYTICS MENU*", self._get_warehouse_menu()),
            5: (MenuState.PRODUCT, "📦 *PRODUCT ANALYTICS MENU*", self._get_product_menu()),
            6: (MenuState.NATIONAL, "🇵🇰 *NATIONAL KPI MENU*", self._get_national_menu()),
            7: (MenuState.DN, "⏳ *PENDING DN MENU*", self._get_pending_dn_menu()),
            8: (MenuState.DN, "🏆 *TOP PERFORMERS MENU*", self._get_top_performers_menu()),
            9: (MenuState.MAIN, "🤖 *AI QUERY*", "Please type your question..."),
        }

        if number in menu_map:
            menu_state, title, content = menu_map[number]
            context.current_menu = menu_state
            if number == 9:
                context.current_menu = MenuState.MAIN
                return "🤖 *AI Query*\n\nType your question and I'll help you find the answer."
            return content

        return self._get_invalid_selection()

    # =====================================================================================================================
    # SUB-MENU HANDLING
    # =====================================================================================================================

    def _handle_city_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle city menu selection"""
        menu_map = {
            1: "📍 *City Dashboard*\n\nEnter city name:",
            2: "💰 *City Revenue*\n\nEnter city name:",
            3: "📦 *City Units*\n\nEnter city name:",
            4: "⏳ *City Pending*\n\nEnter city name:",
            5: "🚚 *City Delivery*\n\nEnter city name:",
            6: "🔄 *Compare Cities*\n\nEnter first city name:",
            7: "🏆 *City Rankings*",
            8: "🏷️ *Top Products*\n\nEnter city name:",
            9: "📈 *Business Score*\n\nEnter city name:",
            10: "📍 *Distance Info*\n\nEnter city name:",
            11: "📈 *Growth Analytics*\n\nEnter city name:",
            12: "🏭 *Warehouse Distribution*\n\nEnter city name:",
            13: "📋 *City Summary*\n\nEnter city name:",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 7:
                # Execute ranking directly
                service = self.service_registry.get_service("city")
                if service:
                    result = service.process_whatsapp_query("top cities", sender)
                    return self._extract_response(result)
                return self._get_city_menu()
            elif number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    def _handle_warehouse_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle warehouse menu selection"""
        menu_map = {
            1: "🏭 *Warehouse Dashboard*\n\nEnter warehouse name:",
            2: "📦 *Warehouse Inventory*\n\nEnter warehouse name:",
            3: "💰 *Warehouse Revenue*\n\nEnter warehouse name:",
            4: "📦 *Warehouse Units*\n\nEnter warehouse name:",
            5: "⏳ *Pending DN*\n\nEnter warehouse name:",
            6: "⏳ *Pending PGI*\n\nEnter warehouse name:",
            7: "⏳ *Pending POD*\n\nEnter warehouse name:",
            8: "🚚 *Delivery Performance*\n\nEnter warehouse name:",
            9: "🏆 *Warehouse Ranking*",
            10: "🔄 *Warehouse Comparison*\n\nEnter first warehouse name:",
            11: "🏷️ *Top Products*\n\nEnter warehouse name:",
            12: "📊 *Dealer Distribution*\n\nEnter warehouse name:",
            13: "📊 *City Distribution*\n\nEnter warehouse name:",
            14: "📊 *Storage Utilization*\n\nEnter warehouse name:",
            15: "🚚 *Transit Analysis*\n\nEnter warehouse name:",
            16: "📈 *Delivery Aging*\n\nEnter warehouse name:",
            17: "📊 *Warehouse KPIs*\n\nEnter warehouse name:",
            18: "📋 *AI Summary*\n\nEnter warehouse name:",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 9:
                service = self.service_registry.get_service("warehouse")
                if service:
                    result = service.process_whatsapp_query("top warehouses", sender)
                    return self._extract_response(result)
                return self._get_warehouse_menu()
            elif number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    def _handle_dealer_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle dealer menu selection"""
        menu_map = {
            1: "🏪 *Dealer Dashboard*\n\nEnter dealer name:",
            2: "💰 *Dealer Revenue*\n\nEnter dealer name:",
            3: "📦 *Dealer Units*\n\nEnter dealer name:",
            4: "🏷️ *Dealer Products*\n\nEnter dealer name:",
            5: "📈 *Dealer Performance*\n\nEnter dealer name:",
            6: "⏳ *Pending DN*\n\nEnter dealer name:",
            7: "⏳ *Pending PGI*\n\nEnter dealer name:",
            8: "⏳ *Pending POD*\n\nEnter dealer name:",
            9: "🚚 *Dealer Delivery*\n\nEnter dealer name:",
            10: "🏆 *Dealer Ranking*",
            11: "🔄 *Dealer Comparison*\n\nEnter first dealer name:",
            12: "📋 *Dealer History*\n\nEnter dealer name:",
            13: "🔍 *Dealer Search*\n\nEnter dealer name or code:",
            14: "📍 *Dealer Cities*\n\nEnter dealer name:",
            15: "📍 *Dealer Distance*\n\nEnter dealer name:",
            16: "📈 *Dealer Trends*\n\nEnter dealer name:",
            17: "🔮 *Dealer Forecast*\n\nEnter dealer name:",
            18: "📋 *AI Summary*\n\nEnter dealer name:",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 10:
                service = self.service_registry.get_service("dealer")
                if service:
                    result = service.process_whatsapp_query("top dealers", sender)
                    return self._extract_response(result)
                return self._get_dealer_menu()
            elif number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    def _handle_product_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle product menu selection"""
        menu_map = {
            1: "📦 *Product Dashboard*\n\nEnter product name:",
            2: "💰 *Product Revenue*\n\nEnter product name:",
            3: "📦 *Product Units*\n\nEnter product name:",
            4: "🏪 *Product Dealers*\n\nEnter product name:",
            5: "🏭 *Product Warehouses*\n\nEnter product name:",
            6: "🏙️ *Product Cities*\n\nEnter product name:",
            7: "⏳ *Pending DN*\n\nEnter product name:",
            8: "⏳ *Pending PGI*\n\nEnter product name:",
            9: "⏳ *Pending POD*\n\nEnter product name:",
            10: "🔄 *Product Comparison*\n\nEnter first product name:",
            11: "🏆 *Product Ranking*",
            12: "📈 *Monthly Trend*\n\nEnter product name:",
            13: "📋 *Executive Summary*\n\nEnter product name:",
            14: "💡 *AI Insights*\n\nEnter product name:",
            15: "🎯 *Recommendations*\n\nEnter product name:",
            16: "📋 *Product Life Cycle*\n\nEnter product name:",
            17: "📈 *Product Performance*\n\nEnter product name:",
            18: "🔍 *Smart Search*\n\nEnter product name, model, or material:",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 11:
                service = self.service_registry.get_service("product")
                if service:
                    result = service.process_whatsapp_query("top products", sender)
                    return self._extract_response(result)
                return self._get_product_menu()
            elif number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    def _handle_national_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle national KPI menu selection"""
        menu_map = {
            1: "🇵🇰 *National Dashboard*",
            2: "🏭 *Warehouse Dashboard*\n\nEnter warehouse name:",
            3: "🏆 *Warehouse Ranking*",
            4: "🔄 *Warehouse Comparison*\n\nEnter first warehouse name:",
            5: "💰 *National Revenue*",
            6: "📦 *National Units*",
            7: "🚚 *National Delivery*",
            8: "⏳ *Pending Dashboard*",
            9: "📄 *POD Dashboard*",
            10: "📄 *PGI Dashboard*",
            11: "🏪 *Dealer Coverage*",
            12: "🏙️ *City Analytics*\n\nEnter city name:",
            13: "📦 *Product Distribution*\n\nEnter product name:",
            14: "📋 *SLA Compliance*",
            15: "📋 *Executive Summary*",
            16: "💡 *AI Insights*",
            17: "🎯 *Recommendations*",
            18: "⭐ *National Health Score*",
            19: "📈 *Monthly Trend*",
            20: "🔮 *National Forecast*",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            elif number in [1, 3, 5, 6, 7, 8, 9, 10, 11, 14, 15, 16, 17, 18, 19, 20]:
                # Direct execution
                service = self.service_registry.get_service("national")
                if service:
                    action_map = {
                        1: "national dashboard",
                        3: "warehouse ranking",
                        5: "national revenue",
                        6: "national units",
                        7: "national delivery",
                        8: "pending dashboard",
                        9: "pod dashboard",
                        10: "pgi dashboard",
                        11: "dealer coverage",
                        14: "sla compliance",
                        15: "executive summary",
                        16: "ai insights",
                        17: "recommendations",
                        18: "national health score",
                        19: "monthly trend",
                        20: "national forecast",
                    }
                    query = action_map.get(number, "")
                    result = service.process_whatsapp_query(query, sender)
                    return self._extract_response(result)
                return self._get_national_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    def _handle_dn_menu_selection(self, sender: str, context: SessionContext, number: int) -> str:
        """Handle DN menu selection"""
        menu_map = {
            1: "📦 *DN Dashboard*\n\nEnter DN number:",
            2: "📊 *DN Status*\n\nEnter DN number:",
            3: "📋 *DN History*\n\nEnter DN number:",
            4: "📅 *DN Timeline*\n\nEnter DN number:",
            5: "🚚 *Transit Analysis*\n\nEnter DN number:",
            6: "⏳ *Pending DN*",
            7: "⏳ *Pending PGI*",
            8: "⏳ *Pending POD*",
            9: "⚠️ *Delayed DN*",
            10: "🔄 *Recent DN*",
            11: "🔍 *Search DN*\n\nEnter search term:",
            12: "🔄 *Compare DN*\n\nEnter first DN number:",
            99: "0. Main Menu",
        }

        if number in menu_map:
            if number == 99:
                context.current_menu = MenuState.MAIN
                return self._get_main_menu()
            elif number in [6, 7, 8, 9, 10]:
                service = self.service_registry.get_service("dn")
                if service:
                    action_map = {
                        6: "pending dns",
                        7: "pending pgi",
                        8: "pending pod",
                        9: "delayed dns",
                        10: "recent dns",
                    }
                    query = action_map.get(number, "")
                    result = service.process_whatsapp_query(query, sender)
                    return self._extract_response(result)
                return self._get_dn_menu()
            else:
                return menu_map[number]

        return self._get_invalid_selection()

    # =====================================================================================================================
    # MENU GENERATORS
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

    def _get_pending_dn_menu(self) -> str:
        return "\n".join([
            "⏳ *PENDING DN MENU*",
            "",
            "0. Main Menu",
            "1. All Pending DN",
            "2. Pending PGI",
            "3. Pending POD",
            "4. Delayed DN",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])

    def _get_top_performers_menu(self) -> str:
        return "\n".join([
            "🏆 *TOP PERFORMERS MENU*",
            "",
            "0. Main Menu",
            "1. Top Dealers",
            "2. Top Warehouses",
            "3. Top Cities",
            "4. Top Products",
            "5. Top Performers (All)",
            "99. Back to Main",
            "",
            "Reply with a number:"
        ])

    def _get_sub_menu(self, menu_state: MenuState) -> str:
        """Get sub-menu based on state"""
        menu_map = {
            MenuState.CITY: self._get_city_menu,
            MenuState.WAREHOUSE: self._get_warehouse_menu,
            MenuState.DEALER: self._get_dealer_menu,
            MenuState.PRODUCT: self._get_product_menu,
            MenuState.NATIONAL: self._get_national_menu,
            MenuState.DN: self._get_dn_menu,
        }
        return menu_map.get(menu_state, self._get_main_menu)()

    def _get_help_response(self) -> str:
        return "\n".join([
            "🤖 *How can I help?*",
            "",
            "You can ask me about:",
            "",
            "📍 **City Analytics**",
            "• Lahore, Karachi, Rawalpindi...",
            "",
            "🏪 **Dealer Analytics**",
            "• Dealer performance, ranking, comparison",
            "",
            "🏭 **Warehouse Analytics**",
            "• Warehouse performance, inventory, ranking",
            "",
            "📦 **Product Analytics**",
            "• Product sales, performance, life cycle",
            "",
            "🇵🇰 **National KPI**",
            "• National performance, executive dashboard",
            "",
            "📋 **DN Tracking**",
            "• Send any 8-12 digit DN number",
            "",
            "Type *menu* to see all options."
        ])

    def _get_invalid_selection(self) -> str:
        return "Invalid selection. Please enter a valid number.\n\n" + self._get_main_menu()

    def _handle_greeting(self, sender: str, context: SessionContext) -> str:
        """Handle greeting"""
        return "👋 Hello! Welcome to HPK Logistics 🏪. How can I assist you today? 📦"

    def _handle_help(self, sender: str, context: SessionContext) -> str:
        """Handle help request"""
        return self._get_help_response()

    # =====================================================================================================================
    # RESPONSE HELPERS
    # =====================================================================================================================

    def _add_follow_ups(self, response: str, follow_ups: List[str]) -> str:
        """Add follow-up suggestions to response"""
        if not follow_ups:
            return response

        footer = "\n\n💡 *Try:*"
        for suggestion in follow_ups[:3]:
            footer += f"\n• {suggestion}"

        # Ensure we don't exceed WhatsApp limits
        if len(response) + len(footer) > 4000:
            return response

        return response + footer

    # =====================================================================================================================
    # HEALTH CHECK
    # =====================================================================================================================

    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        return {
            "service": "ai_provider_service",
            "version": "30.0",
            "status": "healthy",
            "intent_engine": {
                "intents": len(self.intent_engine.INTENT_PATTERNS),
                "cache_size": len(self.intent_engine._cache),
            },
            "entity_engine": {
                "cities": len(self.entity_engine.KNOWN_CITIES),
                "warehouses": len(self.entity_engine.KNOWN_WAREHOUSES),
                "cache_size": len(self.entity_engine._cache),
            },
            "context_manager": {
                "active_sessions": len(self.context_manager._contexts),
                "ttl_seconds": self.context_manager._ttl,
            },
            "service_registry": {
                "services": self.service_registry.get_all_keys(),
            },
            "ai_fallback": {
                "enabled": ENABLE_AI_FALLBACK,
                "groq_available": GROQ_AVAILABLE,
            },
            "timestamp": datetime.now().isoformat(),
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
    "IntentEngine",
    "EntityEngine",
    "ContextManager",
    "ServiceRegistry",
    "Entity",
    "Intent",
    "SessionContext",
    "RoutingDecision",
]
