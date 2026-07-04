# ============================================================
# FILE: app/services/dealer_analytics_service.py
# VERSION: 3.0 - ENTERPRISE DEALER DOMAIN AI ENGINE
# ============================================================

"""
File: app/services/dealer_analytics_service.py
Version: 3.0 - ENTERPRISE DEALER DOMAIN AI ENGINE

================================================================================
PURPOSE
================================================================================

This is the DEALER DOMAIN AI ENGINE - an independent microservice.

Its responsibilities are:
1. Dealer Intelligence Engine
2. Dealer Analytics & KPI
3. Dealer Search & Ranking
4. Dealer Comparison & Performance
5. Dealer AI Assistant
6. Dealer SQL Engine
7. Dealer Intent Detection
8. Dealer Semantic Routing
9. Dealer Session Management
10. Dealer Response Engine

================================================================================
AI ARCHITECTURE
================================================================================

WhatsApp
    │
    ▼
ai_provider_service.py
    │
    ▼
DealerAnalyticsService
    │
    ├── Session Manager
    ├── Menu Engine
    ├── Intent Detection
    ├── Entity Extraction
    ├── Semantic Router
    ├── Business Rule Engine
    ├── SQL Planner
    ├── Repository
    ├── PostgreSQL
    ├── Analytics Engine
    └── Response Formatter

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from enum import Enum
from typing import Any, Optional, Dict, List, Tuple, Union, Callable

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: AI LIBRARIES
# ============================================================

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False

try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False

try:
    from semantic_router import Route, SemanticRouter
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
except ImportError:
    TEXTBLOB_AVAILABLE = False

try:
    import nltk
    from nltk.tokenize import word_tokenize
    NLTK_AVAILABLE = True
except ImportError:
    NLTK_AVAILABLE = False

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

# ============================================================
# BLOCK 2: DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, asc, and_, case
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Dealer database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger.error(f"❌ Dealer database import error: {e}")

# ============================================================
# BLOCK 3: CONFIGURATION
# ============================================================

DEALER_CACHE_TTL = int(os.getenv("DEALER_CACHE_TTL", "300"))
DEALER_SESSION_TIMEOUT = int(os.getenv("DEALER_SESSION_TIMEOUT", "1800"))
DEALER_AI_ENABLED = os.getenv("DEALER_AI_ENABLED", "true").lower() == "true"
DEALER_SEMANTIC_ENABLED = os.getenv("DEALER_SEMANTIC_ENABLED", "true").lower() == "true"

# AI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ============================================================
# BLOCK 4: ENUMS
# ============================================================

class DealerIntent(Enum):
    """Dealer intent types"""
    DASHBOARD = "dashboard"
    REVENUE = "revenue"
    UNITS = "units"
    PENDING = "pending"
    DELIVERY = "delivery"
    RANKING = "ranking"
    COMPARISON = "comparison"
    SEARCH = "search"
    PERFORMANCE = "performance"
    HISTORY = "history"
    TIMELINE = "timeline"
    PRODUCTS = "products"
    MODELS = "models"
    SUMMARY = "summary"
    AI_ASK = "ai_ask"
    MENU = "menu"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"

class DealerMenuState(Enum):
    """Dealer menu states"""
    MAIN = "main"
    DASHBOARD = "dashboard"
    SEARCH = "search"
    PERFORMANCE = "performance"
    ANALYTICS = "analytics"
    AI_ASSISTANT = "ai_assistant"
    DEALER_SELECTED = "dealer_selected"
    COMPARISON = "comparison"

# ============================================================
# BLOCK 5: DATA CLASSES
# ============================================================

@dataclass
class DealerSession:
    """Dealer session state"""
    session_id: str
    locked: bool = True
    current_dealer: Optional[str] = None
    current_dealer_code: Optional[str] = None
    menu_state: DealerMenuState = DealerMenuState.MAIN
    selected_option: Optional[str] = None
    comparison_dealers: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_query: str = ""
    last_answer: str = ""
    last_intent: Optional[DealerIntent] = None
    last_sql: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    filters: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)
    
    def touch(self):
        self.updated_at = datetime.now()
    
    def is_expired(self, timeout: int = DEALER_SESSION_TIMEOUT) -> bool:
        elapsed = (datetime.now() - self.updated_at).total_seconds()
        return elapsed > timeout
    
    def add_history(self, query: str, answer: str):
        self.history.append({
            "query": query,
            "answer": answer[:200] if len(answer) > 200 else answer,
            "intent": self.last_intent.value if self.last_intent else None,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.last_query = query
        self.last_answer = answer
        self.touch()
    
    def set_dealer(self, name: str, code: Optional[str] = None):
        self.current_dealer = name
        self.current_dealer_code = code
        self.menu_state = DealerMenuState.DEALER_SELECTED
        self.touch()
    
    def clear(self):
        self.current_dealer = None
        self.current_dealer_code = None
        self.menu_state = DealerMenuState.MAIN
        self.comparison_dealers = []
        self.filters = {}
        self.context = {}
        self.touch()

@dataclass
class DealerIntentResult:
    """Intent detection result"""
    intent: DealerIntent
    confidence: float
    entities: Dict[str, Any]
    raw_input: str
    processing_time_ms: float

@dataclass
class DealerQueryPlan:
    """Query execution plan"""
    intent: DealerIntent
    dealer: Optional[str] = None
    dealers: List[str] = field(default_factory=list)
    metrics: List[str] = field(default_factory=list)
    limit: int = 10
    sort_by: Optional[str] = None
    order: str = "desc"
    timeframe: Optional[str] = None
    filters: Dict[str, Any] = field(default_factory=dict)

# ============================================================
# BLOCK 6: UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0

def _format_currency(amount: float) -> str:
    if amount is None:
        return "PKR 0.00"
    return f"PKR {amount:,.2f}"

def _format_number(num: Union[int, float]) -> str:
    if num is None:
        return "0"
    return f"{num:,}"

def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%b-%Y")
    return _text(value, "N/A")

# ============================================================
# BLOCK 7: DEALER INTENT ENGINE
# ============================================================

class DealerIntentEngine:
    """AI-powered intent detection for dealer queries"""
    
    INTENT_PATTERNS = {
        DealerIntent.DASHBOARD: [
            r"(?:show|display|get).*(?:dealer|dashboard|profile)",
            r"dealer (?:dashboard|profile|details|info)",
        ],
        DealerIntent.REVENUE: [
            r"(?:revenue|sales|income|amount|turnover).*(?:dealer)",
            r"how much (?:revenue|sales)",
            r"(?:revenue|sales) (?:of|for|by)",
        ],
        DealerIntent.UNITS: [
            r"(?:units|quantity|qty|volume|pieces).*(?:dealer)",
            r"how many units",
        ],
        DealerIntent.PENDING: [
            r"(?:pending|backlog|overdue|delayed).*(?:dealer)",
            r"pending (?:dn|order|delivery)",
        ],
        DealerIntent.DELIVERY: [
            r"(?:delivery|deliveries|transit|shipping).*(?:dealer)",
            r"delivery (?:performance|time|days)",
        ],
        DealerIntent.RANKING: [
            r"(?:top|best|highest|leading).*(?:dealer|dealers)",
            r"dealer (?:ranking|rank|leaderboard)",
            r"top (?:dealers|performers)",
        ],
        DealerIntent.COMPARISON: [
            r"compare\s+([\w\s]+)\s+and\s+([\w\s]+)",
            r"vs",
            r"comparison",
        ],
        DealerIntent.SEARCH: [
            r"(?:search|find|lookup).*(?:dealer)",
            r"search (?:dealer|dealers)",
        ],
        DealerIntent.PERFORMANCE: [
            r"(?:performance|score|rating|health).*(?:dealer)",
            r"dealer (?:performance|score|efficiency)",
        ],
        DealerIntent.HISTORY: [
            r"(?:history|past|previous).*(?:dealer)",
            r"dealer (?:history|transactions)",
        ],
        DealerIntent.TIMELINE: [
            r"(?:timeline|chronology|when).*(?:dealer)",
            r"dealer (?:timeline|activity)",
        ],
        DealerIntent.PRODUCTS: [
            r"(?:product|products|items).*(?:dealer)",
            r"what (?:products|items) (?:does|did)",
        ],
        DealerIntent.MODELS: [
            r"(?:model|models|variants).*(?:dealer)",
            r"which (?:models|variants)",
        ],
        DealerIntent.SUMMARY: [
            r"(?:summary|overview|brief|executive).*(?:dealer)",
            r"dealer (?:summary|overview)",
        ],
        DealerIntent.AI_ASK: [
            r"(?:ask|tell|explain|why|how|what|when|where).*(?:dealer)",
            r"dealer (?:analysis|insight|question)",
        ],
        DealerIntent.MENU: [
            r"menu",
            r"dealer menu",
            r"options",
            r"help",
        ],
        DealerIntent.HELP: [
            r"help",
            r"support",
            r"assist",
            r"what can (?:you|i)",
        ],
        DealerIntent.EXIT: [
            r"99",
            r"exit",
            r"quit",
            r"cancel",
            r"back",
        ],
    }
    
    def __init__(self):
        self._patterns = {
            intent: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
            for intent, patterns in self.INTENT_PATTERNS.items()
        }
        self._cache: Dict[str, Tuple[DealerIntent, float]] = {}
        self._cache_lock = threading.RLock()
        self._initialized = False
        self._initialize()
    
    def _initialize(self):
        if self._initialized:
            return
        
        logger.info("🤖 Initializing Dealer Intent Engine...")
        start_time = time.time()
        
        self._init_spacy()
        self._init_nltk()
        self._init_semantic_router()
        self._init_llm_clients()
        
        self._initialized = True
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ Dealer Intent Engine initialized in {elapsed:.1f}ms")
    
    def _init_spacy(self):
        self.nlp = None
        if SPACY_AVAILABLE and spacy:
            try:
                self.nlp = spacy.load("en_core_web_sm")
                logger.info("✅ spaCy loaded")
            except:
                try:
                    spacy.cli.download("en_core_web_sm")
                    self.nlp = spacy.load("en_core_web_sm")
                    logger.info("✅ spaCy downloaded and loaded")
                except Exception as e:
                    logger.warning(f"⚠️ spaCy init failed: {e}")
    
    def _init_nltk(self):
        self.nltk_available = False
        if NLTK_AVAILABLE and nltk:
            try:
                nltk.data.find('tokenizers/punkt')
                self.nltk_available = True
            except LookupError:
                try:
                    nltk.download('punkt', quiet=True)
                    self.nltk_available = True
                except:
                    pass
    
    def _init_semantic_router(self):
        self.semantic_router = None
        if SEMANTIC_ROUTER_AVAILABLE and SemanticRouter:
            try:
                routes = [
                    Route(name="dealer_dashboard", utterances=[
                        "dealer dashboard", "show dealer", "dealer info", "dealer details"
                    ]),
                    Route(name="dealer_revenue", utterances=[
                        "dealer revenue", "dealer sales", "revenue for dealer"
                    ]),
                    Route(name="dealer_pending", utterances=[
                        "dealer pending", "pending orders", "dealer backlog"
                    ]),
                    Route(name="dealer_ranking", utterances=[
                        "top dealers", "dealer ranking", "best dealers"
                    ]),
                    Route(name="dealer_comparison", utterances=[
                        "compare dealers", "dealer vs dealer", "comparison"
                    ]),
                    Route(name="dealer_search", utterances=[
                        "search dealer", "find dealer", "lookup dealer"
                    ]),
                    Route(name="dealer_performance", utterances=[
                        "dealer performance", "dealer score", "dealer health"
                    ]),
                ]
                self.semantic_router = SemanticRouter(routes=routes)
                logger.info("✅ Semantic Router initialized")
            except Exception as e:
                logger.warning(f"⚠️ Semantic Router init failed: {e}")
    
    def _init_llm_clients(self):
        self.openai_client = None
        self.groq_client = None
        
        if OPENAI_AVAILABLE and openai and OPENAI_API_KEY:
            try:
                self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                logger.info("✅ OpenAI client initialized")
            except Exception as e:
                logger.warning(f"⚠️ OpenAI init failed: {e}")
        
        if GROQ_AVAILABLE and groq and GROQ_API_KEY:
            try:
                self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
                logger.info("✅ Groq client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Groq init failed: {e}")
    
    def detect_intent(self, text: str) -> DealerIntentResult:
        """Detect intent using multi-stage pipeline"""
        start_time = time.time()
        
        if not text or not text.strip():
            return DealerIntentResult(
                intent=DealerIntent.UNKNOWN,
                confidence=0.0,
                entities={},
                raw_input=text,
                processing_time_ms=0.0
            )
        
        text_clean = text.strip().lower()
        cache_key = text_clean[:100]
        
        with self._cache_lock:
            if cache_key in self._cache:
                intent, confidence = self._cache[cache_key]
                return DealerIntentResult(
                    intent=intent,
                    confidence=confidence,
                    entities=self._extract_entities(text),
                    raw_input=text,
                    processing_time_ms=0.0
                )
        
        # Stage 1: Direct pattern matching
        best_intent = DealerIntent.UNKNOWN
        best_score = 0.0
        
        for intent, patterns in self._patterns.items():
            matches = 0
            for pattern in patterns:
                if pattern.search(text_clean):
                    matches += 1
            if matches > 0:
                score = min(1.0, matches / max(1, len(patterns)) * 2)
                if score > best_score:
                    best_score = score
                    best_intent = intent
        
        # Stage 2: RapidFuzz
        if RAPIDFUZZ_AVAILABLE and best_score < 0.6:
            for intent, patterns in self.INTENT_PATTERNS.items():
                for pattern in patterns:
                    score = fuzz.partial_ratio(text_clean, pattern)
                    if score > 80:
                        best_intent = intent
                        best_score = score / 100.0
                        break
                if best_score > 0.8:
                    break
        
        # Stage 3: Semantic Router
        if DEALER_SEMANTIC_ENABLED and self.semantic_router and best_score < 0.6:
            try:
                result = self.semantic_router(text_clean)
                if result and hasattr(result, 'name'):
                    intent_name = result.name.replace("dealer_", "")
                    for intent in DealerIntent:
                        if intent.value == intent_name:
                            best_intent = intent
                            best_score = 0.7
                            break
            except Exception:
                pass
        
        # Stage 4: LLM verification
        if DEALER_AI_ENABLED and best_score < 0.7:
            llm_result = self._llm_verify(text_clean)
            if llm_result:
                best_intent, best_score = llm_result
        
        entities = self._extract_entities(text)
        
        with self._cache_lock:
            self._cache[cache_key] = (best_intent, best_score)
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        return DealerIntentResult(
            intent=best_intent,
            confidence=best_score,
            entities=entities,
            raw_input=text,
            processing_time_ms=elapsed_ms
        )
    
    def _llm_verify(self, text: str) -> Optional[Tuple[DealerIntent, float]]:
        """Use LLM to verify intent"""
        try:
            if self.groq_client:
                response = self.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": "Classify dealer query intent: dashboard, revenue, pending, ranking, comparison, search, performance, summary, ai_ask, menu, help, exit. Return only the intent name."},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    max_tokens=20
                )
                result = response.choices[0].message.content.strip().lower()
                for intent in DealerIntent:
                    if intent.value == result:
                        return (intent, 0.9)
            elif self.openai_client:
                response = self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Classify dealer query intent: dashboard, revenue, pending, ranking, comparison, search, performance, summary, ai_ask, menu, help, exit. Return only the intent name."},
                        {"role": "user", "content": text}
                    ],
                    temperature=0.1,
                    max_tokens=20
                )
                result = response.choices[0].message.content.strip().lower()
                for intent in DealerIntent:
                    if intent.value == result:
                        return (intent, 0.9)
        except Exception as e:
            logger.debug(f"LLM verification failed: {e}")
        
        return None
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text"""
        entities = {
            "dealers": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc"
        }
        
        # Extract dealer names (simple pattern)
        dealer_pattern = r'(?:dealer|dealers|for|of|in|from)\s+([A-Za-z\s]+)'
        matches = re.findall(dealer_pattern, text, re.IGNORECASE)
        if matches:
            entities["dealers"] = [m.strip() for m in matches if m.strip()]
        
        # Extract limit
        limit_match = re.search(r'top\s+(\d+)', text, re.IGNORECASE)
        if limit_match:
            entities["limit"] = int(limit_match.group(1))
        
        # Extract metrics
        metric_keywords = ["revenue", "units", "pending", "delivery", "performance"]
        for metric in metric_keywords:
            if metric in text.lower():
                entities["metrics"].append(metric)
        
        return entities

