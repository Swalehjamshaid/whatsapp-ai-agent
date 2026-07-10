#!/usr/bin/env python3
# ============================================================
# FILE: app/services/dn_analysis.py
# VERSION: 35.2 - DN PROMPT + CYCLE DAYS + STATUS UNIFORM
# ============================================================

"""
DN Analysis Service – Independent Stateful Module.
Provides rich DN dashboard, intent detection, conversation management,
and a clean DN entry prompt.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
from enum import Enum

# ============================================================
# AI LIBRARIES - Independent AI Engine
# ============================================================

try:
    import openai
except ImportError:
    openai = None

try:
    import groq
except ImportError:
    groq = None

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import litellm
except ImportError:
    litellm = None

try:
    from pydantic_ai import Agent
except ImportError:
    Agent = None

try:
    import instructor
except ImportError:
    instructor = None

try:
    from semantic_router import Route, RouteLayer, SemanticRouter
except ImportError:
    SemanticRouter = None

try:
    import flashrank
except ImportError:
    flashrank = None

try:
    import spacy
except ImportError:
    spacy = None

try:
    from rapidfuzz import fuzz, process
except ImportError:
    fuzz = None
    process = None

try:
    import nltk
    from nltk.tokenize import word_tokenize
    from nltk.corpus import stopwords
except ImportError:
    nltk = None
    word_tokenize = None
    stopwords = None

try:
    from textblob import TextBlob
except ImportError:
    TextBlob = None

try:
    import tiktoken
except ImportError:
    tiktoken = None

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, desc, and_, text
    from sqlalchemy.orm import Session, Query
    from app.database import SessionLocal, engine
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("✅ Database imports successful")
except ImportError as e:
    DB_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.error(f"❌ Database import error: {e}")

# ============================================================
# LOGGING SETUP
# ============================================================

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
SESSION_TIMEOUT_MINUTES = int(os.getenv("DN_SESSION_TIMEOUT_MINUTES", "30"))
PERFORMANCE_TARGET_INTENT = int(os.getenv("DN_PERFORMANCE_TARGET_INTENT", "40"))
PERFORMANCE_TARGET_DATABASE = int(os.getenv("DN_PERFORMANCE_TARGET_DATABASE", "100"))
PERFORMANCE_TARGET_RENDER = int(os.getenv("DN_PERFORMANCE_TARGET_RENDER", "10"))
PERFORMANCE_TARGET_TOTAL = int(os.getenv("DN_PERFORMANCE_TARGET_TOTAL", "300"))

# AI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# ============================================================
# UTILITY FUNCTIONS
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    """Safely convert value to string."""
    if value is None:
        return default
    return str(value).strip() or default

def _extract_dn(text: str) -> Optional[str]:
    """Extract DN number from text."""
    match = re.search(r'\b(\d{8,12})\b', text)
    return match.group(1) if match else None

def _is_valid_dn(dn: str) -> bool:
    """Validate DN number format."""
    if not dn:
        return False
    cleaned = re.sub(r'[\s-]', '', dn)
    return cleaned.isdigit() and 8 <= len(cleaned) <= 12

def _format_currency(amount: float) -> str:
    """Format currency amount."""
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: Union[int, float]) -> str:
    """Format number with commas."""
    if num is None:
        return "0"
    return f"{num:,}"

def _format_date(date_val: Any) -> str:
    """Format date to 'dd-MMM-yyyy' (e.g., 23-Jun-2026)"""
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return date_val
        elif isinstance(date_val, datetime):
            dt = date_val
        elif isinstance(date_val, date):
            dt = datetime.combine(date_val, datetime.min.time())
        else:
            return str(date_val)
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return str(date_val)

def _format_status(status: str) -> str:
    """
    Format status with emoji and color indicator.
    Returns e.g., "🟢 Delivered" for delivered/completed/received.
    """
    if not status:
        return "Unknown"
    s = status.lower()
    if s in ['delivered', 'completed', 'pod received', 'received', 'delivered']:
        return "🟢 Delivered"
    elif s in ['pending', 'in transit', 'processing']:
        return "🟡 Pending"
    elif s in ['cancelled', 'rejected']:
        return "🔴 Cancelled"
    else:
        return f"🟣 {status}"

# ============================================================
# ENUMS AND TYPES
# ============================================================

class DNMenuState(str, Enum):
    MAIN = "main"
    DASHBOARD = "dashboard"
    PENDING = "pending"
    SEARCH = "search"
    DETAILS = "details"
    FOLLOWUP = "followup"

class DNIntent(str, Enum):
    DASHBOARD = "dashboard"
    PENDING = "pending"
    SEARCH = "search"
    STATUS = "status"
    REVENUE = "revenue"
    UNITS = "units"
    CUSTOMER = "customer"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    PGI = "pgi"
    POD = "pod"
    DELAY = "delay"
    TRANSIT = "transit"
    AGEING = "ageing"
    TIMELINE = "timeline"
    SUMMARY = "summary"
    HELP = "help"
    EXIT = "exit"
    UNKNOWN = "unknown"

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class DNConversationContext:
    session_id: str
    locked: bool = True
    active_menu: DNMenuState = DNMenuState.MAIN
    current_dn: Optional[str] = None
    selected_option: Optional[int] = None
    history: List[Dict[str, Any]] = field(default_factory=list)
    last_query: str = ""
    last_answer: str = ""
    user_state: str = "idle"
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def touch(self) -> None:
        self.updated_at = datetime.now()
    
    def is_expired(self) -> bool:
        elapsed = datetime.now() - self.updated_at
        return elapsed.total_seconds() > SESSION_TIMEOUT_MINUTES * 60
    
    def add_history(self, query: str, answer: str) -> None:
        self.history.append({
            "query": query,
            "answer": answer,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
        self.last_query = query
        self.last_answer = answer
        self.touch()

@dataclass
class IntentResult:
    intent: DNIntent
    confidence: float
    entities: Dict[str, Any]
    raw_input: str
    processing_time_ms: float

@dataclass
class QueryResult:
    data: Any
    row_count: int
    execution_time_ms: float
    success: bool
    error: Optional[str] = None

# ============================================================
# RESPONSE TEMPLATES (unchanged)
# ============================================================

class DNResponseTemplates:
    @staticmethod
    def format_header(title: str) -> str:
        return f"📦 *{title}*"
    
    @staticmethod
    def format_divider() -> str:
        return "─" * 30
    
    @staticmethod
    def format_footer() -> str:
        return "0. Main Menu\n99. Back"
    
    @staticmethod
    def format_key_value(key: str, value: str) -> str:
        return f"{key}: {value}"
    
    @staticmethod
    def format_section(title: str, items: List[str]) -> str:
        lines = [f"📊 *{title}*", ""]
        lines.extend(items)
        return "\n".join(lines)
    
    @staticmethod
    def format_dn_info(dn_no: str, **fields) -> str:
        lines = [f"📦 *DN {dn_no}*", ""]
        for key, value in fields.items():
            if value is not None and value != "N/A":
                lines.append(f"• {key}: {value}")
        return "\n".join(lines)
    
    @staticmethod
    def format_list(title: str, items: List[Dict[str, str]], max_items: int = 15) -> str:
        if not items:
            return f"📋 *{title}*\n\n✅ No items found.\n\n{DNResponseTemplates.format_footer()}"
        lines = [f"📋 *{title}*", ""]
        lines.append(f"Total: {len(items)}")
        lines.append("")
        for i, item in enumerate(items[:max_items], 1):
            for key, value in item.items():
                if value:
                    lines.append(f"{i}. {key}: {value}")
            lines.append("")
        if len(items) > max_items:
            lines.append(f"... and {len(items) - max_items} more")
        lines.extend(["", DNResponseTemplates.format_footer()])
        return "\n".join(lines)

# ============================================================
# DN INTENT ENGINE (unchanged)
# ============================================================

class DNIntentEngine:
    def __init__(self):
        self._initialized = False
        self._initialize()
    
    def _initialize(self):
        if self._initialized:
            return
        logger.info("🤖 Initializing DN Intent Engine...")
        start_time = time.time()
        self._init_spacy()
        self._init_nltk()
        self._init_semantic_router()
        self._init_llm_clients()
        self._build_intent_patterns()
        self._initialized = True
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ DN Intent Engine initialized in {elapsed:.1f}ms")
    
    def _init_spacy(self):
        self.nlp = None
        if spacy:
            try:
                self.nlp = spacy.load("en_core_web_sm")
                logger.info("✅ spaCy loaded")
            except:
                try:
                    spacy.cli.download("en_core_web_sm")
                    self.nlp = spacy.load("en_core_web_sm")
                    logger.info("✅ spaCy downloaded and loaded")
                except Exception as e:
                    logger.warning(f"⚠️ spaCy initialization failed: {e}")
        else:
            logger.warning("⚠️ spaCy not available")
    
    def _init_nltk(self):
        self.nltk_available = False
        if nltk:
            try:
                nltk.data.find('tokenizers/punkt')
                nltk.data.find('corpora/stopwords')
            except LookupError:
                try:
                    nltk.download('punkt', quiet=True)
                    nltk.download('stopwords', quiet=True)
                except:
                    pass
            self.nltk_available = True
            logger.info("✅ NLTK initialized")
        else:
            logger.warning("⚠️ NLTK not available")
    
    def _init_semantic_router(self):
        self.semantic_router = None
        if SemanticRouter:
            try:
                routes = [
                    Route(name="dashboard", utterances=["show dashboard", "dn dashboard", "delivery note dashboard", "dn info", "delivery note info"]),
                    Route(name="pending", utterances=["pending dns", "pending delivery notes", "show pending", "pending list", "undelivered"]),
                    Route(name="status", utterances=["what is status", "delivery status", "check status", "status update", "current status"]),
                    Route(name="revenue", utterances=["how much revenue", "dn amount", "delivery amount", "sales amount", "what is the amount"]),
                    Route(name="units", utterances=["how many units", "quantity", "dn qty", "items", "products"]),
                    Route(name="customer", utterances=["who is customer", "customer name", "customer code", "customer info", "customer details"]),
                    Route(name="dealer", utterances=["who is dealer", "dealer name", "dealer code", "dealer info", "distributor"]),
                    Route(name="warehouse", utterances=["which warehouse", "warehouse name", "warehouse code", "storage location", "plant"]),
                    Route(name="pgi", utterances=["pgi status", "goods issue", "issue date", "pgi date", "when was it issued"]),
                    Route(name="pod", utterances=["pod status", "proof of delivery", "pod date", "delivery date", "when was it delivered"]),
                    Route(name="delay", utterances=["why delayed", "delay reason", "late delivery", "overdue", "delayed"]),
                    Route(name="transit", utterances=["transit time", "in transit", "delivery time", "how long in transit"]),
                    Route(name="ageing", utterances=["ageing", "how old", "days", "since", "delivery age"]),
                    Route(name="timeline", utterances=["timeline", "history", "chronology", "when", "dates"]),
                    Route(name="summary", utterances=["summary", "overview", "complete view", "all details", "full info"]),
                ]
                self.semantic_router = SemanticRouter(routes=routes)
                logger.info("✅ Semantic Router initialized")
            except Exception as e:
                logger.warning(f"⚠️ Semantic Router initialization failed: {e}")
        else:
            logger.warning("⚠️ Semantic Router not available")
    
    def _init_llm_clients(self):
        self.openai_client = None
        self.groq_client = None
        self.anthropic_client = None
        self.litellm_client = None
        if openai and OPENAI_API_KEY:
            try:
                self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                logger.info("✅ OpenAI client initialized")
            except Exception as e:
                logger.warning(f"⚠️ OpenAI initialization failed: {e}")
        if groq and GROQ_API_KEY:
            try:
                self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
                logger.info("✅ Groq client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Groq initialization failed: {e}")
        if anthropic and ANTHROPIC_API_KEY:
            try:
                self.anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                logger.info("✅ Anthropic client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Anthropic initialization failed: {e}")
        if litellm:
            self.litellm_client = litellm
            logger.info("✅ LiteLLM available")
    
    def _build_intent_patterns(self):
        self.intent_patterns = {
            DNIntent.DASHBOARD: ['dashboard', 'info', 'summary', 'overview'],
            DNIntent.PENDING: ['pending', 'undelivered', 'not delivered'],
            DNIntent.STATUS: ['status', 'state', 'current', 'update'],
            DNIntent.REVENUE: ['revenue', 'amount', 'value', 'price', 'cost'],
            DNIntent.UNITS: ['unit', 'qty', 'quantity', 'piece', 'item'],
            DNIntent.CUSTOMER: ['customer', 'client', 'account'],
            DNIntent.DEALER: ['dealer', 'distributor', 'partner'],
            DNIntent.WAREHOUSE: ['warehouse', 'storage', 'plant', 'location'],
            DNIntent.PGI: ['pgi', 'goods issue', 'issue'],
            DNIntent.POD: ['pod', 'proof of delivery', 'delivered'],
            DNIntent.DELAY: ['delay', 'late', 'overdue', 'slow'],
            DNIntent.TRANSIT: ['transit', 'shipping', 'transport'],
            DNIntent.AGEING: ['age', 'old', 'days', 'since'],
            DNIntent.TIMELINE: ['timeline', 'history', 'chronology'],
            DNIntent.SUMMARY: ['summary', 'complete', 'full'],
            DNIntent.HELP: ['help', 'support', 'assist'],
            DNIntent.EXIT: ['99', 'exit', 'quit', 'cancel', 'back'],
        }
    
    def _rapidfuzz_match(self, text: str) -> Optional[Tuple[str, float]]:
        if not fuzz or not process:
            return None
        text_lower = text.lower()
        best_match = None
        best_score = 0.0
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                score = fuzz.partial_ratio(text_lower, pattern)
                if score > best_score:
                    best_score = score
                    best_match = intent
        if best_score > 80:
            return (best_match.value, best_score / 100.0)
        return None
    
    def _spacy_match(self, text: str) -> Optional[Tuple[str, float]]:
        if not self.nlp:
            return None
        doc = self.nlp(text)
        entities = [ent.text.lower() for ent in doc.ents]
        nouns = [token.text.lower() for token in doc if token.pos_ in ['NOUN', 'PROPN']]
        lemmas = [token.lemma_.lower() for token in doc]
        for intent, patterns in self.intent_patterns.items():
            for pattern in patterns:
                if pattern in lemmas or pattern in nouns or pattern in entities:
                    return (intent.value, 0.85)
        return None
    
    def _semantic_router_match(self, text: str) -> Optional[Tuple[str, float]]:
        if not self.semantic_router:
            return None
        try:
            result = self.semantic_router(text)
            if result:
                return (result.name, result.confidence)
        except Exception as e:
            logger.debug(f"Semantic router error: {e}")
        return None
    
    def _llm_verify(self, text: str, candidates: List[Tuple[str, float]]) -> Tuple[str, float]:
        if not candidates:
            return (DNIntent.UNKNOWN.value, 0.0)
        for intent, confidence in candidates:
            if confidence > 0.9:
                return (intent, confidence)
        try:
            if self.openai_client:
                response = self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Classify the intent of this message for a Delivery Note analysis system. Return only the intent name."},
                        {"role": "user", "content": text}
                    ],
                    max_tokens=10,
                    temperature=0.1
                )
                intent_text = response.choices[0].message.content.strip().lower()
                for intent in DNIntent:
                    if intent.value in intent_text or intent_text in intent.value:
                        return (intent.value, 0.9)
        except Exception as e:
            logger.debug(f"LLM verification failed: {e}")
        return candidates[0]
    
    def detect_intent(self, text: str) -> IntentResult:
        start_time = time.time()
        if not text or not text.strip():
            return IntentResult(intent=DNIntent.UNKNOWN, confidence=0.0, entities={}, raw_input=text, processing_time_ms=0.0)
        text_clean = text.strip().lower()
        dn = _extract_dn(text)
        entities = {"dn": dn} if dn else {}
        result = self._rapidfuzz_match(text_clean)
        candidates = []
        if result:
            candidates.append(result)
        result = self._spacy_match(text_clean)
        if result:
            candidates.append(result)
        result = self._semantic_router_match(text_clean)
        if result:
            candidates.append(result)
        if candidates:
            intent, confidence = self._llm_verify(text_clean, candidates)
        else:
            intent = DNIntent.UNKNOWN.value
            confidence = 0.0
        if any(word in text_clean for word in ['help', 'support', '?']):
            intent = DNIntent.HELP.value
            confidence = max(confidence, 0.7)
        if text_clean in ['99', 'exit', 'quit', 'cancel']:
            intent = DNIntent.EXIT.value
            confidence = 1.0
        if dn and (intent == DNIntent.UNKNOWN.value or confidence < 0.5):
            intent = DNIntent.DASHBOARD.value
            confidence = 0.6
            entities = {"dn": dn}
        elapsed_ms = (time.time() - start_time) * 1000
        return IntentResult(intent=DNIntent(intent), confidence=confidence, entities=entities, raw_input=text, processing_time_ms=elapsed_ms)

# ============================================================
# DN RENDERER - Enhanced Rich Dashboard
# ============================================================

class DNRenderer:
    def __init__(self):
        self.templates = DNResponseTemplates()
    
    def render_dashboard(self, data: Dict[str, Any]) -> str:
        """Render the rich DN dashboard with all sections."""
        dn_no = data.get('dn_no', 'N/A')
        dealer = data.get('customer_name', 'N/A')
        city = data.get('ship_to_city', 'N/A')
        warehouse = data.get('warehouse', 'N/A')
        division = data.get('division', 'N/A')
        
        products_count = len(data.get('products', []))
        total_units = data.get('dn_qty', 0)
        revenue = data.get('dn_amount', 0)
        
        created = _format_date(data.get('dn_create_date'))
        pgi_date = _format_date(data.get('good_issue_date'))
        pod_date = _format_date(data.get('pod_date'))
        # Cycle days = PGI to POD (or creation to POD if no PGI)
        transit_days = data.get('transit_days', 0)
        if transit_days == 0 and pod_date != "N/A" and created != "N/A":
            # fallback: compute from creation to POD
            try:
                created_dt = datetime.strptime(data.get('dn_create_date'), '%Y-%m-%d') if isinstance(data.get('dn_create_date'), str) else None
                pod_dt = datetime.strptime(data.get('pod_date'), '%Y-%m-%d') if isinstance(data.get('pod_date'), str) else None
                if created_dt and pod_dt:
                    transit_days = (pod_dt - created_dt).days
            except:
                pass
        
        delivery_status = data.get('delivery_status', 'Pending')
        pgi_status = data.get('pgi_status', 'Pending')
        pod_status = data.get('pod_status', 'Pending')
        pending_flag = data.get('pending_flag', False)
        
        products = data.get('products', [])
        insight = self._generate_ai_insight(data)
        
        lines = []
        SEP = "━━━━━━━━━━━━━━━━━━━━━━"
        
        lines.append(SEP)
        lines.append("📦 DELIVERY NOTE INTELLIGENCE CENTER")
        lines.append(SEP)
        lines.append("")
        lines.append(f"🆔 DN")
        lines.append(f"{dn_no}")
        lines.append("")
        lines.append(f"👤 Dealer")
        lines.append(f"{dealer}")
        lines.append("")
        lines.append(f"🏙 Destination")
        lines.append(f"{city}")
        lines.append("")
        lines.append(f"🏭 Warehouse")
        lines.append(f"{warehouse}")
        lines.append("")
        lines.append(f"📦 Division")
        lines.append(f"{division}")
        lines.append("")
        
        lines.append(SEP)
        lines.append("📊 DELIVERY SUMMARY")
        lines.append(SEP)
        lines.append("")
        lines.append(f"📦 Products : {products_count}")
        lines.append(f"📦 Units    : {_format_number(total_units)}")
        lines.append(f"💰 Revenue  : {_format_currency(revenue)}")
        lines.append("")
        
        lines.append(SEP)
        lines.append("🚚 DELIVERY TIMELINE")
        lines.append(SEP)
        lines.append("")
        lines.append(f"📅 DN Created : {created}")
        lines.append(f"🚛 PGI Date   : {pgi_date}")
        lines.append(f"📦 POD Date   : {pod_date}")
        transit_display = f"{transit_days} Days" if transit_days > 0 else "N/A"
        lines.append(f"⏱ Total Cycle days     : {transit_display}")
        lines.append("")
        
        lines.append(SEP)
        lines.append("✅ CURRENT STATUS")
        lines.append(SEP)
        lines.append("")
        lines.append(f"🟢 Delivery : {_format_status(delivery_status)}")
        lines.append(f"🟢 PGI      : {_format_status(pgi_status)}")
        lines.append(f"🟢 POD      : {_format_status(pod_status)}")
        pending_text = "Yes" if pending_flag else "No"
        lines.append(f"🟢 Pending  : {pending_text}")
        lines.append("")
        
        if products:
            lines.append(SEP)
            lines.append("📦 PRODUCTS")
            lines.append(SEP)
            lines.append("")
            for item in products[:10]:
                model = item.get('model', 'N/A')
                qty = item.get('qty', 0)
                lines.append(f"• {model} × {qty}")
            if len(products) > 10:
                lines.append(f"... and {len(products)-10} more")
            lines.append("")
        
        lines.append(SEP)
        lines.append("🤖 AI INSIGHT")
        lines.append(SEP)
        lines.append("")
        lines.extend(insight)
        lines.append("")
        
        lines.append(SEP)
        lines.append("🔄 NEXT ACTION")
        lines.append(SEP)
        lines.append("")
        lines.append("📝 Enter another Delivery Note to search again.")
        lines.append("")
        lines.append("🏠 Reply *99* to return to the Main Menu.")
        lines.append(SEP)
        
        return "\n".join(lines)
    
    def _generate_ai_insight(self, data: Dict[str, Any]) -> List[str]:
        insights = []
        status = data.get('delivery_status', '').lower()
        transit_days = data.get('transit_days', 0)
        delay_days = data.get('delay_days', 0)
        threshold = DN_DELAY_THRESHOLD_DAYS
        
        if status in ['delivered', 'completed']:
            insights.append("✅ Delivered before SLA.")
            if transit_days <= 3:
                insights.append("🚚 Fast transit time. Excellent logistics.")
            else:
                insights.append("⏳ Transit time could be improved.")
        else:
            if delay_days > threshold:
                insights.append(f"⚠️ DELAYED by {delay_days} days. Investigate cause.")
            else:
                insights.append("⏳ In transit. Expected delivery soon.")
        
        if data.get('pending_flag', False):
            insights.append("⏳ Pending action required. Follow up with logistics.")
        
        revenue = data.get('dn_amount', 0)
        if revenue > 1_000_000:
            insights.append("💰 High value delivery. Ensure proper documentation.")
        
        if not insights:
            insights.append("✅ No operational issues detected.")
        return insights
    
    def render_dn_prompt(self) -> str:
        """Render the DN entry prompt shown when user enters the service."""
        return """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 DN INTELLIGENCE CENTER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Please enter a Delivery Note (DN) Number.

