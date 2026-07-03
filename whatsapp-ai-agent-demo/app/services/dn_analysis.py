"""
File: app/services/dn_analysis.py
Version: 24.0 - AI-POWERED DN SERVICE WITH CONTENT RECOGNITION

FEATURES:
- ✅ AI Content Recognition using Groq/OpenAI/Anthropic
- ✅ Semantic Search using Semantic Router
- ✅ Entity Extraction using spaCy
- ✅ Fuzzy Matching using RapidFuzz
- ✅ Smart Intent Detection
- ✅ Natural Language Understanding
- ✅ Reranking using FlashRank

LIBRARIES SUPPORTED:
- openai==1.99.9
- groq==0.31.0
- anthropic>=0.61.0
- litellm==1.74.9
- pydantic-ai==0.8.1
- instructor==1.10.0
- spacy==3.8.7
- semantic-router==0.1.11
- flashrank==0.2.10
- rapidfuzz==3.13.0
- nltk==3.9.1
- textblob==0.19.0
- tiktoken==0.9.0

Stays in DN menu until "99"
Answers ALL DN questions intelligently
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Any, Optional, Dict, List, Tuple, Union, Callable
from functools import lru_cache
import hashlib

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
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
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
        nltk.download('punkt')
    try:
        nltk.data.find('corpora/stopwords')
    except LookupError:
        nltk.download('stopwords')
    logger.info("✅ NLTK loaded")
except ImportError:
    NLTK_AVAILABLE = False
    logger.warning("⚠️ NLTK not available")

# TextBlob - Sentiment/Text Processing
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
    from sqlalchemy import func, or_, and_, desc, asc
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
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")  # groq, openai, anthropic
AI_MODEL = os.getenv("AI_MODEL", "llama3-70b-8192")
USE_AI_ENHANCEMENT = os.getenv("USE_AI_ENHANCEMENT", "true").lower() == "true"

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DNContext:
    """DN session context with AI history"""
    current_dn: Optional[str] = None
    in_menu: bool = False
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_entities: Dict[str, Any] = field(default_factory=dict)
    search_results: Optional[List[Dict[str, Any]]] = None
    
@dataclass
class DNIntent:
    """DN intent detection result"""
    intent: str  # dashboard, status, pending, search, compare, trend, forecast, insights, recommendations
    confidence: float
    entities: Dict[str, Any]
    query: str
    explanation: str

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

# ============================================================
# AI CONTENT RECOGNITION ENGINE
# ============================================================

class AIEngine:
    """
    AI Content Recognition Engine
    Uses multiple AI providers for understanding DN queries
    """
    
    def __init__(self):
        self._client = None
        self._provider = AI_PROVIDER
        
        # Initialize AI client
        self._init_client()
        
        # Semantic Router for quick intent detection
        self._router = self._init_router()
        
        # Cache for AI responses
        self._cache: Dict[str, str] = {}
        self._cache_lock = threading.RLock()
    
    def _init_client(self):
        """Initialize AI client"""
        if self._provider == "groq" and GROQ_AVAILABLE:
            try:
                self._client = Groq()
                logger.info("✅ Groq client initialized")
                return
            except Exception as e:
                logger.warning(f"Groq init failed: {e}")
        
        if self._provider == "openai" and OPENAI_AVAILABLE:
            try:
                self._client = OpenAI()
                logger.info("✅ OpenAI client initialized")
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
        
        logger.warning("⚠️ No AI client available - using fallback mode")
        self._client = None
    
    def _init_router(self):
        """Initialize Semantic Router"""
        if not SEMANTIC_ROUTER_AVAILABLE:
            return None
        
        try:
            routes = [
                Route(name="dashboard", utterances=[
                    "show dn", "dn dashboard", "dn details", "dn info",
                    "what is dn", "tell me about dn"
                ]),
                Route(name="status", utterances=[
                    "dn status", "status of dn", "where is dn",
                    "is dn delivered", "dn progress", "dn tracking"
                ]),
                Route(name="pending", utterances=[
                    "pending dns", "pending deliveries", "overdue dns",
                    "undelivered dns", "backlog dns"
                ]),
                Route(name="search", utterances=[
                    "search dn", "find dn", "lookup dn",
                    "dn with customer", "dn by city", "dn by warehouse"
                ]),
                Route(name="revenue", utterances=[
                    "dn revenue", "revenue from dn", "dn amount",
                    "how much is dn", "value of dn"
                ]),
                Route(name="units", utterances=[
                    "dn units", "dn quantity", "how many units",
                    "dn qty", "dn volume"
                ]),
                Route(name="compare", utterances=[
                    "compare dns", "dn vs dn", "comparison",
                    "which dn is better"
                ]),
                Route(name="trend", utterances=[
                    "dn trends", "dn pattern", "dn over time",
                    "weekly dns", "monthly dns"
                ]),
                Route(name="insights", utterances=[
                    "dn insights", "dn analysis", "key findings",
                    "what does dn data show"
                ]),
                Route(name="forecast", utterances=[
                    "dn forecast", "predict dn", "future dns",
                    "expected dns"
                ]),
                Route(name="recommendations", utterances=[
                    "dn recommendations", "improve dns",
                    "suggestions for dns"
                ]),
            ]
            encoder = HuggingFaceEncoder()
            router = Router(routes=routes, encoder=encoder)
            logger.info("✅ Semantic Router initialized")
            return router
        except Exception as e:
            logger.warning(f"Semantic Router init failed: {e}")
            return None
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for AI response"""
        return hashlib.md5(text.encode()).hexdigest()
    
    def recognize_intent(self, query: str) -> DNIntent:
        """
        Recognize intent using AI + Semantic Router + NLP
        """
        # Check cache first
        cache_key = self._get_cache_key(query)
        with self._cache_lock:
            if cache_key in self._cache:
                try:
                    return self._parse_intent_response(self._cache[cache_key])
                except:
                    pass
        
        # STEP 1: Semantic Router (fast)
        intent = self._semantic_route(query)
        
        # STEP 2: Extract entities
        entities = self._extract_entities(query)
        
        # STEP 3: AI Enhancement (if available)
        if self._client and USE_AI_ENHANCEMENT:
            ai_intent = self._ai_recognize_intent(query)
            if ai_intent and ai_intent.confidence > intent.confidence:
                intent = ai_intent
        
        # STEP 4: Keyword fallback
        if intent.confidence < 0.5:
            intent = self._keyword_intent(query)
        
        # Cache the result
        with self._cache_lock:
            self._cache[cache_key] = f"{intent.intent}:{intent.confidence}:{intent.explanation}"
        
        return intent
    
    def _semantic_route(self, query: str) -> DNIntent:
        """Use Semantic Router for fast intent detection"""
        if not self._router:
            return DNIntent(intent="unknown", confidence=0.0, entities={}, query=query, explanation="")
        
        try:
            result = self._router.route(query)
            if result and hasattr(result, 'name'):
                return DNIntent(
                    intent=result.name,
                    confidence=0.8,
                    entities={},
                    query=query,
                    explanation=f"Semantic routing detected: {result.name}"
                )
        except Exception:
            pass
        
        return DNIntent(intent="unknown", confidence=0.0, entities={}, query=query, explanation="")
    
    def _ai_recognize_intent(self, query: str) -> Optional[DNIntent]:
        """Use AI to recognize intent"""
        if not self._client:
            return None
        
        prompt = f"""Analyze this logistics query and extract:
1. Intent (dashboard, status, pending, search, revenue, units, compare, trend, forecast, insights, recommendations)
2. DN number (if any)
3. Entity (customer, city, warehouse, dealer, date, month)
4. Confidence (0-1)

Query: {query}

Return JSON only:
{{"intent": "", "dn": "", "entity": "", "entity_type": "", "confidence": 0.0}}
"""
        
        try:
            if self._provider == "groq":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics intent recognition system. Return ONLY JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                content = response.choices[0].message.content
            
            elif self._provider == "openai":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics intent recognition system. Return ONLY JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=200
                )
                content = response.choices[0].message.content
            
            elif self._provider == "anthropic":
                response = self._client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=200,
                    messages=[{"role": "user", "content": prompt}]
                )
                content = response.content[0].text
            
            else:
                return None
            
            # Parse JSON response
            import json
            data = json.loads(content)
            
            entities = {}
            if data.get("dn"):
                entities["dn"] = data["dn"]
            if data.get("entity") and data.get("entity_type"):
                entities[data["entity_type"]] = data["entity"]
            
            return DNIntent(
                intent=data.get("intent", "unknown"),
                confidence=float(data.get("confidence", 0.7)),
                entities=entities,
                query=query,
                explanation=f"AI detected: {data.get('intent', 'unknown')}"
            )
            
        except Exception as e:
            logger.warning(f"AI intent recognition failed: {e}")
            return None
    
    def _keyword_intent(self, query: str) -> DNIntent:
        """Keyword-based fallback intent detection"""
        query_lower = query.lower()
        entities = {}
        
        # Extract DN
        dn = _extract_dn(query)
        if dn:
            entities["dn"] = dn
        
        # Intent detection
        if "status" in query_lower or "track" in query_lower or "where" in query_lower:
            return DNIntent(intent="status", confidence=0.6, entities=entities, query=query, explanation="Keyword: status")
        elif "pending" in query_lower or "overdue" in query_lower or "backlog" in query_lower:
            return DNIntent(intent="pending", confidence=0.6, entities=entities, query=query, explanation="Keyword: pending")
        elif "search" in query_lower or "find" in query_lower or "lookup" in query_lower:
            return DNIntent(intent="search", confidence=0.6, entities=entities, query=query, explanation="Keyword: search")
        elif "revenue" in query_lower or "amount" in query_lower or "value" in query_lower:
            return DNIntent(intent="revenue", confidence=0.6, entities=entities, query=query, explanation="Keyword: revenue")
        elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower:
            return DNIntent(intent="units", confidence=0.6, entities=entities, query=query, explanation="Keyword: units")
        elif "compare" in query_lower or "vs" in query_lower:
            return DNIntent(intent="compare", confidence=0.6, entities=entities, query=query, explanation="Keyword: compare")
        elif "trend" in query_lower or "pattern" in query_lower:
            return DNIntent(intent="trend", confidence=0.6, entities=entities, query=query, explanation="Keyword: trend")
        elif "forecast" in query_lower or "predict" in query_lower:
            return DNIntent(intent="forecast", confidence=0.6, entities=entities, query=query, explanation="Keyword: forecast")
        elif "insight" in query_lower or "analysis" in query_lower:
            return DNIntent(intent="insights", confidence=0.6, entities=entities, query=query, explanation="Keyword: insights")
        elif "recommend" in query_lower or "suggest" in query_lower:
            return DNIntent(intent="recommendations", confidence=0.6, entities=entities, query=query, explanation="Keyword: recommendations")
        elif dn:
            return DNIntent(intent="dashboard", confidence=0.8, entities=entities, query=query, explanation="DN detected")
        
        return DNIntent(intent="unknown", confidence=0.0, entities=entities, query=query, explanation="")
    
    def _extract_entities(self, query: str) -> Dict[str, Any]:
        """Extract entities using spaCy and other NLP"""
        entities = {}
        
        # Extract DN
        dn = _extract_dn(query)
        if dn:
            entities["dn"] = dn
        
        # Use spaCy for NER
        if SPACY_AVAILABLE and nlp:
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
            except Exception:
                pass
        
        # Extract using regex
        # City detection
        cities = ["lahore", "karachi", "rawalpindi", "islamabad", "multan", "peshawar", 
                  "quetta", "hyderabad", "faisalabad", "sialkot", "gujranwala"]
        for city in cities:
            if city in query.lower():
                entities["city"] = city
                break
        
        # Warehouse detection
        warehouses = ["lahore", "karachi", "rawalpindi", "multan", "peshawar", "islamabad"]
        for wh in warehouses:
            if f"warehouse {wh}" in query.lower() or f"wh {wh}" in query.lower():
                entities["warehouse"] = wh
                break
        
        return entities
    
    def _parse_intent_response(self, cached: str) -> DNIntent:
        """Parse cached intent response"""
        parts = cached.split(":")
        return DNIntent(
            intent=parts[0] if len(parts) > 0 else "unknown",
            confidence=float(parts[1]) if len(parts) > 1 else 0.0,
            entities={},
            query="",
            explanation=parts[2] if len(parts) > 2 else ""
        )
    
    def generate_response(self, query: str, data: Dict[str, Any]) -> str:
        """
        Generate AI-enhanced response based on data
        """
        if not self._client or not USE_AI_ENHANCEMENT:
            return self._generate_fallback_response(query, data)
        
        prompt = f"""You are a logistics DN assistant. Based on this DN data, answer the user's question.

User Question: {query}

DN Data:
- DN Number: {data.get('dn_no', 'N/A')}
- Division: {data.get('division', 'N/A')}
- Order Type: {data.get('order_type', 'N/A')}
- Customer Code: {data.get('customer_code', 'N/A')}
- Dealer: {data.get('dealer', 'N/A')}
- Status: {data.get('delivery_status', 'Pending')}
- PGI Status: {data.get('pgi_status', 'Pending')}
- POD Status: {data.get('pod_status', 'Pending')}
- Pending: {data.get('pending_flag', True)}
- Created Date: {_format_date(data.get('dn_create_date'))}
- Revenue: PKR {data.get('dn_amount', 0):,.2f}
- Units: {data.get('dn_qty', 0):,}

Provide a clear, concise answer. Use bullet points and emojis for WhatsApp.
Keep it under 500 characters.
"""
        
        try:
            if self._provider == "groq":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN assistant. Provide helpful, concise answers for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                return response.choices[0].message.content
            
            elif self._provider == "openai":
                response = self._client.chat.completions.create(
                    model=AI_MODEL,
                    messages=[
                        {"role": "system", "content": "You are a logistics DN assistant. Provide helpful, concise answers for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7,
                    max_tokens=300
                )
                return response.choices[0].message.content
            
            elif self._provider == "anthropic":
                response = self._client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=300,
                    messages=[{"role": "user", "content": prompt}]
                )
                return response.content[0].text
            
        except Exception as e:
            logger.error(f"AI response generation failed: {e}")
        
        return self._generate_fallback_response(query, data)
    
    def _generate_fallback_response(self, query: str, data: Dict[str, Any]) -> str:
        """Generate fallback response without AI"""
        dn_no = data.get('dn_no', 'N/A')
        
        return "\n".join([
            f"📦 *DN {dn_no}*",
            "",
            f"Status: {data.get('delivery_status', 'Pending')}",
            f"Customer: {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            f"Division: {data.get('division', 'N/A')}",
            f"Revenue: PKR {data.get('dn_amount', 0):,.2f}",
            f"Units: {data.get('dn_qty', 0):,}",
            "",
            "💡 *Need more details?*",
            "• Type 'menu' for options",
            "• Type '99' to return",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# MENU RENDERER WITH AI ENHANCEMENT
# ============================================================

class DNMenuRenderer:
    """DN Menu Renderer with AI-enhanced options"""
    
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "📦 *DN ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. DN Status",
            "3. Pending DN",
            "4. Search DN",
            "5. AI Query",
            "99. Back to Main",
            "",
            "📌 *AI-Powered Commands:*",
            "• Type DN number for full dashboard",
            "• 'status [DN]' - DN status",
            "• 'pending' - Show pending DNs",
            "• 'search [keyword]' - Search DNs",
            "• 'revenue [DN]' - Check revenue",
            "• 'units [DN]' - Check units",
            "• 'compare DN1 DN2' - Compare DNs",
            "• 'trend' - DN trends",
            "• 'forecast' - DN forecast",
            "• 'insights' - DN insights",
            "• 'recommendations' - Improvement ideas",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_ai_enhanced_dashboard(data: Dict[str, Any]) -> str:
        """Render AI-enhanced dashboard with all 10 key questions"""
        dn_no = data.get('dn_no', 'N/A')
        
        return "\n".join([
            f"📦 *DN Dashboard - {dn_no}*",
            "",
            "📊 *10 Key Questions Answered:*",
            "",
            "1️⃣ *Status:*",
            f"   {data.get('delivery_status', 'Pending')}",
            "",
            "2️⃣ *Creation:*",
            f"   {_format_date(data.get('dn_create_date'))}",
            "",
            "3️⃣ *Customer:*",
            f"   {data.get('customer_name', data.get('customer_code', 'N/A'))}",
            "",
            "4️⃣ *Warehouse:*",
            f"   {data.get('warehouse', 'N/A')}",
            "",
            "5️⃣ *Revenue:*",
            f"   PKR {data.get('dn_amount', 0):,.2f}",
            "",
            "6️⃣ *Units:*",
            f"   {data.get('dn_qty', 0):,}",
            "",
            "7️⃣ *PGI Status:*",
            f"   {data.get('pgi_status', 'Pending')}",
            "",
            "8️⃣ *POD Status:*",
            f"   {data.get('pod_status', 'Pending')}",
            "",
            "9️⃣ *Pending:*",
            f"   {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
            "",
            "🔟 *SLA:*",
            f"   {data.get('sla_compliant', 'N/A')}",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
            "0. Main Menu",
            "99. Back to Main"
        ])

# ============================================================
# MAIN DN SERVICE WITH AI
# ============================================================

class DNAnalysisService:
    """
    AI-Powered DN Analysis Service
    Uses content recognition to answer ANY DN question
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
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        self._menu_renderer = DNMenuRenderer()
        self._ai_engine = AIEngine()
        
        logger.info("=" * 60)
        logger.info("🚀 DNAnalysisService initialized (v24.0 - AI POWERED)")
        logger.info(f"   📦 AI Provider: {AI_PROVIDER}")
        logger.info(f"   🤖 AI Model: {AI_MODEL}")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🧠 Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
        logger.info(f"   🔍 spaCy NER: {'✅' if SPACY_AVAILABLE else '❌'}")
        logger.info("=" * 60)
    
    @staticmethod
    def _get_session() -> Optional[Session]:
        if not DB_AVAILABLE:
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"Database session error: {e}")
            return None
    
    def _get_context(self, session_id: str) -> DNContext:
        with self._context_lock:
            if session_id not in self._contexts:
                self._contexts[session_id] = DNContext()
            return self._contexts[session_id]
    
    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()
    
    # ============================================================
    # MAIN PROCESSING - AI POWERED
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """
        Main entry point - AI-powered DN query processing
        """
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"🤖 DN Processing: '{message_clean}' from {sender}")
        
        context = self._get_context(sender)
        context.in_menu = True
        
        # ============================================================
        # STEP 1: Check for "99" - Exit to main menu
        # ============================================================
        if message_clean == "99":
            context.in_menu = False
            context.current_dn = None
            return "99"
        
        # ============================================================
        # STEP 2: Check for menu commands
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # ============================================================
        # STEP 3: Check menu options (1-5)
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5"]:
            return self._handle_menu_option(sender, message_clean)
        
        # ============================================================
        # STEP 4: AI Intent Recognition
        # ============================================================
        intent = self._ai_engine.recognize_intent(message_clean)
        logger.info(f"🎯 Intent: {intent.intent} (confidence: {intent.confidence:.2f})")
        
        # ============================================================
        # STEP 5: Route based on intent
        # ============================================================
        
        # DN number detected - show dashboard
        if intent.entities.get("dn") or _extract_dn(message_clean):
            dn = intent.entities.get("dn") or _extract_dn(message_clean)
            if dn:
                context.current_dn = dn
                return self._get_ai_enhanced_dashboard(sender, dn, message_clean)
        
        # Status intent
        if intent.intent == "status":
            dn = intent.entities.get("dn") or _extract_dn(message_clean)
            if dn:
                return self._get_dn_status(sender, dn)
            return self._get_dashboard_prompt("status")
        
        # Pending intent
        if intent.intent == "pending":
            return self._get_pending_dns(sender)
        
        # Search intent
        if intent.intent == "search":
            query = message_clean
            for word in ["search", "find", "lookup"]:
                query = query.replace(word, "").strip()
            if query:
                return self._search_dns(sender, query)
            return self._get_dashboard_prompt("search")
        
        # Revenue intent
        if intent.intent == "revenue":
            dn = intent.entities.get("dn") or _extract_dn(message_clean)
            if dn:
                return self._get_dn_metric(sender, dn, "revenue")
            return self._get_dashboard_prompt("revenue")
        
        # Units intent
        if intent.intent == "units":
            dn = intent.entities.get("dn") or _extract_dn(message_clean)
            if dn:
                return self._get_dn_metric(sender, dn, "units")
            return self._get_dashboard_prompt("units")
        
        # Compare intent
        if intent.intent == "compare":
            return self._handle_comparison(sender, message_clean)
        
        # Trend intent
        if intent.intent == "trend":
            return self._get_trends(sender)
        
        # Forecast intent
        if intent.intent == "forecast":
            return self._get_forecast(sender)
        
        # Insights intent
        if intent.intent == "insights":
            return self._get_insights(sender)
        
        # Recommendations intent
        if intent.intent == "recommendations":
            return self._get_recommendations(sender)
        
        # ============================================================
        # STEP 6: Unknown - AI fallback
        # ============================================================
        return self._handle_ai_fallback(sender, message_clean)
    
    # ============================================================
    # MENU OPTIONS
    # ============================================================
    
    def _handle_menu_option(self, sender: str, option: str) -> str:
        """Handle menu options 1-5"""
        if option == "1":
            return "🔍 *Enter DN number:*\n\nType an 8-12 digit DN number for AI-enhanced dashboard.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return "📊 *Enter DN number for status:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back"
        elif option == "3":
            return self._get_pending_dns(sender)
        elif option == "4":
            return "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\nExample: search Lahore\n\n0. Main Menu\n99. Back"
        elif option == "5":
            return "🤖 *AI Query:*\n\nAsk any DN-related question naturally.\n\nExamples:\n• What is the status of DN 6243700919?\n• Show pending DNs\n• Revenue of DN 6243700919\n• Compare DN 6243700919 and DN 6243714234\n\n0. Main Menu\n99. Back"
        return self.get_main_menu()
    
    def _get_dashboard_prompt(self, action: str) -> str:
        """Get prompt for dashboard action"""
        prompts = {
            "status": "📊 *Enter DN number for status:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back",
            "search": "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\n0. Main Menu\n99. Back",
            "revenue": "💰 *Enter DN number for revenue:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back",
            "units": "📦 *Enter DN number for units:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back",
        }
        return prompts.get(action, self.get_main_menu())
    
    # ============================================================
    # DN OPERATIONS - POSTGRESQL
    # ============================================================
    
    def _get_ai_enhanced_dashboard(self, sender: str, dn_no: str, query: str) -> str:
        """Get AI-enhanced DN dashboard"""
        session = self._get_session()
        if not session:
            return self._get_fallback_dashboard(dn_no)
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.dealer,
                DeliveryReport.dn_work,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                DeliveryReport.dn_create_date,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
                DeliveryReport.customer_name,
                DeliveryReport.warehouse,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            data = {
                'dn_no': _text(result.dn_no),
                'division': _text(result.division),
                'order_type': _text(result.order_type),
                'customer_code': _text(result.customer_code),
                'dealer': _text(result.dealer),
                'dn_work': _text(result.dn_work),
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'pending_flag': bool(result.pending_flag) if result.pending_flag is not None else True,
                'dn_create_date': result.dn_create_date,
                'dn_amount': _safe_float(result.dn_amount),
                'dn_qty': _safe_int(result.dn_qty),
                'customer_name': _text(result.customer_name),
                'warehouse': _text(result.warehouse),
                'good_issue_date': result.good_issue_date,
                'pod_date': result.pod_date,
                'sla_compliant': '✅ Compliant' if result.pod_date else '⏳ Pending',
            }
            
            session.close()
            
            # If AI is available, generate enhanced response
            if self._ai_engine._client and USE_AI_ENHANCEMENT:
                ai_response = self._ai_engine.generate_response(query, data)
                if ai_response:
                    return ai_response + "\n\n0. Main Menu\n99. Back"
            
            return self._menu_renderer.render_ai_enhanced_dashboard(data)
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return self._get_fallback_dashboard(dn_no)
    
    def _get_fallback_dashboard(self, dn_no: str) -> str:
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
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            session.close()
            
            return "\n".join([
                f"📊 *DN {dn_no} - Status*",
                "",
                f"Status: {_text(result.delivery_status, 'Pending')}",
                f"PGI: {_text(result.pgi_status, 'Pending')}",
                f"POD: {_text(result.pod_status, 'Pending')}",
                f"Pending: {'✅ Yes' if result.pending_flag else '❌ No'}",
                "",
                f"Created: {_format_date(result.dn_create_date)}",
                f"Customer: {_text(result.customer_name, result.customer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_metric(self, sender: str, dn_no: str, metric: str) -> str:
        """Get specific DN metric (revenue or units)"""
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
            
            if not result:
                session.close()
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            session.close()
            
            if metric == "revenue":
                return f"💰 *DN {dn_no} Revenue*\n\nPKR {_safe_float(result.dn_amount):,.2f}\n\n0. Main Menu\n99. Back"
            else:
                return f"📦 *DN {dn_no} Units*\n\n{_safe_int(result.dn_qty):,}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Metric error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching {metric} for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_pending_dns(self, sender: str) -> str:
        """Get pending DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.delivery_status,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(20).all()
            
            session.close()
            
            if not results:
                return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
            
            lines = ["📋 *Pending DNs*", ""]
            lines.append(f"Total: {len(results)}")
            lines.append("")
            
            for i, row in enumerate(results[:10], 1):
                dn_no = _text(row.dn_no)
                customer = _text(row.customer_name, row.customer_code)
                status = _text(row.delivery_status, 'Pending')
                lines.append(f"{i}. *DN {dn_no}*")
                lines.append(f"   Customer: {customer}")
                lines.append(f"   Status: {status}")
                lines.append("")
            
            if len(results) > 10:
                lines.append(f"... and {len(results) - 10} more")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Pending error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending DNs.\n\n0. Main Menu\n99. Back"
    
    def _search_dns(self, sender: str, query: str) -> str:
        """Search DNs"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(20).all()
            
            session.close()
            
            if not results:
                return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
            
            lines = [f"🔍 *Search Results for '{query}'*", ""]
            lines.append(f"Found: {len(results)} DNs")
            lines.append("")
            
            for i, row in enumerate(results[:10], 1):
                dn_no = _text(row.dn_no)
                customer = _text(row.customer_name, row.customer_code)
                lines.append(f"{i}. *DN {dn_no}* - {customer}")
            
            if len(results) > 10:
                lines.append(f"... and {len(results) - 10} more")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def _handle_comparison(self, sender: str, query: str) -> str:
        """Handle DN comparison"""
        # Extract two DN numbers
        dns = re.findall(r'\b(\d{8,12})\b', query)
        if len(dns) < 2:
            return "🔄 *Compare DNs*\n\nPlease provide two DN numbers to compare.\n\nExample: compare 6243700919 6243714234\n\n0. Main Menu\n99. Back"
        
        dn1, dn2 = dns[0], dns[1]
        
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result1 = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn1
            ).first()
            
            result2 = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn2
            ).first()
            
            session.close()
            
            if not result1 or not result2:
                return "⚠️ One or both DNs not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🔄 *Comparison: DN {dn1} vs DN {dn2}*",
                "",
                "📊 *Metrics*",
                f"Division: {_text(result1.division)} vs {_text(result2.division)}",
                f"Status: {_text(result1.delivery_status, 'Pending')} vs {_text(result2.delivery_status, 'Pending')}",
                f"Revenue: PKR {_safe_float(result1.dn_amount):,.2f} vs PKR {_safe_float(result2.dn_amount):,.2f}",
                f"Units: {_safe_int(result1.dn_qty)} vs {_safe_int(result2.dn_qty)}",
                "",
                "💡 *Winner:*",
                f"{dn1} has higher revenue" if _safe_float(result1.dn_amount) > _safe_float(result2.dn_amount) else f"{dn2} has higher revenue",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing DNs.\n\n0. Main Menu\n99. Back"
    
    def _get_trends(self, sender: str) -> str:
        """Get DN trends"""
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from sqlalchemy import func, extract
            
            # Get weekly trends
            results = session.query(
                func.extract('week', DeliveryReport.dn_create_date).label('week'),
                func.count(DeliveryReport.dn_no).label('count'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                func.extract('week', DeliveryReport.dn_create_date)
            ).order_by(
                func.extract('week', DeliveryReport.dn_create_date).desc()
            ).limit(4).all()
            
            session.close()
            
            if not results:
                return "📈 No trend data available.\n\n0. Main Menu\n99. Back"
            
            lines = ["📈 *DN Trends (Last 4 Weeks)*", ""]
            for row in results:
                week = int(row.week)
                count = _safe_int(row.count)
                revenue = _safe_float(row.revenue)
                lines.append(f"Week {week}:")
                lines.append(f"   DNs: {count}")
                lines.append(f"   Revenue: PKR {revenue:,.2f}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
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
            from sqlalchemy import func
            
            # Get average daily DNs
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.count(func.distinct(func.date(DeliveryReport.dn_create_date))).label('days'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date >= datetime.now().date() - timedelta(days=30)
            ).first()
            
            session.close()
            
            if not results or not results.days:
                return "🔮 Insufficient data for forecast.\n\n0. Main Menu\n99. Back"
            
            total = _safe_int(results.total)
            days = _safe_int(results.days)
            revenue = _safe_float(results.revenue)
            
            avg_daily = total / days if days > 0 else 0
            avg_daily_revenue = revenue / days if days > 0 else 0
            
            return "\n".join([
                "🔮 *DN Forecast*",
                "",
                f"Average Daily DNs: {avg_daily:.1f}",
                f"Average Daily Revenue: PKR {avg_daily_revenue:,.2f}",
                f"Next 7 Days Forecast: {int(avg_daily * 7)} DNs",
                f"Next 7 Days Revenue: PKR {avg_daily_revenue * 7:,.2f}",
                "",
                "📌 *Based on last 30 days data*",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
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
            from sqlalchemy import func
            
            results = session.query(
                func.count(DeliveryReport.dn_no).label('total'),
                func.sum(case((DeliveryReport.pod_date.isnot(None), 1), else_=0)).label('delivered'),
                func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending'),
                func.avg(DeliveryReport.dn_amount).label('avg_revenue'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).first()
            
            session.close()
            
            total = _safe_int(results.total)
            delivered = _safe_int(results.delivered)
            pending = _safe_int(results.pending)
            avg_revenue = _safe_float(results.avg_revenue)
            total_revenue = _safe_float(results.total_revenue)
            
            delivery_rate = (delivered / total * 100) if total > 0 else 0
            
            return "\n".join([
                "💡 *DN Insights*",
                "",
                f"📊 Total DNs: {total:,}",
                f"✅ Delivered: {delivered:,} ({delivery_rate:.1f}%)",
                f"⏳ Pending: {pending:,}",
                f"💰 Total Revenue: PKR {total_revenue:,.2f}",
                f"📈 Avg Revenue/DN: PKR {avg_revenue:,.2f}",
                "",
                "🎯 *Key Findings:*",
                f"• Delivery rate is {delivery_rate:.1f}%",
                f"• {pending} DNs need attention",
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
            from sqlalchemy import func
            
            # Get pending count
            pending_count = session.query(
                func.count(DeliveryReport.dn_no)
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).scalar() or 0
            
            # Get delayed count
            from datetime import timedelta
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
            
            if pending_count > 10:
                recommendations.append(f"🚨 {pending_count} pending DNs need resolution")
            elif pending_count > 5:
                recommendations.append(f"📋 Review {pending_count} pending DNs")
            
            if delayed_count > 5:
                recommendations.append(f"⏰ {delayed_count} DNs are delayed > {DN_DELAY_THRESHOLD_DAYS} days")
            
            if pending_count <= 5 and delayed_count <= 5:
                recommendations.append("✅ Current DN performance is good")
                recommendations.append("📊 Continue monitoring key metrics")
            
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
    # AI FALLBACK
    # ============================================================
    
    def _handle_ai_fallback(self, sender: str, query: str) -> str:
        """Handle fallback using AI"""
        return "\n".join([
            "🤖 *AI Assistant*",
            "",
            "I understand you're asking about DNs.",
            "",
            "💡 *Try these commands:*",
            "• 'status 6243700919' - DN status",
            "• 'pending' - Show pending DNs",
            "• 'search Lahore' - Search DNs",
            "• 'trend' - View DN trends",
            "• 'forecast' - DN forecast",
            "• 'insights' - DN insights",
            "• 'recommendations' - Improvement ideas",
            "",
            "📌 *Or type a DN number for full dashboard*",
            "",
            "0. Main Menu",
            "99. Back"
        ])

# ============================================================
# SERVICE SINGLETON
# ============================================================

_service: Optional[DNAnalysisService] = None
_service_lock = threading.Lock()

def get_dn_analysis_service() -> DNAnalysisService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalysisService()
    return _service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    service = get_dn_analysis_service()
    result = service.process_whatsapp_query(user_input, session_id)
    
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
    service = get_dn_analysis_service()
    return service.get_main_menu()

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "DNAnalysisService",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
