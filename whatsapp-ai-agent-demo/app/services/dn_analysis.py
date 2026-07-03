"""
File: app/services/dn_analysis.py
Version: 29.0 - COMPLETE DN INTELLIGENCE ENGINE WITH AI

================================================================================
ARCHITECTURE
================================================================================

Main Menu → Press "1" → ENTER DN SERVICE
    ↓
EVERYTHING stays inside dn_analysis.py
    ↓
ALL DN questions answered by THIS FILE
    ↓
NEVER goes back to AI Provider
    ↓
ONLY "99" returns to Main Menu

================================================================================
AI LIBRARIES INTEGRATED
================================================================================

Intent Detection & Routing:
- semantic-router==0.1.11 → Intent classification
- spacy==3.8.7 → Named Entity Recognition (NER)
- rapidfuzz==3.13.0 → Fuzzy matching for entities

AI Explanations (Only for analysis, NOT data retrieval):
- groq==0.31.0 → Primary AI provider
- openai==1.99.9 → Fallback AI provider
- anthropic>=0.61.0 → Alternative AI provider

Text Processing:
- nltk==3.9.1 → Tokenization, stopwords
- textblob==0.19.0 → Text processing, sentiment
- tiktoken==0.9.0 → Token counting

Structured Output:
- litellm==1.74.9 → Unified AI interface
- pydantic-ai==0.8.1 → Structured AI responses
- instructor==1.10.0 → Structured extraction

Reranking:
- flashrank==0.2.10 → Search result reranking

================================================================================
STATUS: ENTERPRISE READY - FULLY INDEPENDENT
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
from collections import defaultdict
import hashlib
import math
import json

logger = logging.getLogger(__name__)

# ============================================================
# AI LIBRARIES - Graceful Loading
# ============================================================

# GROQ - Primary AI Provider
try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ Groq loaded")
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("⚠️ Groq not available")

# OpenAI - Fallback AI Provider
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
    logger.info("✅ OpenAI loaded")
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("⚠️ OpenAI not available")

# Anthropic - Alternative AI Provider
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
    logger.info("✅ Anthropic loaded")
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("⚠️ Anthropic not available")

# LiteLLM - Unified AI Interface
try:
    import litellm
    LITELLM_AVAILABLE = True
    logger.info("✅ LiteLLM loaded")
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("⚠️ LiteLLM not available")

# Pydantic AI - Structured Outputs
try:
    from pydantic_ai import Agent
    from pydantic_ai.models import GroqModel, OpenAIModel
    PYDANTIC_AI_AVAILABLE = True
    logger.info("✅ Pydantic AI loaded")
except ImportError:
    PYDANTIC_AI_AVAILABLE = False
    logger.warning("⚠️ Pydantic AI not available")

# Instructor - Structured Extraction
try:
    import instructor
    INSTRUCTOR_AVAILABLE = True
    logger.info("✅ Instructor loaded")
except ImportError:
    INSTRUCTOR_AVAILABLE = False
    logger.warning("⚠️ Instructor not available")

# spaCy - NER and NLP
try:
    import spacy
    SPACY_AVAILABLE = True
    nlp = None
    try:
        nlp = spacy.load("en_core_web_sm")
        logger.info("✅ spaCy loaded")
    except OSError:
        try:
            import subprocess
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], capture_output=True)
            nlp = spacy.load("en_core_web_sm")
            logger.info("✅ spaCy downloaded and loaded")
        except Exception:
            logger.warning("⚠️ spaCy model not available")
except ImportError:
    SPACY_AVAILABLE = False
    nlp = None
    logger.warning("⚠️ spaCy not available")

# Semantic Router - Intelligent Routing
try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
    logger.info("✅ Semantic Router loaded")
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False
    logger.warning("⚠️ Semantic Router not available")

# FlashRank - Result Reranking
try:
    from flashrank import Ranker
    FLASHRANK_AVAILABLE = True
    ranker = Ranker()
    logger.info("✅ FlashRank loaded")
except ImportError:
    FLASHRANK_AVAILABLE = False
    ranker = None
    logger.warning("⚠️ FlashRank not available")

# RapidFuzz - Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
    logger.info("✅ RapidFuzz loaded")
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("⚠️ RapidFuzz not available")

# NLTK - Text Processing
try:
    import nltk
    NLTK_AVAILABLE = True
    try:
        nltk.data.find('tokenizers/punkt')
    except LookupError:
        nltk.download('punkt', quiet=True)
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        nltk.download('stopwords', quiet=True)
    logger.info("✅ NLTK loaded")
except ImportError:
    NLTK_AVAILABLE = False
    logger.warning("⚠️ NLTK not available")

# TextBlob - Text Processing
try:
    from textblob import TextBlob
    TEXTBLOB_AVAILABLE = True
    logger.info("✅ TextBlob loaded")
except ImportError:
    TEXTBLOB_AVAILABLE = False
    logger.warning("⚠️ TextBlob not available")

# Tiktoken - Token Counting
try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
    logger.info("✅ Tiktoken loaded")
except ImportError:
    TIKTOKEN_AVAILABLE = False
    logger.warning("⚠️ Tiktoken not available")

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, and_, desc, asc, case, extract
    from sqlalchemy.orm import Session
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
except ImportError:
    DB_AVAILABLE = False
    logger.warning("⚠️ Database not available")

# ============================================================
# CONFIGURATION
# ============================================================

CACHE_TTL = int(os.getenv("DN_ANALYTICS_CACHE_TTL", "300"))
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")
AI_MODEL = os.getenv("AI_MODEL", "llama3-70b-8192")
USE_AI_ENHANCEMENT = os.getenv("USE_AI_ENHANCEMENT", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
SLA_TARGET_DAYS = int(os.getenv("DN_SLA_TARGET_DAYS", "3"))

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DNContext:
    """Enterprise DN session context - SMART MEMORY"""
    current_dn: Optional[str] = None
    in_dn_service: bool = False
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_response: Optional[str] = None
    search_results: Optional[List[Dict[str, Any]]] = None
    session_start: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    # Smart Memory - remembers everything
    current_customer: Optional[str] = None
    current_dealer: Optional[str] = None
    current_warehouse: Optional[str] = None
    current_city: Optional[str] = None
    current_sales_office: Optional[str] = None
    current_sales_manager: Optional[str] = None
    current_division: Optional[str] = None
    current_order_type: Optional[str] = None
    
    # Comparison memory
    comparison_dns: List[str] = field(default_factory=list)
    last_comparison: Optional[Dict[str, Any]] = None
    
    # Dashboard cache
    current_dashboard: Optional[Dict[str, Any]] = None
    
    def update_from_dashboard(self, dashboard: Dict[str, Any]):
        """Update context from dashboard data"""
        self.current_dn = dashboard.get('dn_no')
        self.current_customer = dashboard.get('customer_name') or dashboard.get('customer_code')
        self.current_dealer = dashboard.get('dealer_name') or dashboard.get('dealer_code')
        self.current_warehouse = dashboard.get('warehouse')
        self.current_city = dashboard.get('ship_to_city')
        self.current_sales_office = dashboard.get('sales_office')
        self.current_sales_manager = dashboard.get('sales_manager')
        self.current_division = dashboard.get('division')
        self.current_order_type = dashboard.get('order_type')
        self.current_dashboard = dashboard

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    if value is None:
        return default
    return str(value).strip() or default

def _format_date(value: Any) -> str:
    if not value:
        return "N/A"
    if hasattr(value, "strftime"):
        return value.strftime("%d-%b-%Y")
    return str(value)

def _is_valid_dn(dn: str) -> bool:
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12

def _extract_dn(text: str) -> Optional[str]:
    match = re.search(r'\b(\d{8,12})\b', text)
    return match.group(1) if match else None

def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _calculate_days(date1: Any, date2: Any) -> Optional[int]:
    """Calculate days between two dates"""
    if not date1 or not date2:
        return None
    if hasattr(date1, "date"):
        date1 = date1.date()
    if hasattr(date2, "date"):
        date2 = date2.date()
    if isinstance(date1, date) and isinstance(date2, date):
        return (date2 - date1).days
    return None

def _get_status_emoji(status: str) -> str:
    """Get emoji for status"""
    status_map = {
        "Delivered": "✅",
        "Completed": "✅",
        "In Transit": "🚚",
        "Pending PGI": "⏳",
        "Pending POD": "📋",
        "Pending DN": "📦",
        "Delayed": "⚠️",
        "Overdue": "🚨",
        "SLA Breach": "🚨",
    }
    return status_map.get(status, "📊")

# ============================================================
# AI CONTENT RECOGNITION ENGINE
# ============================================================

class DNContentRecognizer:
    """
    AI-powered content recognition for DN queries
    Uses semantic-router, spaCy, and RapidFuzz for intent detection
    Uses Groq/OpenAI only for explanations (NOT data retrieval)
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        
        self._client = None
        self._provider = AI_PROVIDER
        
        # Initialize AI client for explanations only
        self._init_client()
        
        # Initialize semantic router for intent detection
        self._router = self._init_router()
        
        # Cache for responses
        self._cache: Dict[str, str] = {}
        self._cache_lock = threading.RLock()
        
        # Known entities cache for fuzzy matching
        self._known_customers: List[str] = []
        self._known_dealers: List[str] = []
        self._known_warehouses: List[str] = []
        self._known_cities: List[str] = []
        self._known_products: List[str] = []
        self._entity_cache_time: Optional[datetime] = None
        
        logger.info(f"🧠 DNContentRecognizer initialized (Provider: {AI_PROVIDER})")
    
    def _init_client(self):
        """Initialize AI client for explanations only"""
        if AI_PROVIDER == "groq" and GROQ_AVAILABLE:
            try:
                self._client = Groq()
                logger.info("✅ Groq client initialized")
                return
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")
        
        if AI_PROVIDER == "openai" and OPENAI_AVAILABLE:
            try:
                self._client = OpenAI()
                logger.info("✅ OpenAI client initialized")
                self._provider = "openai"
                return
            except Exception as e:
                logger.warning(f"OpenAI init failed: {e}")
        
        if ANTHROPIC_AVAILABLE:
            try:
                self._client = Anthropic()
                logger.info("✅ Anthropic client initialized")
                self._provider = "anthropic"
                return
            except Exception as e:
                logger.warning(f"Anthropic init failed: {e}")
        
        logger.warning("⚠️ No AI client available - AI explanations disabled")
        self._client = None
    
    def _init_router(self):
        """Initialize semantic router with all DN intents"""
        if not SEMANTIC_ROUTER_AVAILABLE:
            return None
        
        try:
            routes = [
                Route(name="dashboard", utterances=[
                    "show dn", "dn dashboard", "dn details", "dn info", "dn summary",
                    "tell me about dn", "what is dn", "dn overview"
                ]),
                Route(name="status", utterances=[
                    "dn status", "status of dn", "where is dn", "track dn",
                    "is dn delivered", "current status", "delivery status"
                ]),
                Route(name="revenue", utterances=[
                    "dn revenue", "revenue from dn", "dn amount", "value of dn",
                    "how much is dn", "dn value", "total revenue"
                ]),
                Route(name="units", utterances=[
                    "dn units", "dn quantity", "how many units", "dn qty",
                    "number of units", "total units", "units count"
                ]),
                Route(name="customer", utterances=[
                    "dn customer", "customer details", "who is customer",
                    "customer info", "show customer", "customer name"
                ]),
                Route(name="dealer", utterances=[
                    "dn dealer", "dealer details", "who is dealer",
                    "dealer info", "show dealer", "dealer name"
                ]),
                Route(name="warehouse", utterances=[
                    "dn warehouse", "warehouse details", "which warehouse",
                    "warehouse info", "show warehouse"
                ]),
                Route(name="city", utterances=[
                    "dn city", "city details", "ship to city",
                    "delivery city", "destination city"
                ]),
                Route(name="sales_office", utterances=[
                    "sales office", "sales team", "who manages",
                    "office details", "show sales office"
                ]),
                Route(name="sales_manager", utterances=[
                    "sales manager", "manager name", "who is manager",
                    "sales rep", "show manager"
                ]),
                Route(name="timeline", utterances=[
                    "dn timeline", "timeline", "chronology",
                    "sequence of events", "event timeline"
                ]),
                Route(name="history", utterances=[
                    "dn history", "history", "past events",
                    "what happened", "show history"
                ]),
                Route(name="transit", utterances=[
                    "transit", "transit time", "transit days",
                    "how long in transit", "transit analysis"
                ]),
                Route(name="sla", utterances=[
                    "sla", "sla compliance", "service level",
                    "delivery time", "sla status"
                ]),
                Route(name="delay", utterances=[
                    "delay", "why delayed", "delayed days",
                    "is it delayed", "delay analysis"
                ]),
                Route(name="pending", utterances=[
                    "pending dns", "pending deliveries", "overdue dns",
                    "pending list", "show pending"
                ]),
                Route(name="search", utterances=[
                    "search dn", "find dn", "lookup dn",
                    "search for", "find dns"
                ]),
                Route(name="compare", utterances=[
                    "compare dns", "dn vs dn", "comparison",
                    "which is better", "compare"
                ]),
                Route(name="trend", utterances=[
                    "dn trends", "dn pattern", "over time",
                    "weekly", "monthly", "trend analysis"
                ]),
                Route(name="forecast", utterances=[
                    "forecast", "predict", "future",
                    "expected", "projection"
                ]),
                Route(name="insights", utterances=[
                    "insights", "analysis", "key findings",
                    "what does data show", "dn analysis"
                ]),
                Route(name="recommendations", utterances=[
                    "recommendations", "suggestions", "improve",
                    "what to do", "action items"
                ]),
                Route(name="explain", utterances=[
                    "explain", "what is", "tell me about",
                    "describe", "explain this"
                ]),
                Route(name="root_cause", utterances=[
                    "root cause", "why", "reason", "cause",
                    "what happened", "why delayed"
                ]),
                Route(name="executive_summary", utterances=[
                    "executive summary", "summary", "overview",
                    "brief", "quick summary"
                ]),
                Route(name="division", utterances=[
                    "division", "department", "business unit",
                    "show division"
                ]),
                Route(name="order_type", utterances=[
                    "order type", "type", "order category",
                    "show order type"
                ]),
                Route(name="material", utterances=[
                    "material", "material number", "product code",
                    "show material"
                ]),
                Route(name="model", utterances=[
                    "model", "customer model", "product model",
                    "show model"
                ]),
                Route(name="pgi", utterances=[
                    "pgi", "goods issue", "pgi status",
                    "show pgi"
                ]),
                Route(name="pod", utterances=[
                    "pod", "proof of delivery", "pod status",
                    "show pod"
                ]),
                Route(name="delivery_days", utterances=[
                    "delivery days", "how long", "delivery time",
                    "total days"
                ]),
                Route(name="distance", utterances=[
                    "distance", "how far", "distance from warehouse",
                    "travel distance"
                ]),
            ]
            encoder = HuggingFaceEncoder()
            router = Router(routes=routes, encoder=encoder)
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            return router
        except Exception as e:
            logger.warning(f"Semantic Router init failed: {e}")
            return None
    
    def _get_known_entities(self, session: Session) -> Dict[str, List[str]]:
        """Load known entities from database for fuzzy matching"""
        try:
            # Get unique customers
            customers = session.query(
                func.distinct(DeliveryReport.customer_name)
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).all()
            self._known_customers = [c[0] for c in customers if c[0]]
            
            # Get unique dealers
            dealers = session.query(
                func.distinct(DeliveryReport.dealer)
            ).filter(
                DeliveryReport.dealer.isnot(None)
            ).all()
            self._known_dealers = [d[0] for d in dealers if d[0]]
            
            # Get unique warehouses
            warehouses = session.query(
                func.distinct(DeliveryReport.warehouse)
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).all()
            self._known_warehouses = [w[0] for w in warehouses if w[0]]
            
            # Get unique cities
            cities = session.query(
                func.distinct(DeliveryReport.ship_to_city)
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).all()
            self._known_cities = [c[0] for c in cities if c[0]]
            
            # Get unique products
            products = session.query(
                func.distinct(DeliveryReport.customer_model)
            ).filter(
                DeliveryReport.customer_model.isnot(None)
            ).all()
            self._known_products = [p[0] for p in products if p[0]]
            
            self._entity_cache_time = datetime.now()
            
        except Exception as e:
            logger.warning(f"Failed to load known entities: {e}")
    
    def _fuzzy_match_entity(self, query: str, entities: List[str], threshold: int = 80) -> Optional[str]:
        """Fuzzy match entity using RapidFuzz"""
        if not RAPIDFUZZ_AVAILABLE or not entities:
            return None
        
        try:
            matches = process.extract(query, entities, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= threshold:
                return matches[0][0]
        except Exception:
            pass
        return None
    
    def _extract_entities_spacy(self, query: str) -> Dict[str, Any]:
        """Extract entities using spaCy NER"""
        entities = {}
        if not SPACY_AVAILABLE or not nlp:
            return entities
        
        try:
            doc = nlp(query)
            for ent in doc.ents:
                if ent.label_ in ["GPE", "LOC"]:
                    entities["location"] = ent.text
                elif ent.label_ == "ORG":
                    entities["organization"] = ent.text
                elif ent.label_ == "PERSON":
                    entities["person"] = ent.text
                elif ent.label_ == "DATE":
                    entities["date"] = ent.text
                elif ent.label_ == "PRODUCT":
                    entities["product"] = ent.text
        except Exception:
            pass
        
        return entities
    
    def recognize(self, query: str, session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Recognize intent and extract entities using:
        1. Semantic Router for intent classification
        2. spaCy for NER
        3. RapidFuzz for fuzzy matching
        4. Keyword fallback
        """
        result = {
            "intent": "unknown",
            "confidence": 0.0,
            "entities": {},
            "dn": None,
            "explanation": "",
            "command": query,
            "requires_ai": False,
            "follow_up": False
        }
        
        query_clean = query.strip()
        query_lower = query_lower = query_clean.lower()
        
        # Extract DN number
        dn = _extract_dn(query_clean)
        if dn:
            result["dn"] = dn
            result["entities"]["dn"] = dn
        
        # Load known entities if needed
        if session and DB_AVAILABLE:
            try:
                self._get_known_entities(session)
            except Exception:
                pass
        
        # STEP 1: Semantic Router for intent detection
        if self._router:
            try:
                route_result = self._router.route(query_clean)
                if route_result and hasattr(route_result, 'name'):
                    result["intent"] = route_result.name
                    result["confidence"] = 0.85
                    result["explanation"] = f"Semantic routing: {route_result.name}"
                    # Log the routing
                    logger.info(f"🎯 Semantic Router: {route_result.name}")
            except Exception as e:
                logger.debug(f"Semantic Router error: {e}")
        
        # STEP 2: spaCy NER for entity extraction
        spacy_entities = self._extract_entities_spacy(query_clean)
        result["entities"].update(spacy_entities)
        
        # STEP 3: Fuzzy matching for entities
        if RAPIDFUZZ_AVAILABLE:
            # Try to match customer
            if self._known_customers:
                customer = self._fuzzy_match_entity(query_clean, self._known_customers)
                if customer:
                    result["entities"]["customer"] = customer
                    result["entities"]["customer_fuzzy"] = True
            
            # Try to match dealer
            if self._known_dealers:
                dealer = self._fuzzy_match_entity(query_clean, self._known_dealers)
                if dealer:
                    result["entities"]["dealer"] = dealer
                    result["entities"]["dealer_fuzzy"] = True
            
            # Try to match warehouse
            if self._known_warehouses:
                warehouse = self._fuzzy_match_entity(query_clean, self._known_warehouses)
                if warehouse:
                    result["entities"]["warehouse"] = warehouse
                    result["entities"]["warehouse_fuzzy"] = True
            
            # Try to match city
            if self._known_cities:
                city = self._fuzzy_match_entity(query_clean, self._known_cities)
                if city:
                    result["entities"]["city"] = city
                    result["entities"]["city_fuzzy"] = True
            
            # Try to match product
            if self._known_products:
                product = self._fuzzy_match_entity(query_clean, self._known_products)
                if product:
                    result["entities"]["product"] = product
                    result["entities"]["product_fuzzy"] = True
        
        # STEP 4: Intent override if DN detected and no intent
        if result["dn"] and result["intent"] == "unknown":
            result["intent"] = "dashboard"
            result["confidence"] = 0.9
            result["explanation"] = "DN number detected"
        
        # STEP 5: Keyword fallback for specific patterns
        if result["intent"] == "unknown" or result["confidence"] < 0.5:
            if "status" in query_lower or "track" in query_lower or "where" in query_lower:
                result["intent"] = "status"
                result["confidence"] = 0.7
            elif "revenue" in query_lower or "amount" in query_lower or "value" in query_lower or "how much" in query_lower:
                result["intent"] = "revenue"
                result["confidence"] = 0.7
            elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower or "how many" in query_lower:
                result["intent"] = "units"
                result["confidence"] = 0.7
            elif "customer" in query_lower or "who" in query_lower:
                result["intent"] = "customer"
                result["confidence"] = 0.7
            elif "dealer" in query_lower:
                result["intent"] = "dealer"
                result["confidence"] = 0.7
            elif "warehouse" in query_lower or "wh" in query_lower:
                result["intent"] = "warehouse"
                result["confidence"] = 0.7
            elif "city" in query_lower:
                result["intent"] = "city"
                result["confidence"] = 0.7
            elif "sales office" in query_lower or "sales team" in query_lower:
                result["intent"] = "sales_office"
                result["confidence"] = 0.7
            elif "sales manager" in query_lower or "manager" in query_lower:
                result["intent"] = "sales_manager"
                result["confidence"] = 0.7
            elif "pgi" in query_lower:
                result["intent"] = "pgi"
                result["confidence"] = 0.7
            elif "pod" in query_lower:
                result["intent"] = "pod"
                result["confidence"] = 0.7
            elif "timeline" in query_lower or "chronology" in query_lower:
                result["intent"] = "timeline"
                result["confidence"] = 0.7
            elif "history" in query_lower:
                result["intent"] = "history"
                result["confidence"] = 0.7
            elif "transit" in query_lower:
                result["intent"] = "transit"
                result["confidence"] = 0.7
            elif "sla" in query_lower:
                result["intent"] = "sla"
                result["confidence"] = 0.7
            elif "delay" in query_lower or "delayed" in query_lower:
                result["intent"] = "delay"
                result["confidence"] = 0.7
            elif "pending" in query_lower:
                result["intent"] = "pending"
                result["confidence"] = 0.7
            elif "compare" in query_lower or "vs" in query_lower:
                result["intent"] = "compare"
                result["confidence"] = 0.7
            elif "search" in query_lower or "find" in query_lower:
                result["intent"] = "search"
                result["confidence"] = 0.7
            elif "trend" in query_lower:
                result["intent"] = "trend"
                result["confidence"] = 0.7
            elif "forecast" in query_lower or "predict" in query_lower:
                result["intent"] = "forecast"
                result["confidence"] = 0.7
            elif "insight" in query_lower:
                result["intent"] = "insights"
                result["confidence"] = 0.7
            elif "recommend" in query_lower or "suggest" in query_lower:
                result["intent"] = "recommendations"
                result["confidence"] = 0.7
            elif "explain" in query_lower or "what is" in query_lower:
                result["intent"] = "explain"
                result["confidence"] = 0.7
                result["requires_ai"] = True
            elif "root cause" in query_lower or "why" in query_lower:
                result["intent"] = "root_cause"
                result["confidence"] = 0.7
                result["requires_ai"] = True
        
        # STEP 6: Check if it's a follow-up question (no DN, short query)
        if not result["dn"] and len(query_clean.split()) <= 4:
            result["follow_up"] = True
        
        # STEP 7: AI explanation requests
        if result["intent"] in ["explain", "root_cause", "executive_summary", "recommendations"]:
            result["requires_ai"] = True
        
        return result
    
    def generate_ai_response(self, query: str, data: Dict[str, Any], context: DNContext) -> str:
        """
        Generate AI explanation using Groq/OpenAI/Anthropic
        ONLY for explanations, NOT for data retrieval
        """
        if not self._client or not USE_AI_ENHANCEMENT:
            return None
        
        # Build context string
        context_str = self._build_context_string(data, context)
        
        # Build prompt based on intent
        intent = context.last_intent or "explain"
        
        prompts = {
            "explain": f"""You are a logistics DN expert. Explain this DN in simple terms for a business user.

DN Data:
{context_str}

Provide:
1. A clear, simple explanation
2. Key highlights
3. Current status
4. What it means for the business

Keep it concise for WhatsApp. Use emojis. Max 300 words.""",

            "root_cause": f"""You are a logistics DN expert. Analyze why this DN is delayed or problematic.

DN Data:
{context_str}

Provide:
1. Root cause analysis
2. Contributing factors
3. Impact on business
4. What went wrong

Keep it concise for WhatsApp. Use emojis. Max 300 words.""",

            "executive_summary": f"""You are a logistics DN expert. Provide an executive summary of this DN.

DN Data:
{context_str}

Provide:
1. One-sentence summary
2. Key metrics
3. Status assessment
4. Recommendation

Keep it concise for WhatsApp. Use emojis. Max 200 words.""",

            "recommendations": f"""You are a logistics DN expert. Provide recommendations for this DN.

DN Data:
{context_str}

Provide:
1. What to do next
2. Potential risks
3. Improvement opportunities
4. Action items

Keep it concise for WhatsApp. Use emojis. Max 200 words.""",

            "insights": f"""You are a logistics DN expert. Provide insights on this DN.

DN Data:
{context_str}

Provide:
1. Key findings
2. Patterns observed
3. What it tells us
4. Business implications

Keep it concise for WhatsApp. Use emojis. Max 250 words."""
        }
        
        prompt = prompts.get(intent, prompts["explain"])
        
        try:
            if self._provider == "groq":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN expert. Provide concise, business-focused analysis for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=400
                )
                return response.choices[0].message.content
            
            elif self._provider == "openai":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN expert. Provide concise, business-focused analysis for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=400
                )
                return response.choices[0].message.content
            
            elif self._provider == "anthropic":
                response = self._client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=400,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text
                
        except Exception as e:
            logger.error(f"AI generation failed: {e}")
        
        return None
    
    def _build_context_string(self, data: Dict[str, Any], context: DNContext) -> str:
        """Build context string for AI prompts"""
        lines = [
            f"DN Number: {data.get('dn_no', 'N/A')}",
            f"Division: {data.get('division', 'N/A')}",
            f"Order Type: {data.get('order_type', 'N/A')}",
            f"Customer: {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            f"Dealer: {data.get('dealer_name', data.get('dealer_code', 'N/A'))}",
            f"Sales Office: {data.get('sales_office', 'N/A')}",
            f"Sales Manager: {data.get('sales_manager', 'N/A')}",
            f"Warehouse: {data.get('warehouse', 'N/A')}",
            f"City: {data.get('ship_to_city', 'N/A')}",
            f"Status: {data.get('delivery_status', 'Pending')}",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Units: {data.get('total_units', 0):,}",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"PGI Date: {_format_date(data.get('good_issue_date'))}",
            f"POD Date: {_format_date(data.get('pod_date'))}",
            f"Delivery Days: {data.get('delivery_days', 'N/A')}",
            f"SLA Compliant: {'Yes' if data.get('sla_compliant') else 'No'}",
            f"DN Age: {data.get('dn_age', 'N/A')} Days",
        ]
        return "\n".join(lines)

# ============================================================
# DN MENU RENDERER
# ============================================================

class DNMenuRenderer:
    """DN Menu Renderer - WhatsApp Format"""
    
    @staticmethod
    def render_main_menu() -> str:
        """Main DN menu - shown when you press "1" or type "menu" """
        return "\n".join([
            "📦 *DN INTELLIGENCE ENGINE*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. Pending DN",
            "4. Search DN",
            "5. Compare DN",
            "6. AI Insights",
            "7. Trends",
            "8. Forecast",
            "99. Back to Main",
            "",
            "📌 *Smart Commands (Uses current DN):*",
            "",
            "📊 *Info:* status, revenue, units, customer, dealer",
            "📍 *Location:* warehouse, city, sales-office",
            "📅 *Timeline:* timeline, history, transit, delivery-days",
            "📋 *Status:* pgi, pod, sla, delay, pending",
            "🤖 *AI:* explain, insights, recommendations, root-cause",
            "🔍 *Search:* search [keyword]",
            "🔄 *Compare:* compare DN1 DN2",
            "",
            "📌 *Quick Commands:*",
            "• Type DN number for full dashboard",
            "• Type 'pending' for pending list",
            "• Type 'search [keyword]' to search",
            "• Type 'compare [DN1] [DN2]' to compare",
            "",
            "💡 *Follow-up Commands:*",
            "• After viewing a DN, just type 'status', 'revenue', etc.",
            "• No need to type the DN again!",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_enterprise_dashboard(data: Dict[str, Any], context: DNContext) -> str:
        """Complete DN dashboard - ALL 300+ questions answered"""
        dn_no = data.get('dn_no', 'N/A')
        
        # Calculate KPIs
        dn_create = data.get('dn_create_date')
        good_issue = data.get('good_issue_date')
        pod_date = data.get('pod_date')
        
        delivery_days = _calculate_days(dn_create, pod_date)
        pod_days = _calculate_days(good_issue, pod_date) if good_issue and pod_date else None
        pgi_days = _calculate_days(dn_create, good_issue) if dn_create and good_issue else None
        transit_days = _calculate_days(good_issue, pod_date) if good_issue and pod_date else None
        dn_age = (datetime.now().date() - dn_create).days if dn_create else None
        
        # SLA status
        sla_compliant = delivery_days is not None and delivery_days <= SLA_TARGET_DAYS
        
        # Get status emoji
        status = data.get('delivery_status', 'Pending')
        status_emoji = _get_status_emoji(status)
        
        lines = [
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📦 *DN {dn_no}* {status_emoji}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📊 *BASIC INFORMATION*",
            f"DN Work: {data.get('dn_work', 'N/A')}",
            f"Order Type: {data.get('order_type', 'N/A')}",
            f"Division: {data.get('division', 'N/A')}",
            "",
            "👤 *CUSTOMER*",
            f"Code: {data.get('customer_code', 'N/A')}",
            f"Name: {data.get('customer_name', 'N/A')}",
            "",
            "🏪 *DEALER*",
            f"Code: {data.get('dealer_code', 'N/A')}",
            f"Name: {data.get('dealer_name', 'N/A')}",
            "",
            "🏢 *SALES*",
            f"Office: {data.get('sales_office', 'N/A')}",
            f"Manager: {data.get('sales_manager', 'N/A')}",
            "",
            "🏭 *WAREHOUSE*",
            f"Name: {data.get('warehouse', 'N/A')}",
            f"Code: {data.get('warehouse_code', 'N/A')}",
            "",
            "📍 *DELIVERY*",
            f"City: {data.get('ship_to_city', 'N/A')}",
            f"Location: {data.get('delivery_location', 'N/A')}",
            "",
            "📦 *PRODUCT*",
            f"Material: {data.get('material_no', 'N/A')}",
            f"Model: {data.get('customer_model', 'N/A')}",
            "",
            "📊 *QUANTITIES*",
            f"Units: {data.get('total_units', 0):,}",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Avg Price: PKR {data.get('avg_price', 0):,.2f}",
            "",
            "📅 *DATES*",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"PGI: {_format_date(data.get('good_issue_date'))}",
            f"POD: {_format_date(data.get('pod_date'))}",
            "",
            "📈 *STATUS*",
            f"Delivery: {data.get('delivery_status', 'Pending')}",
            f"PGI: {data.get('pgi_status', 'Pending')}",
            f"POD: {data.get('pod_status', 'Pending')}",
            f"Pending: {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
            "",
            "📊 *KPIs*",
            f"DN Age: {dn_age if dn_age is not None else 'N/A'} Days",
            f"Delivery Days: {delivery_days if delivery_days is not None else 'N/A'}",
            f"POD Days: {pod_days if pod_days is not None else 'N/A'}",
            f"PGI Days: {pgi_days if pgi_days is not None else 'N/A'}",
            f"Transit Days: {transit_days if transit_days is not None else 'N/A'}",
            "",
            "⚡ *SLA*",
            f"SLA Target: {SLA_TARGET_DAYS} Days",
            f"SLA Status: {'✅ Compliant' if sla_compliant else '❌ Breached'}",
            "",
            "ℹ️ *INSIGHTS*",
        ]
        
        # Add insights if available
        insights = data.get('insights', [])
        if insights:
            for insight in insights[:3]:
                lines.append(f"• {insight}")
        else:
            lines.append("• No insights available")
        
        # Add recommendations if available
        recommendations = data.get('recommendations', [])
        if recommendations:
            lines.append("")
            lines.append("🎯 *RECOMMENDATIONS*")
            for rec in recommendations[:2]:
                lines.append(f"• {rec}")
        
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "💡 *Follow-up Commands:*",
            "• status, revenue, units, customer, dealer",
            "• warehouse, city, timeline, history",
            "• pgi, pod, sla, delay, explain",
            "",
            "0. Main Menu",
            "99. Back to Main"
        ])
        
        return "\n".join(lines)
    
    @staticmethod
    def render_dn_status(data: Dict[str, Any]) -> str:
        """Render DN status"""
        dn_no = data.get('dn_no', 'N/A')
        status = data.get('delivery_status', 'Pending')
        status_emoji = _get_status_emoji(status)
        
        return "\n".join([
            f"📊 *DN {dn_no} - Status*",
            "",
            f"{status_emoji} *{status}*",
            "",
            f"PGI: {data.get('pgi_status', 'Pending')}",
            f"POD: {data.get('pod_status', 'Pending')}",
            f"Pending: {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
            "",
            f"Created: {_format_date(data.get('dn_create_date'))}",
            f"Customer: {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_pending_list(items: List[Dict[str, Any]]) -> str:
        """Render pending DNs list"""
        if not items:
            return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
        
        lines = ["📋 *Pending DNs*", ""]
        lines.append(f"Total: {len(items)}")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            status = item.get('delivery_status', 'Pending')
            days = item.get('pending_days', 0)
            status_emoji = _get_status_emoji(status)
            lines.append(f"{i}. {status_emoji} *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Status: {status}")
            if days > 0:
                lines.append(f"   Pending: {days} Days")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        """Render search results"""
        if not items:
            return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} DNs")
        lines.append("")
        
        for i, item in enumerate(items[:15], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            city = item.get('ship_to_city', 'N/A')
            status = item.get('delivery_status', 'Pending')
            status_emoji = _get_status_emoji(status)
            lines.append(f"{i}. {status_emoji} *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   City: {city} | Status: {status}")
            lines.append("")
        
        if len(items) > 15:
            lines.append(f"... and {len(items) - 15} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_comparison(dn1_data: Dict[str, Any], dn2_data: Dict[str, Any]) -> str:
        """Render DN comparison"""
        dn1 = dn1_data.get('dn_no', 'N/A')
        dn2 = dn2_data.get('dn_no', 'N/A')
        
        # Calculate KPIs
        def get_metric(data):
            return {
                'revenue': data.get('total_revenue', 0),
                'units': data.get('total_units', 0),
                'status': data.get('delivery_status', 'Pending'),
                'city': data.get('ship_to_city', 'N/A'),
                'warehouse': data.get('warehouse', 'N/A'),
                'customer': data.get('customer_name', data.get('customer_code', 'N/A')),
                'dealer': data.get('dealer_name', data.get('dealer_code', 'N/A')),
                'delivery_days': _calculate_days(data.get('dn_create_date'), data.get('pod_date')),
                'sla': '✅' if _calculate_days(data.get('dn_create_date'), data.get('pod_date')) <= SLA_TARGET_DAYS else '❌',
            }
        
        m1 = get_metric(dn1_data)
        m2 = get_metric(dn2_data)
        
        lines = [
            f"🔄 *Comparison: DN {dn1} vs DN {dn2}*",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "📊 *Revenue*",
            f"DN {dn1}: PKR {m1['revenue']:,.2f}",
            f"DN {dn2}: PKR {m2['revenue']:,.2f}",
            f"🏆 Winner: {'DN ' + dn1 if m1['revenue'] > m2['revenue'] else 'DN ' + dn2}",
            "",
            "📦 *Units*",
            f"DN {dn1}: {m1['units']:,}",
            f"DN {dn2}: {m2['units']:,}",
            "",
            "📋 *Status*",
            f"DN {dn1}: {_get_status_emoji(m1['status'])} {m1['status']}",
            f"DN {dn2}: {_get_status_emoji(m2['status'])} {m2['status']}",
            "",
            "📍 *Warehouse*",
            f"DN {dn1}: {m1['warehouse']}",
            f"DN {dn2}: {m2['warehouse']}",
            "",
            "👤 *Customer*",
            f"DN {dn1}: {m1['customer']}",
            f"DN {dn2}: {m2['customer']}",
            "",
            "🏪 *Dealer*",
            f"DN {dn1}: {m1['dealer']}",
            f"DN {dn2}: {m2['dealer']}",
            "",
            "⏱️ *Delivery Days*",
            f"DN {dn1}: {m1['delivery_days'] if m1['delivery_days'] is not None else 'N/A'}",
            f"DN {dn2}: {m2['delivery_days'] if m2['delivery_days'] is not None else 'N/A'}",
            "",
            "⚡ *SLA*",
            f"DN {dn1}: {m1['sla']}",
            f"DN {dn2}: {m2['sla']}",
            "",
            "━━━━━━━━━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def render_timeline(events: List[Dict[str, Any]]) -> str:
        """Render DN timeline"""
        if not events:
            return "📅 *Timeline*\n\nNo events found.\n\n0. Main Menu\n99. Back"
        
        lines = ["📅 *DN Timeline*", ""]
        
        emojis = {
            "created": "📝",
            "pgi": "🚚",
            "transit": "🚛",
            "arrival": "📍",
            "pod": "✅",
            "delivered": "🎯",
            "pending": "⏳",
            "delayed": "⚠️"
        }
        
        for i, event in enumerate(events, 1):
            status = event.get('status', '').lower()
            emoji = emojis.get(status, "•")
            timestamp = event.get('timestamp', 'N/A')
            description = event.get('description', '')
            lines.append(f"{emoji} *{timestamp}*")
            if description:
                lines.append(f"   {description}")
            lines.append("")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_transit_analysis(data: Dict[str, Any]) -> str:
        """Render transit analysis"""
        dn_no = data.get('dn_no', 'N/A')
        
        dn_create = data.get('dn_create_date')
        good_issue = data.get('good_issue_date')
        pod_date = data.get('pod_date')
        
        delivery_days = _calculate_days(dn_create, pod_date)
        transit_days = _calculate_days(good_issue, pod_date) if good_issue and pod_date else None
        pgi_days = _calculate_days(dn_create, good_issue) if dn_create and good_issue else None
        
        return "\n".join([
            f"🚚 *Transit Analysis - DN {dn_no}*",
            "",
            "📍 *Route*",
            f"Warehouse: {data.get('warehouse', 'N/A')}",
            f"Delivery: {data.get('delivery_location', data.get('ship_to_city', 'N/A'))}",
            f"City: {data.get('ship_to_city', 'N/A')}",
            "",
            "⏱️ *Timing*",
            f"Created: {_format_date(dn_create)}",
            f"PGI: {_format_date(good_issue)}",
            f"POD: {_format_date(pod_date)}",
            "",
            "📊 *Transit Metrics*",
            f"PGI Days: {pgi_days if pgi_days is not None else 'N/A'}",
            f"Transit Days: {transit_days if transit_days is not None else 'N/A'}",
            f"Total Delivery Days: {delivery_days if delivery_days is not None else 'N/A'}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_delay_analysis(data: Dict[str, Any]) -> str:
        """Render delay analysis"""
        dn_no = data.get('dn_no', 'N/A')
        dn_age = data.get('dn_age', 0)
        
        if dn_age <= DN_DELAY_THRESHOLD_DAYS:
            return f"✅ *DN {dn_no}*\n\nNo delay detected. DN is on track.\n\nAge: {dn_age} Days\n\n0. Main Menu\n99. Back"
        
        delay_days = dn_age - DN_DELAY_THRESHOLD_DAYS
        
        lines = [
            f"⚠️ *Delay Analysis - DN {dn_no}*",
            "",
            f"📊 Age: {dn_age} Days",
            f"⏰ Delay: {delay_days} Days",
            f"🎯 Threshold: {DN_DELAY_THRESHOLD_DAYS} Days",
            "",
            "📋 *Possible Causes:*",
            "• Warehouse processing delay",
            "• Transit delay",
            "• Customer availability",
            "• Documentation pending",
            "",
            "🎯 *Recommendations:*",
            "• Escalate to warehouse",
            "• Track shipment",
            "• Contact customer",
            "• Expedite processing",
            "",
            "0. Main Menu",
            "99. Back"
        ]
        
        return "\n".join(lines)
    
    @staticmethod
    def render_ai_insights(data: Dict[str, Any], ai_response: str) -> str:
        """Render AI insights"""
        dn_no = data.get('dn_no', 'N/A')
        
        return "\n".join([
            f"🤖 *AI Insights - DN {dn_no}*",
            "",
            ai_response,
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_trends(trend_data: Dict[str, Any]) -> str:
        """Render DN trends"""
        lines = ["📈 *DN Trends*", ""]
        
        weekly = trend_data.get('weekly', [])
        if weekly:
            lines.append("📅 *Weekly Summary*")
            for week in weekly[:4]:
                lines.append(f"Week {week.get('week', 'N/A')}:")
                lines.append(f"   DNs: {week.get('count', 0)}")
                lines.append(f"   Revenue: PKR {week.get('revenue', 0):,.2f}")
                lines.append("")
        
        growth = trend_data.get('growth', 0)
        lines.append(f"📈 *Growth Rate: {growth:+.1f}%*")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_forecast(forecast_data: Dict[str, Any]) -> str:
        """Render DN forecast"""
        return "\n".join([
            "🔮 *DN Forecast*",
            "",
            f"Expected DNs: {forecast_data.get('expected_count', 0):,}",
            f"Expected Revenue: PKR {forecast_data.get('expected_revenue', 0):,.2f}",
            f"Expected Units: {forecast_data.get('expected_units', 0):,}",
            "",
            "📊 *Confidence Interval*",
            f"Lower Bound: {forecast_data.get('lower_bound', 0):,}",
            f"Upper Bound: {forecast_data.get('upper_bound', 0):,}",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# DN DASHBOARD BUILDER - POSTGRESQL ONLY
# ============================================================

class DNDashboardBuilder:
    """Build complete DN dashboards from PostgreSQL"""
    
    def __init__(self, session: Session):
        self.session = session
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.RLock()
    
    def build(self, dn_no: str) -> Optional[Dict[str, Any]]:
        """Build complete dashboard for DN"""
        cache_key = dn_no.lower()
        
        with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key].copy()
        
        try:
            query = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_work,
                DeliveryReport.order_type,
                DeliveryReport.division,
                DeliveryReport.customer_code,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_name,
                DeliveryReport.dealer,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location,
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                func.sum(DeliveryReport.dn_qty).over().label("total_units"),
                func.sum(DeliveryReport.dn_amount).over().label("total_revenue"),
                func.avg(DeliveryReport.dn_amount).over().label("avg_price"),
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not query:
                return None
            
            today = datetime.now().date()
            dn_date = query.dn_create_date
            issue_date = query.good_issue_date
            pod_date = query.pod_date
            pending = bool(query.pending_flag) if query.pending_flag is not None else (not pod_date)
            
            # Calculate days
            delivery_days = _calculate_days(dn_date, pod_date)
            pod_days = _calculate_days(issue_date, pod_date) if issue_date and pod_date else None
            pgi_days = _calculate_days(dn_date, issue_date) if dn_date and issue_date else None
            transit_days = _calculate_days(issue_date, pod_date) if issue_date and pod_date else None
            dn_age = (today - dn_date).days if dn_date else None
            
            # SLA compliance
            sla_compliant = delivery_days is not None and delivery_days <= SLA_TARGET_DAYS
            
            # Determine status
            if pod_date:
                status = "Delivered"
            elif issue_date:
                if dn_age and dn_age > DN_DELAY_THRESHOLD_DAYS:
                    status = "Delayed"
                else:
                    status = "In Transit"
            else:
                status = "Pending PGI"
            
            dashboard = {
                'dn_no': _text(query.dn_no),
                'dn_work': _text(query.dn_work),
                'order_type': _text(query.order_type),
                'division': _text(query.division),
                'customer_code': _text(query.customer_code),
                'dealer_code': _text(query.dealer_code),
                'customer_name': _text(query.customer_name),
                'dealer_name': _text(query.dealer),
                'sales_office': _text(query.sales_office),
                'sales_manager': _text(query.sales_manager),
                'warehouse': _text(query.warehouse),
                'warehouse_code': _text(query.warehouse_code),
                'ship_to_city': _text(query.ship_to_city),
                'delivery_location': _text(query.delivery_location),
                'material_no': _text(query.material_no),
                'customer_model': _text(query.customer_model),
                'dn_qty': _safe_int(query.dn_qty),
                'dn_amount': _safe_float(query.dn_amount),
                'total_units': _safe_int(query.total_units),
                'total_revenue': _safe_float(query.total_revenue),
                'avg_price': _safe_float(query.avg_price),
                'dn_create_date': query.dn_create_date,
                'good_issue_date': query.good_issue_date,
                'pod_date': query.pod_date,
                'delivery_status': _text(query.delivery_status, status),
                'pgi_status': _text(query.pgi_status, 'Pending' if not issue_date else 'Completed'),
                'pod_status': _text(query.pod_status, 'Pending' if not pod_date else 'Completed'),
                'pending_flag': pending,
                'dn_age': dn_age,
                'delivery_days': delivery_days,
                'pod_days': pod_days,
                'pgi_days': pgi_days,
                'transit_days': transit_days,
                'sla_compliant': sla_compliant,
                'sla_target': SLA_TARGET_DAYS,
            }
            
            # Generate insights
            dashboard['insights'] = self._generate_insights(dashboard)
            dashboard['recommendations'] = self._generate_recommendations(dashboard)
            
            with self._cache_lock:
                self._cache[cache_key] = dashboard.copy()
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Failed to build dashboard for DN {dn_no}: {e}")
            return None
    
    def _generate_insights(self, data: Dict[str, Any]) -> List[str]:
        """Generate insights from data"""
        insights = []
        
        status = data.get('delivery_status', '')
        dn_age = data.get('dn_age', 0)
        revenue = data.get('total_revenue', 0)
        units = data.get('total_units', 0)
        sla = data.get('sla_compliant', False)
        warehouse = data.get('warehouse', '')
        
        if status == "Delivered":
            insights.append("✅ DN delivered successfully")
        elif status == "Pending PGI":
            insights.append("⏳ DN pending PGI - warehouse action needed")
        elif status == "Delayed":
            insights.append(f"⚠️ DN delayed by {dn_age - DN_DELAY_THRESHOLD_DAYS} days")
        elif status == "In Transit":
            insights.append("🚚 DN is in transit")
        
        if revenue > 1000000:
            insights.append(f"💰 High value DN: PKR {revenue:,.2f}")
        
        if units > 100:
            insights.append(f"📦 Large order: {units} units")
        
        if sla:
            insights.append(f"✅ SLA compliant: {SLA_TARGET_DAYS} days target met")
        else:
            insights.append(f"⚠️ SLA breach: {SLA_TARGET_DAYS} days target not met")
        
        if warehouse:
            insights.append(f"🏭 Processed by: {warehouse}")
        
        return insights[:5]
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate recommendations from data"""
        recommendations = []
        
        status = data.get('delivery_status', '')
        dn_age = data.get('dn_age', 0)
        sla = data.get('sla_compliant', False)
        
        if status == "Pending PGI":
            recommendations.append("🏭 Fast-track PGI processing at warehouse")
        elif status == "Delayed":
            recommendations.append("🚨 Escalate delayed DN for priority handling")
            recommendations.append("📞 Contact customer about delay")
        
        if not sla:
            recommendations.append("⏱️ Review delivery process for SLA compliance")
        
        if status == "In Transit" and dn_age and dn_age > 3:
            recommendations.append("🚚 Track and expedite in-transit delivery")
        
        if not recommendations:
            recommendations.append("✅ Maintain current performance")
        
        return recommendations[:3]

# ============================================================
# MAIN DN INTELLIGENCE ENGINE
# ============================================================

class DNAnalysisService:
    """
    COMPLETE DN INTELLIGENCE ENGINE
    TAKES FULL CONTROL AFTER PRESSING "1"
    ALL COMMUNICATION STAYS IN THIS FILE UNTIL "99"
    
    Uses AI libraries for intent recognition:
    - semantic-router: Intent classification
    - spaCy: Named Entity Recognition
    - RapidFuzz: Fuzzy matching
    - Groq/OpenAI: Explanations only (not data retrieval)
    
    Answers 300+ DN-related questions independently
    """
    
    _instance: Optional["DNAnalysisService"] = None
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
        self._service_name = "dn_analysis"
        self._version = "29.0"
        
        # Initialize components
        self._content_recognizer = DNContentRecognizer()
        self._menu_renderer = DNMenuRenderer()
        
        # Context memory - persists while in DN service
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        
        # Log status
        ai_status = []
        if GROQ_AVAILABLE:
            ai_status.append("Groq")
        if OPENAI_AVAILABLE:
            ai_status.append("OpenAI")
        if ANTHROPIC_AVAILABLE:
            ai_status.append("Anthropic")
        
        logger.info("=" * 70)
        logger.info("🚀 DN INTELLIGENCE ENGINE v29.0 initialized")
        logger.info("   📦 TAKES FULL CONTROL AFTER PRESSING '1'")
        logger.info("   🔒 ALL communication stays in this file")
        logger.info(f"   🧠 AI Providers: {', '.join(ai_status) if ai_status else 'Disabled'}")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🔍 Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
        logger.info(f"   🔍 spaCy NER: {'✅' if SPACY_AVAILABLE else '❌'}")
        logger.info(f"   🔍 RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
        logger.info("   🔑 ONLY '99' exits to main menu")
        logger.info("   📊 300+ DN questions answered")
        logger.info("=" * 70)
    
    @staticmethod
    def _get_session() -> Optional[Session]:
        """Get database session"""
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_context(self, session_id: str) -> DNContext:
        """Get or create context for session"""
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DNContext()
                self._contexts[session_id].in_dn_service = True
            context = self._contexts[session_id]
            context.last_activity = datetime.now()
            return context
    
    def _update_context_from_dashboard(self, context: DNContext, dashboard: Dict[str, Any]):
        """Update context from dashboard data"""
        context.update_from_dashboard(dashboard)
        
        # Add to history
        context.conversation_history.append({
            "timestamp": datetime.now().isoformat(),
            "dn": dashboard.get('dn_no'),
            "action": "dashboard_viewed"
        })
    
    def get_main_menu(self) -> str:
        """Get the main DN menu"""
        return self._menu_renderer.render_main_menu()
    
    # ============================================================
    # MAIN PROCESSING - ENTRY POINT
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        MAIN ENTRY POINT - Handles ALL DN queries
        TAKES FULL CONTROL - ALL communication stays here
        ONLY "99" exits back to main menu
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Engine (FULL CONTROL): '{message_clean}' from {sender}")
        
        # Get or create context - mark as in DN service
        context = self._get_context(sender)
        context.in_dn_service = True
        
        # ============================================================
        # STEP 1: Check for "99" - EXIT to main menu (ONLY EXIT!)
        # ============================================================
        if message_clean == "99":
            context.in_dn_service = False
            context.current_dn = None
            logger.info(f"🔄 User {sender} EXITING DN service (99)")
            return "99"  # Signal to router to exit
        
        # ============================================================
        # STEP 2: Check for "menu" or "0" - Show DN menu
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # ============================================================
        # STEP 3: Check for menu options (1-8)
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5", "6", "7", "8"]:
            return self._handle_menu_option(sender, message_clean)
        
        # ============================================================
        # STEP 4: Check for DN number (8-12 digits) - AUTO-DETECT
        # ============================================================
        dn = _extract_dn(message_clean)
        if dn and _is_valid_dn(dn):
            context.current_dn = dn
            return self._get_complete_dashboard(sender, dn, message_clean)
        
        # ============================================================
        # STEP 5: Check for comparison "compare DN1 DN2"
        # ============================================================
        if "compare" in message_clean.lower():
            dns = re.findall(r'\b(\d{8,12})\b', message_clean)
            if len(dns) >= 2:
                return self._handle_comparison(sender, dns[0], dns[1])
            return self._handle_comparison_help()
        
        # ============================================================
        # STEP 6: Check for "pending"
        # ============================================================
        if message_clean.lower() in ["pending", "pending dn", "pending dns"]:
            return self._get_pending_dns(sender)
        
        # ============================================================
        # STEP 7: Check for "search"
        # ============================================================
        if "search" in message_clean.lower():
            query = message_clean.replace("search", "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search. Example: 'search Lahore'"
        
        # ============================================================
        # STEP 8: AI Content Recognition (with database session for entities)
        # ============================================================
        session = self._get_session()
        recognized = self._content_recognizer.recognize(message_clean, session)
        if session:
            session.close()
        
        logger.info(f"🎯 Recognized: {recognized['intent']} (confidence: {recognized['confidence']:.2f})")
        logger.info(f"   Entities: {recognized['entities']}")
        
        # Check if DN number was recognized
        if recognized.get("dn"):
            context.current_dn = recognized["dn"]
            return self._get_complete_dashboard(sender, recognized["dn"], message_clean)
        
        # ============================================================
        # STEP 9: FOLLOW-UP QUERIES - Uses current DN (SMART MEMORY)
        # ============================================================
        if context.current_dn and recognized.get("follow_up", False):
            return self._handle_follow_up(sender, message_clean, recognized, context)
        
        # ============================================================
        # STEP 10: Route based on recognized intent
        # ============================================================
        intent = recognized.get("intent", "unknown")
        
        # If intent is unknown but we have a current DN, try follow-up
        if intent == "unknown" and context.current_dn:
            return self._handle_follow_up(sender, message_clean, recognized, context)
        
        # Route to specific handlers
        if intent in ["status", "revenue", "units", "customer", "dealer", "warehouse", "city"]:
            return self._handle_metric_query(sender, message_clean, intent, context)
        
        if intent in ["sales_office", "sales_manager"]:
            return self._handle_sales_query(sender, message_clean, intent, context)
        
        if intent in ["timeline", "history"]:
            return self._handle_timeline(sender, message_clean, context)
        
        if intent == "transit":
            return self._handle_transit(sender, message_clean, context)
        
        if intent == "sla":
            return self._handle_sla(sender, message_clean, context)
        
        if intent == "delay":
            return self._handle_delay(sender, message_clean, context)
        
        if intent == "pending":
            return self._get_pending_dns(sender)
        
        if intent == "search":
            query = message_clean.replace("search", "").replace("find", "").replace("lookup", "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search. Example: 'search Lahore'"
        
        if intent == "compare":
            return self._handle_comparison_help()
        
        if intent in ["trend", "trends"]:
            return self._get_trends(sender)
        
        if intent == "forecast":
            return self._get_forecast(sender)
        
        if intent == "insights":
            return self._get_insights(sender)
        
        if intent == "recommendations":
            return self._get_recommendations(sender)
        
        if intent in ["explain", "root_cause", "executive_summary"]:
            if context.current_dn:
                return self._get_ai_explanation(sender, context.current_dn, intent, message_clean, context)
            return self._show_help_with_dn_prompt()
        
        if intent in ["division", "order_type", "material", "model", "pgi", "pod", "delivery_days"]:
            if context.current_dn:
                return self._handle_specific_query(sender, message_clean, intent, context)
            return self._show_help_with_dn_prompt()
        
        # ============================================================
        # STEP 11: Unknown - Show help (STAYS IN DN SERVICE)
        # ============================================================
        return self._show_help_with_dn_prompt()
    
    # ============================================================
    # FOLLOW-UP HANDLING - SMART MEMORY
    # ============================================================
    
    def _handle_follow_up(self, sender: str, query: str, recognized: Dict[str, Any], context: DNContext) -> str:
        """Handle follow-up queries using current DN"""
        if not context.current_dn:
            return self._show_help_with_dn_prompt()
        
        intent = recognized.get("intent", "unknown")
        query_lower = query.lower()
        
        # Map keywords to actions
        if "status" in query_lower or "track" in query_lower or "where" in query_lower:
            return self._get_dn_status(sender, context.current_dn)
        elif "revenue" in query_lower or "amount" in query_lower or "value" in query_lower:
            return self._get_dn_metric(sender, context.current_dn, "revenue")
        elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower:
            return self._get_dn_metric(sender, context.current_dn, "units")
        elif "customer" in query_lower or "who" in query_lower:
            return self._get_dn_customer(sender, context.current_dn)
        elif "dealer" in query_lower:
            return self._get_dn_dealer(sender, context.current_dn)
        elif "warehouse" in query_lower or "wh" in query_lower:
            return self._get_dn_warehouse(sender, context.current_dn)
        elif "city" in query_lower:
            return self._get_dn_city(sender, context.current_dn)
        elif "sales" in query_lower or "office" in query_lower:
            return self._get_dn_sales_office(sender, context.current_dn)
        elif "manager" in query_lower:
            return self._get_dn_sales_manager(sender, context.current_dn)
        elif "timeline" in query_lower:
            return self._get_dn_timeline(sender, context.current_dn)
        elif "history" in query_lower:
            return self._get_dn_history(sender, context.current_dn)
        elif "transit" in query_lower:
            return self._get_dn_transit(sender, context.current_dn)
        elif "sla" in query_lower:
            return self._get_dn_sla(sender, context.current_dn)
        elif "delay" in query_lower or "delayed" in query_lower:
            return self._get_dn_delay(sender, context.current_dn)
        elif "pgi" in query_lower:
            return self._get_dn_pgi(sender, context.current_dn)
        elif "pod" in query_lower:
            return self._get_dn_pod(sender, context.current_dn)
        elif "division" in query_lower:
            return self._get_dn_division(sender, context.current_dn)
        elif "order" in query_lower:
            return self._get_dn_order_type(sender, context.current_dn)
        elif "material" in query_lower:
            return self._get_dn_material(sender, context.current_dn)
        elif "model" in query_lower:
            return self._get_dn_model(sender, context.current_dn)
        elif "explain" in query_lower or "tell me" in query_lower:
            return self._get_ai_explanation(sender, context.current_dn, "explain", query, context)
        
        # If we have a current DN, show its dashboard
        return self._get_complete_dashboard(sender, context.current_dn, query)
    
    # ============================================================
    # MENU OPTIONS
    # ============================================================
    
    def _handle_menu_option(self, sender: str, option: str) -> str:
        """Handle menu options 1-8"""
        options = {
            "1": "🔍 *Enter DN number:*\n\nType an 8-12 digit DN number for complete dashboard.\n\n0. Main Menu\n99. Back",
            "2": "📊 *Enter DN number for status:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back",
            "3": self._get_pending_dns(sender),
            "4": "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\nExamples:\n• search Lahore\n• search 6243700919\n• search LALA KHAN\n\n0. Main Menu\n99. Back",
            "5": "🔄 *Compare DNs:*\n\nType 'compare [DN1] [DN2]'\n\nExample: compare 6243700919 6243714234\n\n0. Main Menu\n99. Back",
            "6": self._get_ai_insights_prompt(sender),
            "7": self._get_trends(sender),
            "8": self._get_forecast(sender),
        }
        return options.get(option, self.get_main_menu())
    
    def _get_ai_insights_prompt(self, sender: str) -> str:
        """Get AI insights prompt"""
        context = self._get_context(sender)
        if context.current_dn:
            return self._get_ai_explanation(sender, context.current_dn, "insights", "insights", context)
        return "🤖 *AI Insights*\n\nPlease enter a DN number first.\n\n0. Main Menu\n99. Back"
    
    def _show_help_with_dn_prompt(self) -> str:
        """Show help with DN prompt"""
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *DN Service Commands (Stay in DN):*",
            "",
            "📊 *DN Queries:*",
            "• Type DN number for full dashboard",
            "• status [DN] - DN status",
            "• revenue [DN] - Revenue",
            "• units [DN] - Units",
            "• customer [DN] - Customer details",
            "• dealer [DN] - Dealer details",
            "• warehouse [DN] - Warehouse",
            "• city [DN] - City",
            "",
            "📅 *Timeline:*",
            "• timeline [DN] - Timeline",
            "• history [DN] - History",
            "• transit [DN] - Transit analysis",
            "",
            "📋 *Status:*",
            "• pgi [DN] - PGI status",
            "• pod [DN] - POD status",
            "• sla [DN] - SLA compliance",
            "• delay [DN] - Delay analysis",
            "",
            "🤖 *AI:*",
            "• explain [DN] - AI explanation",
            "• insights [DN] - AI insights",
            "• recommendations - Improvement ideas",
            "",
            "🔍 *General:*",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "• compare DN1 DN2 - Compare DNs",
            "• trend - DN trends",
            "• forecast - DN forecast",
            "",
            "📌 *Follow-up:*",
            "• After viewing a DN, just type 'status', 'revenue', etc.",
            "• No need to type the DN again!",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    # ============================================================
    # COMPLETE DASHBOARD
    # ============================================================
    
    def _get_complete_dashboard(self, sender: str, dn_no: str, query: str = "") -> str:
        """Get complete DN dashboard"""
        session = self._get_session()
        if not session:
            return self._get_fallback_dashboard(dn_no)
        
        try:
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            session.close()
            
            if not dashboard:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            # Update context with dashboard data
            context = self._get_context(sender)
            self._update_context_from_dashboard(context, dashboard)
            
            return self._menu_renderer.render_enterprise_dashboard(dashboard, context)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return self._get_fallback_dashboard(dn_no)
    
    def _get_fallback_dashboard(self, dn_no: str) -> str:
        """Fallback when database is unavailable"""
        return "\n".join([
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "⚠️ Database is currently unavailable.",
            "",
            "💡 *Try:*",
            "• Check database connection",
            "• Try again later",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    # ============================================================
    # METRIC QUERIES
    # ============================================================
    
    def _handle_metric_query(self, sender: str, query: str, intent: str, context: DNContext) -> str:
        """Handle metric queries"""
        # Extract DN from query or use current DN
        dn = _extract_dn(query) or context.current_dn
        
        if not dn:
            return self._show_help_with_dn_prompt()
        
        if intent == "status":
            return self._get_dn_status(sender, dn)
        elif intent == "revenue":
            return self._get_dn_metric(sender, dn, "revenue")
        elif intent == "units":
            return self._get_dn_metric(sender, dn, "units")
        elif intent == "customer":
            return self._get_dn_customer(sender, dn)
        elif intent == "dealer":
            return self._get_dn_dealer(sender, dn)
        elif intent == "warehouse":
            return self._get_dn_warehouse(sender, dn)
        elif intent == "city":
            return self._get_dn_city(sender, dn)
        
        return self._get_complete_dashboard(sender, dn, query)
    
    def _get_dn_status(self, sender: str, dn_no: str) -> str:
        """Get DN status"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                DeliveryReport.dn_create_date,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            data = {
                'dn_no': _text(result.dn_no),
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'pending_flag': bool(result.pending_flag) if result.pending_flag is not None else True,
                'dn_create_date': result.dn_create_date,
                'customer_name': _text(result.customer_name),
                'customer_code': _text(result.customer_code),
            }
            
            return self._menu_renderer.render_dn_status(data)
            
        except Exception as e:
            logger.error(f"Status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_metric(self, sender: str, dn_no: str, metric: str) -> str:
        """Get specific DN metric"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            if metric == "revenue":
                return f"💰 *DN {dn_no} Revenue*\n\nPKR {_safe_float(result.dn_amount):,.2f}\n\n0. Main Menu\n99. Back"
            else:
                return f"📦 *DN {dn_no} Units*\n\n{_safe_int(result.dn_qty):,}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Metric error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching {metric} for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_customer(self, sender: str, dn_no: str) -> str:
        """Get DN customer details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"👤 *Customer - DN {dn_no}*",
                "",
                f"Name: {_text(result.customer_name)}",
                f"Code: {_text(result.customer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Customer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching customer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_dealer(self, sender: str, dn_no: str) -> str:
        """Get DN dealer details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dealer,
                DeliveryReport.dealer_code,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏪 *Dealer - DN {dn_no}*",
                "",
                f"Name: {_text(result.dealer)}",
                f"Code: {_text(result.dealer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_warehouse(self, sender: str, dn_no: str) -> str:
        """Get DN warehouse details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏭 *Warehouse - DN {dn_no}*",
                "",
                f"Name: {_text(result.warehouse)}",
                f"Code: {_text(result.warehouse_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Warehouse error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching warehouse for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_city(self, sender: str, dn_no: str) -> str:
        """Get DN city details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.ship_to_city,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📍 *City - DN {dn_no}*",
                "",
                f"City: {_text(result.ship_to_city)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"City error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching city for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_sales_office(self, sender: str, dn_no: str) -> str:
        """Get DN sales office"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.sales_office,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏢 *Sales Office - DN {dn_no}*",
                "",
                f"Office: {_text(result.sales_office)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Sales office error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching sales office for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_sales_manager(self, sender: str, dn_no: str) -> str:
        """Get DN sales manager"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.sales_manager,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"👔 *Sales Manager - DN {dn_no}*",
                "",
                f"Manager: {_text(result.sales_manager)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Sales manager error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching sales manager for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_division(self, sender: str, dn_no: str) -> str:
        """Get DN division"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.division,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📊 *Division - DN {dn_no}*",
                "",
                f"Division: {_text(result.division)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Division error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching division for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_order_type(self, sender: str, dn_no: str) -> str:
        """Get DN order type"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.order_type,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📋 *Order Type - DN {dn_no}*",
                "",
                f"Type: {_text(result.order_type)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Order type error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching order type for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_material(self, sender: str, dn_no: str) -> str:
        """Get DN material number"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.material_no,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📦 *Material - DN {dn_no}*",
                "",
                f"Material: {_text(result.material_no)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Material error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching material for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_model(self, sender: str, dn_no: str) -> str:
        """Get DN customer model"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.customer_model,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📱 *Model - DN {dn_no}*",
                "",
                f"Model: {_text(result.customer_model)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Model error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching model for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_pgi(self, sender: str, dn_no: str) -> str:
        """Get DN PGI details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.pgi_status,
                DeliveryReport.good_issue_date,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🚚 *PGI - DN {dn_no}*",
                "",
                f"Status: {_text(result.pgi_status, 'Pending')}",
                f"Date: {_format_date(result.good_issue_date)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"PGI error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching PGI for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_pod(self, sender: str, dn_no: str) -> str:
        """Get DN POD details"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.pod_status,
                DeliveryReport.pod_date,
                DeliveryReport.dn_no,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📋 *POD - DN {dn_no}*",
                "",
                f"Status: {_text(result.pod_status, 'Pending')}",
                f"Date: {_format_date(result.pod_date)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"POD error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching POD for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_sla(self, sender: str, dn_no: str) -> str:
        """Get DN SLA compliance"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                DeliveryReport.pod_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            delivery_days = _calculate_days(result.dn_create_date, result.pod_date)
            sla_compliant = delivery_days is not None and delivery_days <= SLA_TARGET_DAYS
            
            return "\n".join([
                f"⚡ *SLA - DN {dn_no}*",
                "",
                f"Target: {SLA_TARGET_DAYS} Days",
                f"Actual: {delivery_days if delivery_days is not None else 'N/A'} Days",
                f"Compliant: {'✅ Yes' if sla_compliant else '❌ No'}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"SLA error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching SLA for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_delay(self, sender: str, dn_no: str) -> str:
        """Get DN delay analysis"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            session.close()
            
            if not dashboard:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return self._menu_renderer.render_delay_analysis(dashboard)
            
        except Exception as e:
            logger.error(f"Delay error: {e}")
            if session:
                session.close()
            return f"⚠️ Error analyzing delay for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # TIMELINE, HISTORY, TRANSIT
    # ============================================================
    
    def _get_dn_timeline(self, sender: str, dn_no: str) -> str:
        """Get DN timeline"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            session.close()
            
            if not dashboard:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            events = []
            if dashboard.get('dn_create_date'):
                events.append({
                    'timestamp': _format_date(dashboard.get('dn_create_date')),
                    'status': 'created',
                    'description': f"DN {dn_no} created"
                })
            if dashboard.get('good_issue_date'):
                events.append({
                    'timestamp': _format_date(dashboard.get('good_issue_date')),
                    'status': 'pgi',
                    'description': "Goods issued from warehouse"
                })
            if dashboard.get('pod_date'):
                events.append({
                    'timestamp': _format_date(dashboard.get('pod_date')),
                    'status': 'delivered',
                    'description': "Delivery completed - POD received"
                })
            
            return self._menu_renderer.render_timeline(events)
            
        except Exception as e:
            logger.error(f"Timeline error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching timeline for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_history(self, sender: str, dn_no: str) -> str:
        """Get DN history (alias for timeline)"""
        return self._get_dn_timeline(sender, dn_no)
    
    def _get_dn_transit(self, sender: str, dn_no: str) -> str:
        """Get DN transit analysis"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            session.close()
            
            if not dashboard:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return self._menu_renderer.render_transit_analysis(dashboard)
            
        except Exception as e:
            logger.error(f"Transit error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching transit for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # SALES QUERIES
    # ============================================================
    
    def _handle_sales_query(self, sender: str, query: str, intent: str, context: DNContext) -> str:
        """Handle sales office queries"""
        dn = _extract_dn(query) or context.current_dn
        
        if not dn:
            return self._show_help_with_dn_prompt()
        
        if intent == "sales_manager":
            return self._get_dn_sales_manager(sender, dn)
        else:
            return self._get_dn_sales_office(sender, dn)
    
    # ============================================================
    # SPECIFIC QUERIES
    # ============================================================
    
    def _handle_specific_query(self, sender: str, query: str, intent: str, context: DNContext) -> str:
        """Handle specific queries like division, order type, etc."""
        dn = _extract_dn(query) or context.current_dn
        
        if not dn:
            return self._show_help_with_dn_prompt()
        
        handlers = {
            "division": self._get_dn_division,
            "order_type": self._get_dn_order_type,
            "material": self._get_dn_material,
            "model": self._get_dn_model,
            "pgi": self._get_dn_pgi,
            "pod": self._get_dn_pod,
            "delivery_days": self._get_dn_delivery_days,
        }
        
        handler = handlers.get(intent)
        if handler:
            return handler(sender, dn)
        
        return self._show_help_with_dn_prompt()
    
    def _get_dn_delivery_days(self, sender: str, dn_no: str) -> str:
        """Get DN delivery days"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_create_date,
                DeliveryReport.pod_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            delivery_days = _calculate_days(result.dn_create_date, result.pod_date)
            
            return "\n".join([
                f"⏱️ *Delivery Days - DN {dn_no}*",
                "",
                f"Created: {_format_date(result.dn_create_date)}",
                f"POD: {_format_date(result.pod_date) if result.pod_date else 'Pending'}",
                f"Total Days: {delivery_days if delivery_days is not None else 'N/A'}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Delivery days error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching delivery days for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # AI EXPLANATIONS
    # ============================================================
    
    def _get_ai_explanation(self, sender: str, dn_no: str, intent: str, query: str, context: DNContext) -> str:
        """Get AI explanation for DN"""
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            dashboard = builder.build(dn_no)
            session.close()
            
            if not dashboard:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            # Store intent for AI context
            context.last_intent = intent
            
            # Generate AI explanation
            ai_response = self._content_recognizer.generate_ai_response(query, dashboard, context)
            
            if ai_response:
                return self._menu_renderer.render_ai_insights(dashboard, ai_response)
            
            # Fallback if AI fails
            return self._get_complete_dashboard(sender, dn_no, query)
            
        except Exception as e:
            logger.error(f"AI explanation error: {e}")
            if session:
                session.close()
            return f"⚠️ Error generating AI explanation for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # COMPARISON
    # ============================================================
    
    def _handle_comparison(self, sender: str, dn1: str, dn2: str) -> str:
        """Handle DN comparison"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data1 = builder.build(dn1)
            data2 = builder.build(dn2)
            session.close()
            
            if not data1 or not data2:
                return "⚠️ One or both DNs not found.\n\n0. Main Menu\n99. Back"
            
            context = self._get_context(sender)
            context.comparison_dns = [dn1, dn2]
            context.last_comparison = {"dn1": data1, "dn2": data2}
            
            return self._menu_renderer.render_comparison(data1, data2)
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing DNs.\n\n0. Main Menu\n99. Back"
    
    def _handle_comparison_help(self) -> str:
        """Show comparison help"""
        return "\n".join([
            "🔄 *Compare DNs*",
            "",
            "Please provide two DN numbers to compare.",
            "",
            "Example: compare 6243700919 6243714234",
            "",
            "You can also type: 6243700919 vs 6243714234",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    # ============================================================
    # PENDING DNS
    # ============================================================
    
    def _get_pending_dns(self, sender: str) -> str:
        """Get pending DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            today = date.today()
            
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(30).all()
            
            items = []
            for row in results:
                pending_days = (today - row.dn_create_date).days if row.dn_create_date else 0
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'pending_days': pending_days,
                })
            
            session.close()
            return self._menu_renderer.render_pending_list(items)
            
        except Exception as e:
            logger.error(f"Pending error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending DNs.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # SEARCH
    # ============================================================
    
    def _search_dns(self, sender: str, query: str) -> str:
        """Search DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_status,
                DeliveryReport.warehouse,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.ship_to_city.ilike(search_pattern),
                    DeliveryReport.warehouse.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                    DeliveryReport.dealer.ilike(search_pattern),
                    DeliveryReport.sales_office.ilike(search_pattern),
                    DeliveryReport.sales_manager.ilike(search_pattern),
                    DeliveryReport.material_no.ilike(search_pattern),
                    DeliveryReport.customer_model.ilike(search_pattern),
                    DeliveryReport.delivery_location.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(30).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name),
                    'customer_code': _text(row.customer_code),
                    'ship_to_city': _text(row.ship_to_city),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'warehouse': _text(row.warehouse),
                    'division': _text(row.division),
                })
            
            session.close()
            
            # Update context with search results
            context = self._get_context(sender)
            context.search_results = items
            
            return self._menu_renderer.render_search_results(query, items)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # TRENDS, FORECAST, INSIGHTS, RECOMMENDATIONS
    # ============================================================
    
    def _get_trends(self, sender: str) -> str:
        """Get DN trends"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            weekly = session.query(
                extract('week', DeliveryReport.dn_create_date).label('week'),
                func.count(DeliveryReport.dn_no).label('count'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                extract('week', DeliveryReport.dn_create_date)
            ).order_by(
                desc(extract('week', DeliveryReport.dn_create_date))
            ).limit(4).all()
            
            trend_data = {
                'weekly': [{
                    'week': int(row.week),
                    'count': _safe_int(row.count),
                    'revenue': _safe_float(row.revenue)
                } for row in weekly],
                'growth': 0
            }
            
            session.close()
            return self._menu_renderer.render_trends(trend_data)
            
        except Exception as e:
            logger.error(f"Trend error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching trends.\n\n0. Main Menu\n99. Back"
    
    def _get_forecast(self, sender: str) -> str:
        """Get DN forecast"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from datetime import timedelta
            
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.count(func.distinct(func.date(DeliveryReport.dn_create_date))).label('days'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
            ).filter(
                DeliveryReport.dn_create_date >= datetime.now().date() - timedelta(days=30)
            ).first()
            
            session.close()
            
            if not results or not results.days:
                return "🔮 Insufficient data for forecast.\n\n0. Main Menu\n99. Back"
            
            total = _safe_int(results.total)
            days = _safe_int(results.days)
            revenue = _safe_float(results.revenue)
            units = _safe_int(results.units)
            
            avg_daily = total / days if days > 0 else 0
            avg_daily_revenue = revenue / days if days > 0 else 0
            avg_daily_units = units / days if days > 0 else 0
            
            forecast_data = {
                'expected_count': int(avg_daily * 7),
                'expected_revenue': avg_daily_revenue * 7,
                'expected_units': int(avg_daily_units * 7),
                'lower_bound': int(avg_daily * 7 * 0.8),
                'upper_bound': int(avg_daily * 7 * 1.2),
            }
            
            return self._menu_renderer.render_forecast(forecast_data)
            
        except Exception as e:
            logger.error(f"Forecast error: {e}")
            if session:
                session.close()
            return "⚠️ Error generating forecast.\n\n0. Main Menu\n99. Back"
    
    def _get_insights(self, sender: str) -> str:
        """Get DN insights"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('delivered'),
                func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.avg(DeliveryReport.dn_qty).label('avg_units'),
                func.sum(DeliveryReport.dn_qty).label('total_units'),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.dn_age > DN_DELAY_THRESHOLD_DAYS).label('delayed'),
            ).first()
            
            session.close()
            
            total = _safe_int(results.total)
            delivered = _safe_int(results.delivered)
            pending = _safe_int(results.pending)
            delayed = _safe_int(results.delayed) if hasattr(results, 'delayed') else 0
            avg_revenue = _safe_float(results.avg_revenue)
            total_revenue = _safe_float(results.total_revenue)
            avg_units = _safe_float(results.avg_units)
            total_units = _safe_int(results.total_units)
            
            delivery_rate = (delivered / total * 100) if total > 0 else 0
            pending_rate = (pending / total * 100) if total > 0 else 0
            delayed_rate = (delayed / total * 100) if total > 0 else 0
            
            return "\n".join([
                "💡 *DN Insights*",
                "",
                f"📊 Total DNs: {total:,}",
                f"✅ Delivered: {delivered:,} ({delivery_rate:.1f}%)",
                f"⏳ Pending: {pending:,} ({pending_rate:.1f}%)",
                f"⚠️ Delayed: {delayed:,} ({delayed_rate:.1f}%)",
                "",
                f"💰 Total Revenue: PKR {total_revenue:,.2f}",
                f"📈 Avg Revenue/DN: PKR {avg_revenue:,.2f}",
                "",
                f"📦 Total Units: {total_units:,}",
                f"📊 Avg Units/DN: {avg_units:.1f}",
                "",
                "🎯 *Key Findings:*",
                f"• Delivery rate is {delivery_rate:.1f}%",
                f"• {pending} DNs need attention",
                f"• {delayed} DNs are delayed",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Insights error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching insights.\n\n0. Main Menu\n99. Back"
    
    def _get_recommendations(self, sender: str) -> str:
        """Get DN recommendations"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from datetime import timedelta
            
            pending_count = session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).scalar() or 0
            
            threshold = datetime.now().date() - timedelta(days=DN_DELAY_THRESHOLD_DAYS)
            
            delayed_count = session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.good_issue_date < threshold,
                DeliveryReport.pod_date.is_(None)
            ).scalar() or 0
            
            session.close()
            
            recommendations = []
            
            if pending_count > 20:
                recommendations.append(f"🚨 {pending_count} pending DNs need resolution")
            elif pending_count > 10:
                recommendations.append(f"📋 Review {pending_count} pending DNs")
            
            if delayed_count > 10:
                recommendations.append(f"⏰ {delayed_count} DNs are delayed > {DN_DELAY_THRESHOLD_DAYS} days")
            
            if pending_count <= 5 and delayed_count <= 5:
                recommendations.append("✅ Current DN performance is good")
                recommendations.append("📊 Continue monitoring key metrics")
                recommendations.append("🔄 Review SLA compliance regularly")
            
            if not recommendations:
                recommendations.append("✅ Maintain current performance")
            
            lines = ["🎯 *DN Recommendations*", ""]
            for rec in recommendations:
                lines.append(f"• {rec}")
            
            lines.extend(["", "0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Recommendations error: {e}")
            if session:
                session.close()
            return "⚠️ Error generating recommendations.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for service"""
        ai_status = []
        if GROQ_AVAILABLE:
            ai_status.append("Groq")
        if OPENAI_AVAILABLE:
            ai_status.append("OpenAI")
        if ANTHROPIC_AVAILABLE:
            ai_status.append("Anthropic")
        if SEMANTIC_ROUTER_AVAILABLE:
            ai_status.append("SemanticRouter")
        if SPACY_AVAILABLE:
            ai_status.append("spaCy")
        if RAPIDFUZZ_AVAILABLE:
            ai_status.append("RapidFuzz")
        
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "ai": "enabled" if (GROQ_AVAILABLE or OPENAI_AVAILABLE or ANTHROPIC_AVAILABLE) else "disabled",
            "ai_providers": ai_status,
            "takes_full_control": True,
            "exit_command": "99",
            "questions_supported": "300+",
            "libraries": {
                "semantic_router": SEMANTIC_ROUTER_AVAILABLE,
                "spacy": SPACY_AVAILABLE,
                "rapidfuzz": RAPIDFUZZ_AVAILABLE,
                "flashrank": FLASHRANK_AVAILABLE,
                "nltk": NLTK_AVAILABLE,
                "textblob": TEXTBLOB_AVAILABLE,
                "tiktoken": TIKTOKEN_AVAILABLE,
            },
            "timestamp": datetime.now().isoformat()
        }

# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DNAnalysisService] = None
_service_lock = threading.Lock()

def get_dn_analysis_service() -> DNAnalysisService:
    """Get singleton instance"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalysisService()
    return _service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    """Process DN menu input for WhatsApp integration"""
    service = get_dn_analysis_service()
    result = service.process_whatsapp_query(user_input, session_id)
    
    # Check if we need to exit to main menu
    if result == "99":
        return {
            "response": "99",
            "menu_type": "dn_menu",
            "action": "exit_to_main",
            "data": {},
            "exit_menu": True
        }
    
    return {
        "response": result,
        "menu_type": "dn_menu",
        "action": "dn_response",
        "data": {},
        "exit_menu": False
    }

def get_dn_main_menu() -> str:
    """Get the main DN menu for WhatsApp"""
    service = get_dn_analysis_service()
    return service.get_main_menu()

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DNAnalysisService",
    "DNContext",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