# ============================================================
# BLOCK 8: DEALER ENTITY EXTRACTOR
# ============================================================

class DealerEntityExtractor:
    """Extract dealer entities from text"""
    
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
    
    def extract(self, text: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """Extract entities with context awareness"""
        text_lower = text.lower()
        cache_key = text_lower[:100]
        
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        entities = {
            "dealer_names": [],
            "dealer_codes": [],
            "metrics": [],
            "limit": 10,
            "sort_by": None,
            "order": "desc",
            "comparison": [],
            "timeframe": None,
            "filters": {}
        }
        
        # Extract dealer names
        dealer_patterns = [
            r'dealer\s+([A-Za-z\s]+)',
            r'for\s+([A-Za-z\s]+)',
            r'of\s+([A-Za-z\s]+)',
            r'compare\s+([A-Za-z\s]+)\s+and\s+([A-Za-z\s]+)',
        ]
        
        for pattern in dealer_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                for match in matches:
                    if isinstance(match, tuple):
                        entities["dealer_names"].extend([m.strip() for m in match if m.strip()])
                        entities["comparison"] = [m.strip() for m in match if m.strip()]
                    else:
                        name = match.strip()
                        if name and len(name) > 2:
                            entities["dealer_names"].append(name)
        
        # Extract dealer codes
        code_pattern = r'\b([A-Z0-9]{4,10})\b'
        code_matches = re.findall(code_pattern, text.upper())
        if code_matches:
            entities["dealer_codes"] = code_matches
        
        # Extract limit
        limit_match = re.search(r'(?:top|first|limit)\s+(\d+)', text_lower)
        if limit_match:
            entities["limit"] = int(limit_match.group(1))
        
        # Extract metrics
        metric_map = {
            "revenue": ["revenue", "sales", "income", "amount"],
            "units": ["units", "quantity", "qty", "volume"],
            "pending": ["pending", "backlog", "overdue"],
            "delivery": ["delivery", "deliveries", "transit"],
            "performance": ["performance", "score", "rating", "health"],
        }
        
        for metric, keywords in metric_map.items():
            for keyword in keywords:
                if keyword in text_lower:
                    entities["metrics"].append(metric)
                    break
        
        # Extract sort order
        if "highest" in text_lower or "top" in text_lower:
            entities["order"] = "desc"
        elif "lowest" in text_lower or "bottom" in text_lower:
            entities["order"] = "asc"
        
        # Extract timeframe
        timeframe_patterns = [
            (r'this\s+month', 'current_month'),
            (r'last\s+month', 'previous_month'),
            (r'this\s+quarter', 'current_quarter'),
            (r'last\s+quarter', 'previous_quarter'),
            (r'this\s+year', 'current_year'),
            (r'last\s+year', 'previous_year'),
        ]
        
        for pattern, value in timeframe_patterns:
            if re.search(pattern, text_lower):
                entities["timeframe"] = value
                break
        
        with self._cache_lock:
            self._cache[cache_key] = entities.copy()
        
        return entities

# ============================================================
# BLOCK 9: DEALER REPOSITORY
# ============================================================

class DealerRepository:
    """PostgreSQL repository for dealer operations"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
    
    def get_dashboard(self, dealer_identifier: str) -> Optional[Dict[str, Any]]:
        """Get dealer dashboard"""
        cache_key = f"dashboard_{dealer_identifier.lower()}"
        
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pgi_pending_dn'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pod_pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pod_completed'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no)
                ))).label('pgi_completed'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
                func.avg(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)),
                     DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                )).label('avg_pod_days'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue_per_dn'),
                func.min(DeliveryReport.dn_create_date).label('first_order'),
                func.max(DeliveryReport.dn_create_date).label('last_order'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.dealer_code) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_identifier.lower()}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{dealer_identifier.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).first()
            
            if not query:
                return None
            
            total_dn = int(query.total_dn or 0)
            pending_dn = int(query.pending_dn or 0)
            pgi_completed = int(query.pgi_completed or 0)
            pod_completed = int(query.pod_completed or 0)
            
            data = {
                'dealer': _text(query.dealer),
                'dealer_code': _text(query.dealer_code),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'total_dn': total_dn,
                'total_units': int(query.total_units or 0),
                'total_revenue': float(query.total_revenue or 0.0),
                'pending_dn': pending_dn,
                'pgi_pending_dn': int(query.pgi_pending_dn or 0),
                'pod_pending_dn': int(query.pod_pending_dn or 0),
                'pgi_completed': pgi_completed,
                'pod_completed': pod_completed,
                'avg_delivery_days': float(query.avg_delivery_days or 0.0),
                'avg_pod_days': float(query.avg_pod_days or 0.0),
                'avg_revenue_per_dn': float(query.avg_revenue_per_dn or 0.0),
                'delivery_success_pct': _percent(pgi_completed, total_dn),
                'pod_success_pct': _percent(pod_completed, total_dn),
                'pending_pct': _percent(pending_dn, total_dn),
                'first_order': _date_text(query.first_order),
                'last_order': _date_text(query.last_order),
            }
            
            # Business score
            score = (
                data['delivery_success_pct'] * 0.30 +
                (100 - data['pending_pct']) * 0.25 +
                min(100, data['avg_revenue_per_dn'] / 1000) * 0.25 +
                min(100, data['total_dn'] / 10) * 0.20
            )
            data['business_score'] = round(min(100, max(0, score)), 1)
            
            if data['business_score'] >= 85:
                data['overall_status'] = "Excellent"
                data['performance_grade'] = "A"
            elif data['business_score'] >= 70:
                data['overall_status'] = "Good"
                data['performance_grade'] = "B"
            elif data['business_score'] >= 50:
                data['overall_status'] = "Watch"
                data['performance_grade'] = "C"
            else:
                data['overall_status'] = "Critical"
                data['performance_grade'] = "D"
            
            with self._cache_lock:
                self._cache[cache_key] = data.copy()
            
            return data
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            return None
    
    def get_ranking(self, metric: str = "revenue", limit: int = 10) -> List[Dict[str, Any]]:
        """Get dealer ranking"""
        metric_map = {
            "revenue": (func.sum(DeliveryReport.dn_amount), _format_currency),
            "units": (func.sum(DeliveryReport.dn_qty), lambda x: f"{int(x or 0):,}"),
            "dn": (func.count(distinct(DeliveryReport.dn_no)), lambda x: f"{int(x or 0):,}"),
            "delivery": (func.avg(case(
                (DeliveryReport.good_issue_date.isnot(None),
                 DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
            )), lambda x: f"{float(x or 0):.1f} days"),
            "pending": (func.count(distinct(case(
                (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                 DeliveryReport.dn_no)
            ))), lambda x: f"{int(x or 0):,}"),
        }
        
        if metric not in metric_map:
            metric = "revenue"
        
        agg_func, formatter = metric_map[metric]
        
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                agg_func.label('value')
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc('value') if metric in ["revenue", "units", "dn"] else asc('value')
            ).limit(limit).all()
            
            ranking = []
            for row in results:
                if row.dealer:
                    ranking.append({
                        'dealer': _text(row.dealer),
                        'value': formatter(row.value),
                        'raw_value': float(row.value or 0),
                    })
            return ranking
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            return []
    
    def compare(self, dealer1: str, dealer2: str) -> Dict[str, Any]:
        """Compare two dealers"""
        dash1 = self.get_dashboard(dealer1)
        dash2 = self.get_dashboard(dealer2)
        
        if not dash1 or not dash2:
            return {}
        
        metrics = {}
        
        for dealer, dash in [(dealer1, dash1), (dealer2, dash2)]:
            metrics[f"{dealer}_metrics"] = {
                "Revenue": _format_currency(dash.get('total_revenue', 0)),
                "Units": _format_number(dash.get('total_units', 0)),
                "DN": _format_number(dash.get('total_dn', 0)),
                "Pending": _format_number(dash.get('pending_dn', 0)),
                "Delivery": f"{dash.get('delivery_success_pct', 0):.1f}%",
                "POD": f"{dash.get('pod_success_pct', 0):.1f}%",
                "Score": f"{dash.get('business_score', 0):.1f}/100",
                "Grade": dash.get('performance_grade', 'N/A'),
            }
        
        rev1 = dash1.get('total_revenue', 0)
        rev2 = dash2.get('total_revenue', 0)
        
        if rev1 > rev2:
            metrics["explanation"] = f"{dealer1} has higher revenue than {dealer2}"
        elif rev2 > rev1:
            metrics["explanation"] = f"{dealer2} has higher revenue than {dealer1}"
        else:
            metrics["explanation"] = f"{dealer1} and {dealer2} have similar revenue"
        
        return metrics
    
    def search(self, query: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Search dealers"""
        search_pattern = f"%{query}%"
        
        try:
            results = self.session.query(
                DeliveryReport.customer_name.label('dealer'),
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_count'),
            ).filter(
                or_(
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.dealer_code.ilike(search_pattern),
                    func.lower(DeliveryReport.customer_name).ilike(f"%{query.lower()}%"),
                    func.lower(DeliveryReport.dealer_code).ilike(f"%{query.lower()}%"),
                )
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.dealer_code,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            items = []
            for row in results:
                if row.dealer:
                    items.append({
                        'dealer': _text(row.dealer),
                        'dealer_code': _text(row.dealer_code),
                        'sales_office': _text(row.sales_office),
                        'sales_manager': _text(row.sales_manager),
                        'revenue': float(row.revenue or 0),
                        'dn_count': int(row.dn_count or 0),
                        'units': int(row.units or 0),
                        'pending_count': int(row.pending_count or 0),
                    })
            return items
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    def get_products(self, dealer_identifier: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get products for a dealer"""
        try:
            results = self.session.query(
                DeliveryReport.customer_model.label('product'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(distinct(DeliveryReport.dn_no)).label('dn_count'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_name) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.dealer_code) == dealer_identifier.lower(),
                    func.lower(DeliveryReport.customer_name).ilike(f"%{dealer_identifier.lower()}%"),
                ),
                DeliveryReport.customer_model.isnot(None)
            ).group_by(
                DeliveryReport.customer_model
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            products = []
            for row in results:
                if row.product:
                    products.append({
                        'product': _text(row.product),
                        'revenue': float(row.revenue or 0),
                        'units': int(row.units or 0),
                        'dn_count': int(row.dn_count or 0),
                    })
            return products
        except Exception as e:
            logger.error(f"Products error: {e}")
            return []

# ============================================================
# BLOCK 10: DEALER RENDERER
# ============================================================

class DealerRenderer:
    """Render dealer responses for WhatsApp"""
    
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "📊 *DEALER INTELLIGENCE ENGINE*",
            "",
            "1️⃣ Dealer Dashboard",
            "2️⃣ Dealer Search",
            "3️⃣ Dealer Performance",
            "4️⃣ Dealer Analytics",
            "5️⃣ AI Assistant",
            "",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type dealer name for dashboard",
            "• top dealers - Show rankings",
            "• search [keyword] - Search dealers",
            "• compare [dealer1] and [dealer2]",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_dashboard(dealer: str, data: Dict[str, Any]) -> str:
        return "\n".join([
            f"📊 *Dealer Dashboard - {dealer}*",
            "",
            "📌 *Details*",
            f"Code: {data.get('dealer_code', 'N/A')}",
            f"Office: {data.get('sales_office', 'N/A')}",
            f"Manager: {data.get('sales_manager', 'N/A')}",
            "",
            "💰 *Financials*",
            f"Revenue: {_format_currency(data.get('total_revenue', 0))}",
            f"Avg/DN: {_format_currency(data.get('avg_revenue_per_dn', 0))}",
            "",
            "📦 *Operations*",
            f"DN: {_format_number(data.get('total_dn', 0))}",
            f"Units: {_format_number(data.get('total_units', 0))}",
            f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
            f"Pending PGI: {_format_number(data.get('pgi_pending_dn', 0))}",
            f"Pending POD: {_format_number(data.get('pod_pending_dn', 0))}",
            "",
            "🚚 *Delivery*",
            f"Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"POD: {data.get('pod_success_pct', 0):.1f}%",
            f"Avg Days: {data.get('avg_delivery_days', 0):.1f}",
            f"Avg POD: {data.get('avg_pod_days', 0):.1f}",
            "",
            "📈 *Performance*",
            f"Score: {data.get('business_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            f"Grade: {data.get('performance_grade', 'N/A')}",
            "",
            "📅 *Timeline*",
            f"First: {data.get('first_order', 'N/A')}",
            f"Last: {data.get('last_order', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_ranking(ranking: List[Dict[str, Any]], metric: str = "Revenue", limit: int = 10) -> str:
        lines = [f"🏆 *Dealer Rankings by {metric}*", ""]
        
        for i, item in enumerate(ranking[:limit], 1):
            dealer = item.get('dealer', 'Unknown')
            value = item.get('value', 'N/A')
            
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"{i}."
            
            lines.append(f"{medal} {dealer}: {value}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison(comparison: Dict[str, Any]) -> str:
        lines = ["🔄 *Dealer Comparison*", ""]
        
        for key, value in comparison.items():
            if key == "explanation":
                lines.extend(["", "💡 *Summary*", value])
            elif "_metrics" in key:
                dealer = key.replace("_metrics", "")
                lines.append(f"📊 *{dealer}*")
                for k, v in value.items():
                    lines.append(f"  {k}: {v}")
                lines.append("")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"🔍 No dealers found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} dealers")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dealer = item.get('dealer', 'Unknown')
            code = item.get('dealer_code', 'N/A')
            revenue = _format_currency(item.get('revenue', 0))
            pending = _format_number(item.get('pending_count', 0))
            
            lines.append(f"{i}. *{dealer}* (Code: {code})")
            lines.append(f"   Revenue: {revenue}")
            lines.append(f"   DN: {_format_number(item.get('dn_count', 0))}")
            lines.append(f"   Pending: {pending}")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_products(products: List[Dict[str, Any]], dealer: str) -> str:
        if not products:
            return f"📦 *Products - {dealer}*\n\nNo products found.\n\n0. Main Menu\n99. Back"
        
        lines = [f"📦 *Products - {dealer}*", ""]
        
        for i, item in enumerate(products[:10], 1):
            product = item.get('product', 'Unknown')
            revenue = _format_currency(item.get('revenue', 0))
            units = _format_number(item.get('units', 0))
            
            lines.append(f"{i}. *{product}*")
            lines.append(f"   Revenue: {revenue}")
            lines.append(f"   Units: {units}")
            lines.append("")
        
        if len(products) > 10:
            lines.append(f"... and {len(products) - 10} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_ai_response(query: str, response: str) -> str:
        return "\n".join([
            f"🤖 *AI Assistant*",
            "",
            f"📝 *Your Query:* {query}",
            "",
            response,
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# BLOCK 11: MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Domain AI Engine"""
    
    _instance: Optional["DealerAnalyticsService"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized") and self._initialized:
            return
        
        self._initialized = True
        self._service_name = "dealer_analytics"
        self._version = "3.0"
        
        # Initialize engines
        self._intent_engine = DealerIntentEngine()
        self._entity_extractor = DealerEntityExtractor()
        self._renderer = DealerRenderer()
        
        # Sessions
        self._sessions: Dict[str, DealerSession] = {}
        self._session_lock = threading.RLock()
        
        logger.info("=" * 60)
        logger.info(f"🚀 Dealer Domain AI Engine v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🤖 AI Engine: {'Active' if DEALER_AI_ENABLED else 'Limited'}")
        logger.info(f"   🔍 Semantic: {'Enabled' if DEALER_SEMANTIC_ENABLED else 'Disabled'}")
        logger.info("=" * 60)
    
    def _get_session(self, session_id: str) -> DealerSession:
        with self._session_lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = DealerSession(session_id=session_id)
                logger.info(f"🆕 New dealer session created for {session_id}")
            return self._sessions[session_id]
    
    def _get_db_session(self) -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """Main entry point for dealer processing"""
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📊 Dealer Query: '{message_clean}' from {sender}")
        
        session = self._get_session(sender)
        session.touch()
        
        # STEP 1: Check for exit
        if message_clean == "99":
            session.clear()
            logger.info(f"🚪 Dealer session exited for {sender}")
            return "99"
        
        # STEP 2: Check for menu commands
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # STEP 3: Check for menu options (1-5)
        if message_clean in ["1", "2", "3", "4", "5"]:
            return self._handle_menu_option(sender, message_clean, session)
        
        # STEP 4: Detect intent
        intent_result = self._intent_engine.detect_intent(message_clean)
        session.last_intent = intent_result.intent
        logger.info(f"🎯 Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})")
        
        # STEP 5: Process based on intent
        response = self._process_intent(session, intent_result, message_clean)
        
        # STEP 6: Update history
        session.add_history(message_clean, response)
        
        return response
    
    def _handle_menu_option(self, sender: str, option: str, session: DealerSession) -> str:
        """Handle menu options"""
        if option == "1":
            if session.current_dealer:
                return self._get_dashboard(session.current_dealer)
            return "🔍 *Enter dealer name:*\n\nType a dealer name.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return "🔍 *Search Dealers:*\n\nType 'search [keyword]'\n\nExamples:\n• search Lahore\n• search Zoom\n\n0. Main Menu\n99. Back"
        elif option == "3":
            return self._get_ranking("revenue", 10)
        elif option == "4":
            if session.current_dealer:
                return self._get_analytics(session.current_dealer)
            return "🔍 *Enter dealer name for analytics:*\n\n0. Main Menu\n99. Back"
        elif option == "5":
            return "🤖 *AI Assistant*\n\nAsk me anything about dealers:\n• Revenue trends\n• Performance analysis\n• Dealer comparisons\n• Product insights\n\n0. Main Menu\n99. Back"
        return self.get_main_menu()
    
    def _process_intent(self, session: DealerSession, intent_result: DealerIntentResult, message: str) -> str:
        """Process intent and return response"""
        
        intent = intent_result.intent
        entities = intent_result.entities
        
        # Extract dealer names from entities
        dealer_names = entities.get("dealers", [])
        
        # Handle exit
        if intent == DealerIntent.EXIT:
            session.clear()
            return "99"
        
        # Handle menu
        if intent == DealerIntent.MENU or intent == DealerIntent.HELP:
            return self.get_main_menu()
        
        # Handle ranking
        if intent == DealerIntent.RANKING:
            metric = entities.get("metrics", ["revenue"])[0] if entities.get("metrics") else "revenue"
            limit = entities.get("limit", 10)
            return self._get_ranking(metric, limit)
        
        # Handle comparison
        if intent == DealerIntent.COMPARISON and len(dealer_names) >= 2:
            return self._compare(dealer_names[0], dealer_names[1])
        
        # Handle search
        if intent == DealerIntent.SEARCH:
            query = message.replace("search", "").replace("find", "").strip()
            if query:
                return self._search(query)
            return "🔍 Please specify what to search."
        
        # Handle dealer-specific queries
        dealer_name = None
        
        # Check if we have a dealer from entities
        if dealer_names:
            dealer_name = dealer_names[0]
        elif session.current_dealer:
            dealer_name = session.current_dealer
        
        if dealer_name:
            session.set_dealer(dealer_name)
            
            if intent == DealerIntent.DASHBOARD:
                return self._get_dashboard(dealer_name)
            elif intent == DealerIntent.REVENUE:
                return self._get_revenue(dealer_name)
            elif intent == DealerIntent.UNITS:
                return self._get_units(dealer_name)
            elif intent == DealerIntent.PENDING:
                return self._get_pending(dealer_name)
            elif intent == DealerIntent.DELIVERY:
                return self._get_delivery(dealer_name)
            elif intent == DealerIntent.PERFORMANCE:
                return self._get_performance(dealer_name)
            elif intent == DealerIntent.PRODUCTS or intent == DealerIntent.MODELS:
                return self._get_products(dealer_name)
            elif intent == DealerIntent.HISTORY or intent == DealerIntent.TIMELINE:
                return self._get_timeline(dealer_name)
            elif intent == DealerIntent.SUMMARY:
                return self._get_summary(dealer_name)
            elif intent == DealerIntent.AI_ASK:
                return self._get_ai_response(message, dealer_name)
        
        # If we have a dealer name but no intent, show dashboard
        if dealer_names:
            return self._get_dashboard(dealer_names[0])
        
        # If it's a dealer name with no intent, show dashboard
        if len(message.split()) <= 3:
            dealer_name = self._resolve_dealer_name(message)
            if dealer_name:
                session.set_dealer(dealer_name)
                return self._get_dashboard(dealer_name)
        
        # AI fallback
        if DEALER_AI_ENABLED:
            return self._get_ai_response(message, session.current_dealer)
        
        # Unknown
        return self._get_help()
    
    def _resolve_dealer_name(self, text: str) -> Optional[str]:
        """Resolve dealer name from text"""
        text_clean = text.strip().lower()
        
        # Try database search
        try:
            with self._get_db_session() as session:
                repo = DealerRepository(session)
                results = repo.search(text_clean, limit=1)
                if results:
                    return results[0].get('dealer')
        except Exception:
            pass
        
        return None
    
    def _get_dashboard(self, dealer_name: str) -> str:
        """Get dealer dashboard"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return self._renderer.render_dashboard(dealer_name, data)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_ranking(self, metric: str = "revenue", limit: int = 10) -> str:
        """Get dealer ranking"""
        session = self._get_db_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            ranking = repo.get_ranking(metric, limit)
            session.close()
            
            if not ranking:
                return f"🏆 *Dealer Rankings by {metric.title()}*\n\nNo dealers found.\n\n0. Main Menu\n99. Back"
            
            return self._renderer.render_ranking(ranking, metric.title(), limit)
            
        except Exception as e:
            logger.error(f"Ranking error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching rankings.\n\n0. Main Menu\n99. Back"
    
    def _compare(self, dealer1: str, dealer2: str) -> str:
        """Compare two dealers"""
        session = self._get_db_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            comparison = repo.compare(dealer1, dealer2)
            session.close()
            
            if not comparison:
                return "⚠️ One or both dealers not found.\n\n0. Main Menu\n99. Back"
            
            return self._renderer.render_comparison(comparison)
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing dealers.\n\n0. Main Menu\n99. Back"
    
    def _search(self, query: str) -> str:
        """Search dealers"""
        session = self._get_db_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            results = repo.search(query)
            session.close()
            
            return self._renderer.render_search_results(query, results)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def _get_revenue(self, dealer_name: str) -> str:
        """Get dealer revenue"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            revenue = data.get('total_revenue', 0)
            return f"💰 *{dealer_name} Revenue*\n\n{_format_currency(revenue)}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Revenue error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching revenue for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_units(self, dealer_name: str) -> str:
        """Get dealer units"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            units = data.get('total_units', 0)
            return f"📦 *{dealer_name} Units*\n\n{_format_number(units)}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Units error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching units for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_pending(self, dealer_name: str) -> str:
        """Get dealer pending"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"⏳ *Pending Summary - {dealer_name}*",
                "",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
                f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
                f"Pending Revenue: {_format_currency(data.get('total_revenue', 0) * (data.get('pending_pct', 0) / 100))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Pending error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching pending for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_delivery(self, dealer_name: str) -> str:
        """Get dealer delivery"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🚚 *Delivery Summary - {dealer_name}*",
                "",
                f"Success Rate: {data.get('delivery_success_pct', 0):.1f}%",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
                f"Avg Delivery Days: {data.get('avg_delivery_days', 0):.1f}",
                f"Avg POD Days: {data.get('avg_pod_days', 0):.1f}",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Delivery error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching delivery for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_performance(self, dealer_name: str) -> str:
        """Get dealer performance"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📈 *Performance - {dealer_name}*",
                "",
                f"Score: {data.get('business_score', 0):.1f}/100",
                f"Status: {data.get('overall_status', 'Unknown')}",
                f"Grade: {data.get('performance_grade', 'N/A')}",
                f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
                f"POD Success: {data.get('pod_success_pct', 0):.1f}%",
                f"Pending DN: {_format_number(data.get('pending_dn', 0))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Performance error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching performance for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_products(self, dealer_name: str) -> str:
        """Get dealer products"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            products = repo.get_products(dealer_name)
            session.close()
            
            return self._renderer.render_products(products, dealer_name)
            
        except Exception as e:
            logger.error(f"Products error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching products for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_timeline(self, dealer_name: str) -> str:
        """Get dealer timeline"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📅 *Timeline - {dealer_name}*",
                "",
                f"First Order: {data.get('first_order', 'N/A')}",
                f"Last Order: {data.get('last_order', 'N/A')}",
                f"Total DN: {_format_number(data.get('total_dn', 0))}",
                f"Total Revenue: {_format_currency(data.get('total_revenue', 0))}",
                "",
                f"Status: {data.get('overall_status', 'Unknown')}",
                f"Score: {data.get('business_score', 0):.1f}/100",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Timeline error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching timeline for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_summary(self, dealer_name: str) -> str:
        """Get dealer summary"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            summary = f"📋 *Executive Summary - {dealer_name}*\n\n"
            summary += f"Revenue: {_format_currency(data.get('total_revenue', 0))}\n"
            summary += f"DN: {_format_number(data.get('total_dn', 0))}\n"
            summary += f"Units: {_format_number(data.get('total_units', 0))}\n"
            summary += f"Pending: {_format_number(data.get('pending_dn', 0))}\n"
            summary += f"Delivery: {data.get('delivery_success_pct', 0):.1f}%\n"
            summary += f"Score: {data.get('business_score', 0):.1f}/100\n"
            summary += f"Status: {data.get('overall_status', 'Unknown')}\n"
            summary += f"Grade: {data.get('performance_grade', 'N/A')}\n\n"
            summary += "0. Main Menu\n99. Back"
            
            return summary
            
        except Exception as e:
            logger.error(f"Summary error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching summary for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_analytics(self, dealer_name: str) -> str:
        """Get dealer analytics"""
        session = self._get_db_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            repo = DealerRepository(session)
            data = repo.get_dashboard(dealer_name)
            session.close()
            
            if not data:
                return f"⚠️ Dealer '{dealer_name}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📊 *Analytics - {dealer_name}*",
                "",
                "📈 *Revenue Analysis*",
                f"Total: {_format_currency(data.get('total_revenue', 0))}",
                f"Per DN: {_format_currency(data.get('avg_revenue_per_dn', 0))}",
                "",
                "📦 *Volume Analysis*",
                f"Total DN: {_format_number(data.get('total_dn', 0))}",
                f"Total Units: {_format_number(data.get('total_units', 0))}",
                f"Per DN: {round(data.get('total_units', 0) / max(1, data.get('total_dn', 1)), 2)}",
                "",
                "⏳ *Pending Analysis*",
                f"Pending: {_format_number(data.get('pending_dn', 0))}",
                f"PGI Pending: {_format_number(data.get('pgi_pending_dn', 0))}",
                f"POD Pending: {_format_number(data.get('pod_pending_dn', 0))}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Analytics error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching analytics for {dealer_name}\n\n0. Main Menu\n99. Back"
    
    def _get_ai_response(self, query: str, dealer_name: Optional[str] = None) -> str:
        """Get AI-powered response"""
        if not DEALER_AI_ENABLED:
            return "🤖 *AI Assistant*\n\nAI is currently disabled. Please try:\n• Dealer dashboard\n• Dealer search\n• Dealer rankings\n\n0. Main Menu\n99. Back"
        
        # Build context
        context = f"Dealer: {dealer_name or 'Not specified'}\n"
        
        # Get dealer data if available
        dealer_data = None
        if dealer_name:
            session = self._get_db_session()
            if session:
                try:
                    repo = DealerRepository(session)
                    dealer_data = repo.get_dashboard(dealer_name)
                    session.close()
                except Exception:
                    pass
        
        # Build response
        response_lines = ["🤖 *AI Assistant*", ""]
        response_lines.append(f"📝 *Question:* {query}")
        response_lines.append("")
        
        if dealer_data:
            response_lines.append("📊 *Dealer Data:*")
            response_lines.append(f"Revenue: {_format_currency(dealer_data.get('total_revenue', 0))}")
            response_lines.append(f"DN: {_format_number(dealer_data.get('total_dn', 0))}")
            response_lines.append(f"Delivery: {dealer_data.get('delivery_success_pct', 0):.1f}%")
            response_lines.append(f"Score: {dealer_data.get('business_score', 0):.1f}/100")
            response_lines.append("")
        
        # Try LLM response
        try:
            if self._intent_engine.groq_client:
                response = self._intent_engine.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {context}"},
                        {"role": "user", "content": query}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
                response_lines.append("💡 *AI Insights:*")
                response_lines.append(ai_response)
            elif self._intent_engine.openai_client:
                response = self._intent_engine.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": f"You are a dealer analytics expert. Provide insights for: {context}"},
                        {"role": "user", "content": query}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                ai_response = response.choices[0].message.content.strip()
                response_lines.append("💡 *AI Insights:*")
                response_lines.append(ai_response)
            else:
                response_lines.append("💡 *AI Insights:*")
                response_lines.append("AI insights are currently unavailable.")
                response_lines.append("Please try a specific command like:")
                response_lines.append("• Revenue analysis")
                response_lines.append("• Delivery performance")
                response_lines.append("• Dealer comparison")
        except Exception as e:
            logger.error(f"AI response error: {e}")
            response_lines.append("💡 *AI Insights:*")
            response_lines.append("Unable to generate AI insights at this time.")
            response_lines.append("Please try a specific dealer command.")
        
        response_lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(response_lines)
    
    def _get_help(self) -> str:
        """Get help message"""
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *Dealer Commands:*",
            "• Type dealer name for dashboard",
            "• top dealers - Show rankings",
            "• search [keyword] - Search dealers",
            "• compare [dealer1] and [dealer2]",
            "• revenue - Revenue of current dealer",
            "• pending - Pending of current dealer",
            "• delivery - Delivery of current dealer",
            "• products - Products of current dealer",
            "• history - Timeline of current dealer",
            "",
            "📌 *Current Dealer:*",
            "• Use 'menu' to see all options",
            "• Type '99' to return to main menu",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def health_check(self) -> Dict[str, Any]:
        """Health check"""
        with self._session_lock:
            active_sessions = len(self._sessions)
        
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "ai_enabled": DEALER_AI_ENABLED,
            "semantic_enabled": DEALER_SEMANTIC_ENABLED,
            "active_sessions": active_sessions,
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }


# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()

def get_dealer_service() -> DealerAnalyticsService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DealerAnalyticsService()
    return _service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DealerAnalyticsService",
    "DealerSession",
    "DealerIntent",
    "DealerMenuState",
    "get_dealer_service",
]