Example:
6243634099

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 Commands

🔎 Enter any Delivery Note Number to search.

🏠 Reply *99* to return to the Main Menu.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🤖 Awaiting Delivery Note..."""
    
    # Other render methods (pending, search, status, etc.) remain unchanged
    def render_pending(self, items: List[Dict[str, Any]]) -> str:
        if not items:
            return "📋 *Pending DNs*\n\n✅ No pending DNs found.\n\n" + self.templates.format_footer()
        formatted_items = []
        for item in items[:15]:
            formatted_items.append({
                "DN": item.get('dn_no', 'N/A'),
                "Customer": item.get('customer_name', 'N/A'),
                "Status": item.get('delivery_status', 'Pending')
            })
        return self.templates.format_list("Pending DNs", formatted_items)
    
    def render_search(self, query: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"🔍 No results found for '{query}'\n\n{self.templates.format_footer()}"
        formatted_items = []
        for item in items[:15]:
            formatted_items.append({
                "DN": item.get('dn_no', 'N/A'),
                "Customer": item.get('customer_name', 'N/A'),
                "Status": item.get('delivery_status', 'Pending')
            })
        return self.templates.format_list(f"Search Results for '{query}'", formatted_items)
    
    def render_status(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Status": data.get('delivery_status', 'Pending'),
            "PGI Status": data.get('pgi_status', 'Pending'),
            "POD Status": data.get('pod_status', 'Pending'),
            "Created": _format_date(data.get('dn_create_date')),
            "PGI Date": _format_date(data.get('good_issue_date')),
            "POD Date": _format_date(data.get('pod_date'))
        }
        lines = [self.templates.format_header(f"Status - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_revenue(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        amount = data.get('dn_amount', 0)
        return f"💰 *Revenue - DN {dn_no}*\n\n{_format_currency(amount)}\n\n{self.templates.format_footer()}"
    
    def render_units(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        qty = data.get('dn_qty', 0)
        return f"📦 *Units - DN {dn_no}*\n\n{_format_number(qty)}\n\n{self.templates.format_footer()}"
    
    def render_customer(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Name": data.get('customer_name'),
            "Code": data.get('customer_code'),
            "Model": data.get('customer_model')
        }
        lines = [self.templates.format_header(f"Customer - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_dealer(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Name": data.get('customer_name'),
            "Code": data.get('customer_code')
        }
        lines = [self.templates.format_header(f"Dealer - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_warehouse(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Warehouse": data.get('warehouse'),
            "City": data.get('ship_to_city')
        }
        lines = [self.templates.format_header(f"Warehouse - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_pgi(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "PGI Status": data.get('pgi_status', 'Pending'),
            "PGI Date": _format_date(data.get('good_issue_date')),
            "Work Order": data.get('dn_work')
        }
        lines = [self.templates.format_header(f"PGI - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_pod(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "POD Status": data.get('pod_status', 'Pending'),
            "POD Date": _format_date(data.get('pod_date')),
            "Delivery Status": data.get('delivery_status', 'Pending')
        }
        lines = [self.templates.format_header(f"POD - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_delay(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        days = data.get('delay_days', 0)
        threshold = data.get('threshold', DN_DELAY_THRESHOLD_DAYS)
        status = "⚠️ DELAYED" if days > threshold else "✅ ON TIME"
        lines = [
            self.templates.format_header(f"Delay Analysis - DN {dn_no}"),
            "",
            f"Status: {status}",
            f"Delay Days: {days}",
            f"Threshold: {threshold} days",
            "",
            self.templates.format_footer()
        ]
        return "\n".join(lines)
    
    def render_transit(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Transit Days": data.get('transit_days', 'N/A'),
            "PGI Date": _format_date(data.get('good_issue_date')),
            "POD Date": _format_date(data.get('pod_date')),
            "Status": data.get('delivery_status', 'Pending')
        }
        lines = [self.templates.format_header(f"Transit - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_ageing(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "Age (Days)": data.get('age_days', 'N/A'),
            "Created": _format_date(data.get('dn_create_date')),
            "Status": data.get('delivery_status', 'Pending')
        }
        lines = [self.templates.format_header(f"Ageing - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_timeline(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        events = []
        created = _format_date(data.get('dn_create_date'))
        pgi = _format_date(data.get('good_issue_date'))
        pod = _format_date(data.get('pod_date'))
        if created != "N/A":
            events.append(f"📋 Created: {created}")
        if pgi != "N/A":
            events.append(f"🚚 PGI: {pgi}")
        if pod != "N/A":
            events.append(f"✅ POD: {pod}")
        if not events:
            events = ["No timeline events available"]
        lines = [
            self.templates.format_header(f"Timeline - DN {dn_no}"),
            "",
            *events,
            "",
            self.templates.format_footer()
        ]
        return "\n".join(lines)
    
    def render_summary(self, data: Dict[str, Any]) -> str:
        dn_no = data.get('dn_no', 'N/A')
        fields = {
            "DN": dn_no,
            "Customer": data.get('customer_name'),
            "Customer Code": data.get('customer_code'),
            "Dealer": data.get('customer_name'),
            "Status": data.get('delivery_status', 'Pending'),
            "Amount": _format_currency(data.get('dn_amount', 0)),
            "Quantity": _format_number(data.get('dn_qty', 0)),
            "Division": data.get('division'),
            "Order Type": data.get('order_type'),
            "Created": _format_date(data.get('dn_create_date')),
            "Warehouse": data.get('warehouse'),
            "City": data.get('ship_to_city')
        }
        lines = [self.templates.format_header(f"Summary - DN {dn_no}"), ""]
        for key, value in fields.items():
            if value and value != "N/A" and value != "0" and value != "PKR 0.00":
                lines.append(f"• {key}: {value}")
        lines.extend(["", self.templates.format_footer()])
        return "\n".join(lines)
    
    def render_help(self) -> str:
        return "\n".join([
            "💡 *DN Commands:*",
            "",
            "• Type a DN number (8-12 digits) for dashboard",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "• status - Status of current DN",
            "• revenue - Revenue of current DN",
            "• units - Units of current DN",
            "• customer - Customer of current DN",
            "• dealer - Dealer of current DN",
            "• warehouse - Warehouse of current DN",
            "• pgi - PGI status",
            "• pod - POD status",
            "• delay - Delay analysis",
            "• transit - Transit time",
            "• ageing - Ageing analysis",
            "• timeline - Event timeline",
            "• summary - Full summary",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def render_main_menu(self) -> str:
        return "\n".join([
            "📦 *DN ANALYTICS MENU*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. Pending DN",
            "3. Search DN",
            "99. Back to Main",
            "",
            "📌 *Quick Commands:*",
            "• Type DN number for dashboard",
            "• pending - Show pending DNs",
            "• search [keyword] - Search DNs",
            "",
            "Reply with a number or DN number:"
        ])

# ============================================================
# DN DATABASE REPOSITORY - FIXED COLUMN NAMES
# ============================================================

class DNDatabaseRepository:
    def __init__(self):
        self._session_local = SessionLocal if DB_AVAILABLE else None
        logger.info(f"🗄️  Database repository initialized: {'connected' if self._session_local else 'unavailable'}")
    
    def _get_session(self) -> Optional[Session]:
        if not self._session_local:
            logger.error("❌ Database not available")
            return None
        try:
            return self._session_local()
        except Exception as e:
            logger.error(f"❌ Database session error: {e}")
            return None
    
    def _close_session(self, session: Optional[Session]):
        if session:
            try:
                session.close()
            except Exception as e:
                logger.debug(f"Session close error: {e}")
    
    def get_dn_by_number(self, dn_no: str) -> QueryResult:
        """Get DN by number, including products list."""
        start_time = time.time()
        session = self._get_session()
        if not session:
            return QueryResult(data=None, row_count=0, execution_time_ms=0, success=False, error="Database unavailable")
        
        try:
            result = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.division,
                DeliveryReport.order_type,
                DeliveryReport.customer_code,
                DeliveryReport.customer_name,
                DeliveryReport.customer_model,
                DeliveryReport.dn_work,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.warehouse,
                DeliveryReport.ship_to_city,
                DeliveryReport.remarks,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                self._close_session(session)
                return QueryResult(data=None, row_count=0, execution_time_ms=(time.time()-start_time)*1000, success=True)
            
            # Products
            products_result = session.query(
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_qty')
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).group_by(
                DeliveryReport.customer_model
            ).all()
            
            products = []
            for row in products_result:
                if row.customer_model:
                    products.append({
                        'model': _text(row.customer_model),
                        'qty': int(row.total_qty or 0)
                    })
            
            data = {
                'dn_no': _text(result.dn_no),
                'division': _text(result.division),
                'order_type': _text(result.order_type),
                'customer_code': _text(result.customer_code),
                'customer_name': _text(result.customer_name),
                'customer_model': _text(result.customer_model),
                'dn_work': _text(result.dn_work),
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'dn_create_date': _text(result.dn_create_date),
                'good_issue_date': _text(result.good_issue_date),
                'pod_date': _text(result.pod_date),
                'dn_qty': result.dn_qty or 0,
                'dn_amount': result.dn_amount or 0,
                'warehouse': _text(result.warehouse),
                'ship_to_city': _text(result.ship_to_city),
                'remarks': _text(result.remarks),
                'products': products,
            }
            # pending_flag based on pod_date
            data['pending_flag'] = (data.get('pod_date') == "N/A" or not data.get('pod_date'))
            data['age_days'] = self._calculate_age(data.get('dn_create_date'))
            data['delay_days'] = self._calculate_delay(data.get('dn_create_date'), data.get('delivery_status'))
            data['transit_days'] = self._calculate_transit(data.get('good_issue_date'), data.get('pod_date'))
            
            self._close_session(session)
            elapsed_ms = (time.time() - start_time) * 1000
            return QueryResult(data=data, row_count=1, execution_time_ms=elapsed_ms, success=True)
            
        except Exception as e:
            logger.error(f"❌ Database error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self._close_session(session)
            return QueryResult(data=None, row_count=0, execution_time_ms=(time.time()-start_time)*1000, success=False, error=str(e))
    
    def get_pending_dns(self, limit: int = 30) -> QueryResult:
        start_time = time.time()
        session = self._get_session()
        if not session:
            return QueryResult(data=[], row_count=0, execution_time_ms=0, success=False, error="Database unavailable")
        try:
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
            ).filter(
                DeliveryReport.pod_date.is_(None)
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            self._close_session(session)
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'dn_create_date': _text(row.dn_create_date),
                })
            elapsed_ms = (time.time() - start_time) * 1000
            return QueryResult(data=items, row_count=len(items), execution_time_ms=elapsed_ms, success=True)
        except Exception as e:
            logger.error(f"❌ Pending query error: {e}")
            self._close_session(session)
            return QueryResult(data=[], row_count=0, execution_time_ms=(time.time()-start_time)*1000, success=False, error=str(e))
    
    def search_dns(self, query: str, limit: int = 30) -> QueryResult:
        start_time = time.time()
        session = self._get_session()
        if not session:
            return QueryResult(data=[], row_count=0, execution_time_ms=0, success=False, error="Database unavailable")
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                    DeliveryReport.ship_to_city.ilike(search_pattern),
                    DeliveryReport.warehouse.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            self._close_session(session)
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'dn_create_date': _text(row.dn_create_date),
                })
            elapsed_ms = (time.time() - start_time) * 1000
            return QueryResult(data=items, row_count=len(items), execution_time_ms=elapsed_ms, success=True)
        except Exception as e:
            logger.error(f"❌ Search error: {e}")
            self._close_session(session)
            return QueryResult(data=[], row_count=0, execution_time_ms=(time.time()-start_time)*1000, success=False, error=str(e))
    
    def get_dn_stats(self) -> QueryResult:
        start_time = time.time()
        session = self._get_session()
        if not session:
            return QueryResult(data={}, row_count=0, execution_time_ms=0, success=False, error="Database unavailable")
        try:
            total = session.query(DeliveryReport).count()
            pending = session.query(DeliveryReport).filter(DeliveryReport.pod_date.is_(None)).count()
            delivered = session.query(DeliveryReport).filter(DeliveryReport.pod_date.is_not(None)).count()
            pgi_issued = session.query(DeliveryReport).filter(DeliveryReport.good_issue_date.is_not(None)).count()
            total_revenue = session.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            pending_revenue = session.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pod_date.is_(None)).scalar() or 0
            total_units = session.query(func.sum(DeliveryReport.dn_qty)).scalar() or 0
            self._close_session(session)
            data = {
                'total_dns': total,
                'pending_dns': pending,
                'delivered_dns': delivered,
                'pgi_issued': pgi_issued,
                'total_revenue': total_revenue,
                'pending_revenue': pending_revenue,
                'total_units': total_units,
            }
            elapsed_ms = (time.time() - start_time) * 1000
            return QueryResult(data=data, row_count=1, execution_time_ms=elapsed_ms, success=True)
        except Exception as e:
            logger.error(f"❌ Stats error: {e}")
            self._close_session(session)
            return QueryResult(data={}, row_count=0, execution_time_ms=(time.time()-start_time)*1000, success=False, error=str(e))
    
    def _calculate_age(self, create_date: Any) -> int:
        if not create_date or create_date == "N/A":
            return 0
        try:
            if isinstance(create_date, str):
                for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                    try:
                        date_obj = datetime.strptime(create_date, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    return 0
            elif isinstance(create_date, datetime):
                date_obj = create_date
            elif isinstance(create_date, date):
                date_obj = datetime.combine(create_date, datetime.min.time())
            else:
                return 0
            delta = datetime.now() - date_obj
            return max(0, delta.days)
        except Exception:
            return 0
    
    def _calculate_delay(self, create_date: Any, status: str) -> int:
        if status and status.lower() in ['delivered', 'completed']:
            return 0
        return self._calculate_age(create_date)
    
    def _calculate_transit(self, issue_date: Any, pod_date: Any) -> int:
        if not issue_date or not pod_date:
            return 0
        try:
            date_format = '%Y-%m-%d'
            if isinstance(issue_date, str):
                issue = datetime.strptime(issue_date[:10], date_format).date()
            elif isinstance(issue_date, date):
                issue = issue_date
            else:
                return 0
            if isinstance(pod_date, str):
                pod = datetime.strptime(pod_date[:10], date_format).date()
            elif isinstance(pod_date, date):
                pod = pod_date
            else:
                return 0
            delta = pod - issue
            return max(0, delta.days)
        except Exception:
            return 0

# ============================================================
# DN STATE MACHINE (unchanged)
# ============================================================

class DNStateMachine:
    def __init__(self):
        self._transitions = {
            DNMenuState.MAIN: {
                'dashboard': DNMenuState.DASHBOARD,
                'pending': DNMenuState.PENDING,
                'search': DNMenuState.SEARCH,
                'details': DNMenuState.DETAILS,
                'exit': DNMenuState.MAIN,
            },
            DNMenuState.DASHBOARD: {
                'followup': DNMenuState.FOLLOWUP,
                'exit': DNMenuState.MAIN,
                'back': DNMenuState.MAIN,
            },
            DNMenuState.PENDING: {
                'details': DNMenuState.DETAILS,
                'exit': DNMenuState.MAIN,
                'back': DNMenuState.MAIN,
            },
            DNMenuState.SEARCH: {
                'details': DNMenuState.DETAILS,
                'exit': DNMenuState.MAIN,
                'back': DNMenuState.MAIN,
            },
            DNMenuState.DETAILS: {
                'followup': DNMenuState.FOLLOWUP,
                'exit': DNMenuState.MAIN,
                'back': DNMenuState.MAIN,
            },
            DNMenuState.FOLLOWUP: {
                'followup': DNMenuState.FOLLOWUP,
                'exit': DNMenuState.MAIN,
                'back': DNMenuState.MAIN,
            },
        }
    
    def transition(self, current_state: DNMenuState, action: str) -> DNMenuState:
        transitions = self._transitions.get(current_state, {})
        return transitions.get(action, current_state)
    
    def can_transition(self, current_state: DNMenuState, action: str) -> bool:
        transitions = self._transitions.get(current_state, {})
        return action in transitions

# ============================================================
# DN CONVERSATION MANAGER (unchanged)
# ============================================================

class DNConversationManager:
    def __init__(self):
        self._contexts: Dict[str, DNConversationContext] = {}
        self._lock = threading.RLock()
        self._timeout_minutes = SESSION_TIMEOUT_MINUTES
        logger.info(f"🗣️  Conversation manager initialized (timeout: {self._timeout_minutes}m)")
    
    def get_context(self, session_id: str) -> DNConversationContext:
        with self._lock:
            self._cleanup_expired()
            if session_id not in self._contexts:
                context = DNConversationContext(session_id=session_id)
                self._contexts[session_id] = context
                logger.info(f"🆕 New DN context created for {session_id}")
                return context
            context = self._contexts[session_id]
            if context.is_expired():
                logger.info(f"⏰ DN context expired for {session_id}, creating new")
                del self._contexts[session_id]
                context = DNConversationContext(session_id=session_id)
                self._contexts[session_id] = context
            return context
    
    def update_context(self, session_id: str, **kwargs) -> Optional[DNConversationContext]:
        with self._lock:
            if session_id not in self._contexts:
                return None
            context = self._contexts[session_id]
            for key, value in kwargs.items():
                if hasattr(context, key):
                    setattr(context, key, value)
            context.touch()
            return context
    
    def add_history(self, session_id: str, query: str, answer: str) -> None:
        with self._lock:
            if session_id in self._contexts:
                self._contexts[session_id].add_history(query, answer)
    
    def destroy_context(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._contexts:
                del self._contexts[session_id]
                logger.info(f"🗑️  DN context destroyed for {session_id}")
                return True
            return False
    
    def is_locked(self, session_id: str) -> bool:
        with self._lock:
            if session_id in self._contexts:
                return self._contexts[session_id].locked
            return False
    
    def _cleanup_expired(self) -> None:
        expired = []
        for session_id, context in self._contexts.items():
            if context.is_expired():
                expired.append(session_id)
        for session_id in expired:
            del self._contexts[session_id]
            logger.info(f"🧹 Expired DN context removed for {session_id}")

# ============================================================
# MAIN DN ANALYTICS SERVICE
# ============================================================

class DNAnalyticsService:
    _instance: Optional["DNAnalyticsService"] = None
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
        self._service_name = "dn_analytics"
        self._version = "35.2"
        self._intent_engine = DNIntentEngine()
        self._renderer = DNRenderer()
        self._repository = DNDatabaseRepository()
        self._conversation_manager = DNConversationManager()
        self._state_machine = DNStateMachine()
        self._performance_logs: List[Dict[str, Any]] = []
        self._perf_lock = threading.RLock()
        logger.info("=" * 60)
        logger.info(f"🚀 DN Analytics Service v{self._version} initialized")
        logger.info(f"   🗄️  Database: {'Connected' if DB_AVAILABLE else 'Fallback'}")
        logger.info(f"   🤖 AI Engine: {'Initialized' if self._intent_engine._initialized else 'Limited'}")
        logger.info(f"   ⏰ Session Timeout: {SESSION_TIMEOUT_MINUTES}m")
        logger.info("=" * 60)
    
    def _log_performance(self, session_id: str, intent_result: IntentResult, 
                         query_result: Optional[QueryResult], render_time_ms: float,
                         total_time_ms: float) -> None:
        with self._perf_lock:
            self._performance_logs.append({
                'session_id': session_id,
                'intent': intent_result.intent.value,
                'intent_confidence': intent_result.confidence,
                'intent_time_ms': intent_result.processing_time_ms,
                'db_time_ms': query_result.execution_time_ms if query_result else 0,
                'render_time_ms': render_time_ms,
                'total_time_ms': total_time_ms,
                'row_count': query_result.row_count if query_result else 0,
                'timestamp': datetime.now().isoformat()
            })
            if len(self._performance_logs) > 1000:
                self._performance_logs = self._performance_logs[-1000:]
    
    def get_main_menu(self) -> str:
        return self._renderer.render_main_menu()
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "intent_engine": "active" if self._intent_engine._initialized else "degraded",
            "active_sessions": len(self._conversation_manager._contexts),
            "performance_logs": len(self._performance_logs),
            "exit_command": "99",
            "timestamp": datetime.now().isoformat()
        }
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        if not message or not message.strip():
            return self._renderer.render_dn_prompt()  # show prompt on empty
        
        start_time = time.time()
        message_clean = message.strip()
        logger.info(f"📨 DN Query: '{message_clean}' from {sender}")
        
        context = self._conversation_manager.get_context(sender)
        context.touch()
        
        # Exit command
        if message_clean in ["99", "exit", "quit", "cancel"]:
            self._conversation_manager.destroy_context(sender)
            logger.info(f"🚪 DN session exited for {sender}")
            return "99"
        
        # Main menu / help
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # Detect intent
        intent_result = self._intent_engine.detect_intent(message_clean)
        logger.info(f"🎯 Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})")
        
        if intent_result.entities.get('dn'):
            context.current_dn = intent_result.entities['dn']
            logger.info(f"📌 DN set: {context.current_dn}")
        
        response = self._process_intent(context, intent_result)
        
        # If response is None (unknown and no DN), show prompt
        if response is None:
            response = self._renderer.render_dn_prompt()
        
        context.add_history(message_clean, response)
        self._conversation_manager.update_context(
            sender,
            active_menu=context.active_menu,
            last_query=message_clean,
            last_answer=response
        )
        
        total_time_ms = (time.time() - start_time) * 1000
        self._log_performance(sender, intent_result, None, 0, total_time_ms)
        logger.info(f"⏱️  Total processing time: {total_time_ms:.1f}ms")
        return response
    
    def _process_intent(self, context: DNConversationContext, intent: IntentResult) -> Optional[str]:
        """Returns None if we should show the prompt (no DN, unknown)."""
        if intent.intent == DNIntent.EXIT:
            return "99"
        if intent.intent == DNIntent.HELP:
            return self._renderer.render_help()
        if intent.intent == DNIntent.PENDING:
            context.active_menu = DNMenuState.PENDING
            return self._handle_pending(context, intent)
        if intent.intent == DNIntent.SEARCH:
            context.active_menu = DNMenuState.SEARCH
            return self._handle_search(context, intent)
        if intent.intent in [DNIntent.DASHBOARD, DNIntent.STATUS, DNIntent.REVENUE,
                           DNIntent.UNITS, DNIntent.CUSTOMER, DNIntent.DEALER,
                           DNIntent.WAREHOUSE, DNIntent.PGI, DNIntent.POD,
                           DNIntent.DELAY, DNIntent.TRANSIT, DNIntent.AGEING,
                           DNIntent.TIMELINE, DNIntent.SUMMARY]:
            if not context.current_dn and not intent.entities.get('dn'):
                return None  # prompt for DN
            dn = intent.entities.get('dn') or context.current_dn
            context.current_dn = dn
            context.active_menu = DNMenuState.DETAILS
            return self._handle_dn_intent(context, intent)
        
        # Check if the message contains a DN number
        dn = _extract_dn(intent.raw_input)
        if dn and _is_valid_dn(dn):
            context.current_dn = dn
            context.active_menu = DNMenuState.DASHBOARD
            intent.entities['dn'] = dn
            return self._handle_dn_intent(context, intent)
        
        # If we're in the main state and no DN, show prompt
        if context.active_menu == DNMenuState.MAIN:
            return None  # prompt
        
        # Otherwise show help
        return "❌ I didn't understand that.\n\n" + self._renderer.render_help()
    
    def _handle_pending(self, context: DNConversationContext, intent: IntentResult) -> str:
        result = self._repository.get_pending_dns(limit=30)
        if not result.success:
            return f"⚠️ Error fetching pending DNs: {result.error}\n\n0. Main Menu\n99. Back"
        return self._renderer.render_pending(result.data)
    
    def _handle_search(self, context: DNConversationContext, intent: IntentResult) -> str:
        query = intent.raw_input
        for word in ["search", "find", "lookup"]:
            query = query.replace(word, "").strip()
        if not query:
            return "🔍 Please specify what to search.\n\n0. Main Menu\n99. Back"
        result = self._repository.search_dns(query, limit=30)
        if not result.success:
            return f"⚠️ Error searching: {result.error}\n\n0. Main Menu\n99. Back"
        return self._renderer.render_search(query, result.data)
    
    def _handle_dn_intent(self, context: DNConversationContext, intent: IntentResult) -> str:
        dn = intent.entities.get('dn') or context.current_dn
        if not dn:
            return "🔍 Please provide a DN number.\n\n0. Main Menu\n99. Back"
        result = self._repository.get_dn_by_number(dn)
        if not result.success:
            return f"⚠️ Error fetching DN {dn}: {result.error}\n\n0. Main Menu\n99. Back"
        if not result.data:
            return f"⚠️ DN '{dn}' not found.\n\n0. Main Menu\n99. Back"
        intent_map = {
            DNIntent.DASHBOARD: self._renderer.render_dashboard,
            DNIntent.STATUS: self._renderer.render_status,
            DNIntent.REVENUE: self._renderer.render_revenue,
            DNIntent.UNITS: self._renderer.render_units,
            DNIntent.CUSTOMER: self._renderer.render_customer,
            DNIntent.DEALER: self._renderer.render_dealer,
            DNIntent.WAREHOUSE: self._renderer.render_warehouse,
            DNIntent.PGI: self._renderer.render_pgi,
            DNIntent.POD: self._renderer.render_pod,
            DNIntent.DELAY: self._renderer.render_delay,
            DNIntent.TRANSIT: self._renderer.render_transit,
            DNIntent.AGEING: self._renderer.render_ageing,
            DNIntent.TIMELINE: self._renderer.render_timeline,
            DNIntent.SUMMARY: self._renderer.render_summary,
        }
        renderer = intent_map.get(intent.intent, self._renderer.render_dashboard)
        return renderer(result.data)

# ============================================================
# MODULE EXPORTS
# ============================================================

_service: Optional[DNAnalyticsService] = None
_service_lock = threading.Lock()

def get_dn_analytics_service() -> DNAnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = DNAnalyticsService()
    return _service

# Alias for ai_provider_service.py
get_dn_analysis_service = get_dn_analytics_service

def process_dn_menu(session_id: str, user_input: str) -> Dict[str, Any]:
    service = get_dn_analytics_service()
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
    service = get_dn_analytics_service()
    return service.get_main_menu()

def is_session_locked(session_id: str) -> bool:
    service = get_dn_analytics_service()
    return service._conversation_manager.is_locked(session_id)

def get_session_context(session_id: str) -> Optional[Dict[str, Any]]:
    service = get_dn_analytics_service()
    context = service._conversation_manager.get_context(session_id)
    if context:
        return {
            "session_id": context.session_id,
            "locked": context.locked,
            "active_menu": context.active_menu.value if context.active_menu else None,
            "current_dn": context.current_dn,
            "history_count": len(context.history),
            "last_query": context.last_query,
            "created_at": context.created_at.isoformat(),
            "updated_at": context.updated_at.isoformat(),
            "is_expired": context.is_expired()
        }
    return None

def get_performance_stats() -> Dict[str, Any]:
    service = get_dn_analytics_service()
    with service._perf_lock:
        logs = service._performance_logs
        if not logs:
            return {
                "total_requests": 0,
                "avg_total_time_ms": 0,
                "avg_intent_time_ms": 0,
                "avg_db_time_ms": 0,
                "min_total_time_ms": 0,
                "max_total_time_ms": 0
            }
        total_times = [log['total_time_ms'] for log in logs]
        intent_times = [log['intent_time_ms'] for log in logs]
        db_times = [log['db_time_ms'] for log in logs if log['db_time_ms'] > 0]
        return {
            "total_requests": len(logs),
            "avg_total_time_ms": sum(total_times) / len(total_times),
            "avg_intent_time_ms": sum(intent_times) / len(intent_times),
            "avg_db_time_ms": sum(db_times) / len(db_times) if db_times else 0,
            "min_total_time_ms": min(total_times),
            "max_total_time_ms": max(total_times),
            "target_total_ms": PERFORMANCE_TARGET_TOTAL
        }

__all__ = [
    "DNAnalyticsService",
    "get_dn_analytics_service",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
    "is_session_locked",
    "get_session_context",
    "get_performance_stats",
    "DNAnalysisService",
]

DNAnalysisService = DNAnalyticsService
