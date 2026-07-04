# ============================================================
# FILE: app/services/ai_provider_service.py
# VERSION: 54.0 - GATEWAY WITH AI DASHBOARD DETECTION
# ============================================================

"""
File: app/services/ai_provider_service.py
Version: 54.0 - GATEWAY WITH AI DASHBOARD DETECTION

================================================================================
PURPOSE
================================================================================

This is the SOLE GATEWAY for all WhatsApp interactions.

Its ONLY responsibilities are:
1. Display the main dashboard
2. Detect user selection by menu number, dashboard name, or alias (AI-enhanced)
3. Route to ONLY ONE of the seven approved modules
4. Lock the session to that module
5. Forward ALL subsequent messages to the locked module
6. Return to main dashboard only when module signals "__EXIT__"

================================================================================
AI USAGE
================================================================================

AI is used ONLY for:
1. Understanding natural language dashboard requests
2. Matching user intent to the correct dashboard
3. Handling typos and variations in dashboard names

AI is NOT used for:
- Business logic
- Analytics
- SQL queries
- Answering business questions
- Any domain-specific processing

================================================================================
FORBIDDEN
================================================================================

This file is NOT ALLOWED to perform ANY business logic:
- ❌ Search DN, Dealer, Warehouse, Product, City
- ❌ Build Dashboard
- ❌ Execute SQL
- ❌ Query PostgreSQL
- ❌ Calculate KPI, Revenue, Units, Pending
- ❌ Generate Executive Dashboard
- ❌ Call Analytics Functions
- ❌ Detect Follow-up Questions
- ❌ Detect Dashboard Type (when locked)
- ❌ Entity Detection (when locked)
- ❌ AI Engine (when locked)
- ❌ Answer questions directly

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable, Tuple

logger = logging.getLogger(__name__)

# ============================================================
# BLOCK 1: AI LIBRARIES - FOR DASHBOARD DETECTION ONLY
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
    from semantic_router import Route, SemanticRouter
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False

try:
    import tiktoken
    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

logger.info("=" * 60)
logger.info("🔍 AI Libraries Status:")
logger.info(f"   OpenAI: {'✅' if OPENAI_AVAILABLE else '❌'}")
logger.info(f"   Groq: {'✅' if GROQ_AVAILABLE else '❌'}")
logger.info(f"   RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
logger.info(f"   SpaCy: {'✅' if SPACY_AVAILABLE else '❌'}")
logger.info(f"   TextBlob: {'✅' if TEXTBLOB_AVAILABLE else '❌'}")
logger.info(f"   NLTK: {'✅' if NLTK_AVAILABLE else '❌'}")
logger.info(f"   Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
logger.info(f"   Tiktoken: {'✅' if TIKTOKEN_AVAILABLE else '❌'}")
logger.info("=" * 60)

# ============================================================
# BLOCK 2: CONFIGURATION
# ============================================================

SESSION_TIMEOUT_SECONDS = int(os.getenv("SESSION_TIMEOUT_SECONDS", "1800"))  # 30 minutes
EXIT_SIGNAL = "__EXIT__"

# AI Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
USE_AI_DETECTION = os.getenv("USE_AI_DETECTION", "true").lower() == "true"
AI_CONFIDENCE_THRESHOLD = float(os.getenv("AI_CONFIDENCE_THRESHOLD", "0.7"))

# ============================================================
# BLOCK 3: ENUMS
# ============================================================

class ModuleType(Enum):
    """Available domain modules - EXACTLY 7"""
    NATIONAL = "national"
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    CITY = "city"
    AI = "ai"

# ============================================================
# BLOCK 4: DATA CLASSES
# ============================================================

@dataclass
class Session:
    """Session state for a user."""
    sender: str
    locked: bool = False
    module_type: Optional[ModuleType] = None
    module_name: Optional[str] = None
    file_name: Optional[str] = None
    menu_id: Optional[int] = None
    service_instance: Optional[Any] = None
    entered_at: Optional[datetime] = None
    last_activity: datetime = field(default_factory=datetime.now)
    history: List[Dict[str, Any]] = field(default_factory=list)
    
    def update_activity(self):
        self.last_activity = datetime.now()
    
    def is_expired(self, timeout_seconds: int = SESSION_TIMEOUT_SECONDS) -> bool:
        elapsed = (datetime.now() - self.last_activity).total_seconds()
        return elapsed > timeout_seconds
    
    def add_history(self, query: str, response: str):
        self.history.append({
            "query": query,
            "response": response[:200] if len(response) > 200 else response,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.history) > 100:
            self.history = self.history[-100:]
    
    def lock(self, module_type: ModuleType, module_name: str, file_name: str, 
             menu_id: int, service_instance: Any):
        self.locked = True
        self.module_type = module_type
        self.module_name = module_name
        self.file_name = file_name
        self.menu_id = menu_id
        self.service_instance = service_instance
        self.entered_at = datetime.now()
        self.update_activity()
        logger.info(f"🔒 Session LOCKED: {self.sender} → {module_name}")
    
    def unlock(self):
        old_module = self.module_name
        self.locked = False
        self.module_type = None
        self.module_name = None
        self.file_name = None
        self.menu_id = None
        self.service_instance = None
        self.entered_at = None
        self.update_activity()
        logger.info(f"🔓 Session UNLOCKED: {self.sender} from {old_module}")
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "sender": self.sender,
            "locked": self.locked,
            "module_type": self.module_type.value if self.module_type else None,
            "module_name": self.module_name,
            "file_name": self.file_name,
            "menu_id": self.menu_id,
            "entered_at": self.entered_at.isoformat() if self.entered_at else None,
            "last_activity": self.last_activity.isoformat(),
            "history_count": len(self.history),
            "is_expired": self.is_expired()
        }

@dataclass
class MenuItem:
    """Menu item configuration - EXACTLY 7"""
    id: int
    name: str
    aliases: List[str]
    module_type: ModuleType
    file: str
    loader: Callable
    
    def matches(self, text: str) -> bool:
        text_lower = text.strip().lower()
        
        if text_lower == str(self.id):
            return True
        if text_lower == self.name.lower():
            return True
        for alias in self.aliases:
            if text_lower == alias.lower():
                return True
            if alias.lower() in text_lower:
                return True
        return False
    
    def get_all_patterns(self) -> List[str]:
        """Get all patterns including id, name, and aliases."""
        patterns = [str(self.id), self.name.lower()]
        patterns.extend([a.lower() for a in self.aliases])
        return patterns

# ============================================================
# BLOCK 5: AI DASHBOARD DETECTOR
# ============================================================

class AIDashboardDetector:
    """
    AI-powered dashboard detection.
    Uses multiple AI techniques to understand natural language requests.
    """
    
    def __init__(self):
        self._initialized = False
        self._initialize()
    
    def _initialize(self):
        if self._initialized:
            return
        
        logger.info("🤖 Initializing AI Dashboard Detector...")
        start_time = time.time()
        
        # Initialize NLP components
        self._init_spacy()
        self._init_nltk()
        self._init_semantic_router()
        self._init_llm_clients()
        
        self._initialized = True
        elapsed = (time.time() - start_time) * 1000
        logger.info(f"✅ AI Dashboard Detector initialized in {elapsed:.1f}ms")
    
    def _init_spacy(self):
        """Initialize spaCy for NLP."""
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
                    logger.warning(f"⚠️ spaCy initialization failed: {e}")
        else:
            logger.warning("⚠️ spaCy not available")
    
    def _init_nltk(self):
        """Initialize NLTK."""
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
            logger.info(f"✅ NLTK initialized: {self.nltk_available}")
        else:
            logger.warning("⚠️ NLTK not available")
    
    def _init_semantic_router(self):
        """Initialize Semantic Router."""
        self.semantic_router = None
        if SEMANTIC_ROUTER_AVAILABLE and SemanticRouter:
            try:
                routes = [
                    Route(name="national", utterances=[
                        "national dashboard", "national kpi", "national", "kpi", "pakistan", "overall"
                    ]),
                    Route(name="dn", utterances=[
                        "dn dashboard", "dn intelligence", "dn", "delivery note", "delivery", "pending dn"
                    ]),
                    Route(name="dealer", utterances=[
                        "dealer dashboard", "dealer analytics", "dealer", "distributor"
                    ]),
                    Route(name="warehouse", utterances=[
                        "warehouse dashboard", "warehouse analytics", "warehouse", "storage", "inventory"
                    ]),
                    Route(name="product", utterances=[
                        "product dashboard", "product analytics", "product", "material", "sku", "model"
                    ]),
                    Route(name="city", utterances=[
                        "city dashboard", "city analytics", "city", "location", "region"
                    ]),
                    Route(name="ai", utterances=[
                        "ai assistant", "assistant", "ai", "chat", "help", "general ai"
                    ]),
                ]
                self.semantic_router = SemanticRouter(routes=routes)
                logger.info("✅ Semantic Router initialized")
            except Exception as e:
                logger.warning(f"⚠️ Semantic Router initialization failed: {e}")
        else:
            logger.warning("⚠️ Semantic Router not available")
    
    def _init_llm_clients(self):
        """Initialize LLM clients for detection."""
        self.openai_client = None
        self.groq_client = None
        
        if OPENAI_AVAILABLE and openai and OPENAI_API_KEY:
            try:
                self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
                logger.info("✅ OpenAI client initialized")
            except Exception as e:
                logger.warning(f"⚠️ OpenAI initialization failed: {e}")
        
        if GROQ_AVAILABLE and groq and GROQ_API_KEY:
            try:
                self.groq_client = groq.Groq(api_key=GROQ_API_KEY)
                logger.info("✅ Groq client initialized")
            except Exception as e:
                logger.warning(f"⚠️ Groq initialization failed: {e}")
    
    def detect_dashboard(self, text: str, menu_items: List[MenuItem]) -> Optional[Tuple[MenuItem, float]]:
        """
        Detect which dashboard the user wants using AI.
        Returns (MenuItem, confidence) or None.
        """
        if not text or not text.strip():
            return None
        
        text_clean = text.strip()
        logger.info(f"🔍 AI Detecting dashboard for: '{text_clean}'")
        
        # Stage 1: Exact match (fastest)
        for item in menu_items:
            if item.matches(text_clean):
                logger.info(f"✅ Exact match: {item.name}")
                return (item, 1.0)
        
        # Stage 2: RapidFuzz fuzzy matching
        if RAPIDFUZZ_AVAILABLE:
            result = self._rapidfuzz_match(text_clean, menu_items)
            if result:
                return result
        
        # Stage 3: SpaCy semantic matching
        if SPACY_AVAILABLE and self.nlp:
            result = self._spacy_match(text_clean, menu_items)
            if result:
                return result
        
        # Stage 4: Semantic Router
        if SEMANTIC_ROUTER_AVAILABLE and self.semantic_router:
            result = self._semantic_router_match(text_clean, menu_items)
            if result:
                return result
        
        # Stage 5: LLM verification (most expensive)
        if USE_AI_DETECTION:
            result = self._llm_match(text_clean, menu_items)
            if result:
                return result
        
        logger.info(f"❌ No dashboard detected for: '{text_clean}'")
        return None
    
    def _rapidfuzz_match(self, text: str, menu_items: List[MenuItem]) -> Optional[Tuple[MenuItem, float]]:
        """Use RapidFuzz for fast fuzzy matching."""
        if not RAPIDFUZZ_AVAILABLE:
            return None
        
        text_lower = text.lower()
        best_match = None
        best_score = 0.0
        
        for item in menu_items:
            patterns = item.get_all_patterns()
            for pattern in patterns:
                score = fuzz.partial_ratio(text_lower, pattern)
                if score > best_score:
                    best_score = score
                    best_match = item
        
        if best_score > 80:
            logger.info(f"✅ RapidFuzz match: {best_match.name} ({best_score:.1f}%)")
            return (best_match, best_score / 100.0)
        
        return None
    
    def _spacy_match(self, text: str, menu_items: List[MenuItem]) -> Optional[Tuple[MenuItem, float]]:
        """Use spaCy for semantic understanding."""
        if not SPACY_AVAILABLE or not self.nlp:
            return None
        
        doc = self.nlp(text)
        lemmas = [token.lemma_.lower() for token in doc]
        
        for item in menu_items:
            patterns = item.get_all_patterns()
            for pattern in patterns:
                if pattern in lemmas:
                    logger.info(f"✅ SpaCy match: {item.name}")
                    return (item, 0.85)
        
        return None
    
    def _semantic_router_match(self, text: str, menu_items: List[MenuItem]) -> Optional[Tuple[MenuItem, float]]:
        """Use Semantic Router for intent classification."""
        if not SEMANTIC_ROUTER_AVAILABLE or not self.semantic_router:
            return None
        
        try:
            result = self.semantic_router(text)
            if result and hasattr(result, 'name'):
                route_name = result.name
                for item in menu_items:
                    if item.module_type.value == route_name:
                        confidence = getattr(result, 'confidence', 0.7)
                        logger.info(f"✅ Semantic Router match: {item.name} ({confidence:.2f})")
                        return (item, confidence)
        except Exception as e:
            logger.debug(f"Semantic router error: {e}")
        
        return None
    
    def _llm_match(self, text: str, menu_items: List[MenuItem]) -> Optional[Tuple[MenuItem, float]]:
        """Use LLM for final verification."""
        # Build prompt
        dashboard_list = "\n".join([f"- {item.name}" for item in menu_items])
        
        prompt = f"""Given this user message: "{text}"

Which dashboard should this be routed to from this list?
{dashboard_list}

Return ONLY the exact dashboard name from the list, nothing else.
If none match, return "UNKNOWN"."""

        try:
            # Try Groq first
            if self.groq_client:
                response = self.groq_client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": "You are a routing assistant. Return only the exact dashboard name."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=50
                )
                result = response.choices[0].message.content.strip()
                logger.info(f"🔍 Groq response: '{result}'")
                
                for item in menu_items:
                    if result.lower() == item.name.lower():
                        logger.info(f"✅ Groq match: {item.name}")
                        return (item, 0.9)
            
            # Try OpenAI
            if self.openai_client:
                response = self.openai_client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a routing assistant. Return only the exact dashboard name."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    max_tokens=50
                )
                result = response.choices[0].message.content.strip()
                logger.info(f"🔍 OpenAI response: '{result}'")
                
                for item in menu_items:
                    if result.lower() == item.name.lower():
                        logger.info(f"✅ OpenAI match: {item.name}")
                        return (item, 0.9)
                        
        except Exception as e:
            logger.debug(f"LLM match error: {e}")
        
        return None
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for AI detector."""
        return {
            "initialized": self._initialized,
            "spacy": SPACY_AVAILABLE and self.nlp is not None,
            "nltk": self.nltk_available,
            "semantic_router": SEMANTIC_ROUTER_AVAILABLE and self.semantic_router is not None,
            "openai": OPENAI_AVAILABLE and self.openai_client is not None,
            "groq": GROQ_AVAILABLE and self.groq_client is not None,
            "rapidfuzz": RAPIDFUZZ_AVAILABLE,
            "use_ai_detection": USE_AI_DETECTION,
        }

# ============================================================
# BLOCK 6: SERVICE REGISTRY - EXACTLY 7 MODULES
# ============================================================

class ServiceRegistry:
    _instance: Optional["ServiceRegistry"] = None
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
        self._menu_items: List[MenuItem] = []
        self._module_map: Dict[ModuleType, MenuItem] = {}
        self._loader_cache: Dict[ModuleType, Any] = {}
        self._cache_lock = threading.RLock()
        
        self._register_modules()
        self._ai_detector = AIDashboardDetector()
        
        logger.info(f"📦 Service Registry initialized with {len(self._menu_items)} modules")
    
    def _register_modules(self):
        modules = [
            MenuItem(
                id=1,
                name="National Dashboard",
                aliases=["national", "national kpi", "kpi", "pakistan", "overall"],
                module_type=ModuleType.NATIONAL,
                file="national_kpi_service.py",
                loader=self._load_national_service
            ),
            MenuItem(
                id=2,
                name="DN Intelligence Center",
                aliases=["dn", "dn dashboard", "dn intelligence", "delivery", "delivery note", "pending dn"],
                module_type=ModuleType.DN,
                file="dn_analysis.py",
                loader=self._load_dn_service
            ),
            MenuItem(
                id=3,
                name="Dealer Dashboard",
                aliases=["dealer", "dealer dashboard", "dealer analytics", "distributor"],
                module_type=ModuleType.DEALER,
                file="dealer_analytics_service.py",
                loader=self._load_dealer_service
            ),
            MenuItem(
                id=4,
                name="Warehouse Dashboard",
                aliases=["warehouse", "warehouse dashboard", "warehouse analytics", "warehouse report"],
                module_type=ModuleType.WAREHOUSE,
                file="warehouse_service.py",
                loader=self._load_warehouse_service
            ),
            MenuItem(
                id=5,
                name="Product Dashboard",
                aliases=["product", "product dashboard", "material", "sku", "model"],
                module_type=ModuleType.PRODUCT,
                file="product_service.py",
                loader=self._load_product_service
            ),
            MenuItem(
                id=6,
                name="City Dashboard",
                aliases=["city", "city dashboard", "location", "region"],
                module_type=ModuleType.CITY,
                file="city_service.py",
                loader=self._load_city_service
            ),
            MenuItem(
                id=7,
                name="AI Assistant",
                aliases=["ai", "assistant", "general ai", "chat", "help"],
                module_type=ModuleType.AI,
                file="groq_service.py",
                loader=self._load_ai_service
            ),
        ]
        
        for item in modules:
            self._menu_items.append(item)
            self._module_map[item.module_type] = item
    
    # ============================================================
    # LOADER METHODS
    # ============================================================
    
    def _safe_import(self, module_name: str, function_name: str) -> Optional[Any]:
        try:
            module = __import__(module_name, fromlist=[function_name])
            return getattr(module, function_name, None)
        except ImportError as e:
            logger.warning(f"⚠️ Could not import {module_name}: {e}")
            return None
        except Exception as e:
            logger.warning(f"⚠️ Error importing {module_name}: {e}")
            return None
    
    def _load_national_service(self):
        with self._cache_lock:
            if ModuleType.NATIONAL not in self._loader_cache:
                loader = self._safe_import("app.services.national_kpi_service", "get_national_kpi_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.NATIONAL] = loader()
                        logger.info("✅ National KPI service loaded")
                    except Exception as e:
                        logger.error(f"❌ National KPI service init failed: {e}")
                        self._loader_cache[ModuleType.NATIONAL] = None
                else:
                    self._loader_cache[ModuleType.NATIONAL] = None
            return self._loader_cache[ModuleType.NATIONAL]
    
    def _load_dn_service(self):
        with self._cache_lock:
            if ModuleType.DN not in self._loader_cache:
                loader = self._safe_import("app.services.dn_analysis", "get_dn_analysis_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.DN] = loader()
                        logger.info("✅ DN service loaded")
                    except Exception as e:
                        logger.error(f"❌ DN service init failed: {e}")
                        self._loader_cache[ModuleType.DN] = None
                else:
                    self._loader_cache[ModuleType.DN] = None
            return self._loader_cache[ModuleType.DN]
    
    def _load_dealer_service(self):
        with self._cache_lock:
            if ModuleType.DEALER not in self._loader_cache:
                loader = self._safe_import("app.services.dealer_analytics_service", "get_dealer_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.DEALER] = loader()
                        logger.info("✅ Dealer service loaded")
                    except Exception as e:
                        logger.error(f"❌ Dealer service init failed: {e}")
                        self._loader_cache[ModuleType.DEALER] = None
                else:
                    self._loader_cache[ModuleType.DEALER] = None
            return self._loader_cache[ModuleType.DEALER]
    
    def _load_warehouse_service(self):
        with self._cache_lock:
            if ModuleType.WAREHOUSE not in self._loader_cache:
                loader = self._safe_import("app.services.warehouse_service", "get_warehouse_analytics_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.WAREHOUSE] = loader()
                        logger.info("✅ Warehouse service loaded")
                    except Exception as e:
                        logger.error(f"❌ Warehouse service init failed: {e}")
                        self._loader_cache[ModuleType.WAREHOUSE] = None
                else:
                    self._loader_cache[ModuleType.WAREHOUSE] = None
            return self._loader_cache[ModuleType.WAREHOUSE]
    
    def _load_product_service(self):
        with self._cache_lock:
            if ModuleType.PRODUCT not in self._loader_cache:
                loader = self._safe_import("app.services.product_service", "get_product_analytics_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.PRODUCT] = loader()
                        logger.info("✅ Product service loaded")
                    except Exception as e:
                        logger.error(f"❌ Product service init failed: {e}")
                        self._loader_cache[ModuleType.PRODUCT] = None
                else:
                    self._loader_cache[ModuleType.PRODUCT] = None
            return self._loader_cache[ModuleType.PRODUCT]
    
    def _load_city_service(self):
        with self._cache_lock:
            if ModuleType.CITY not in self._loader_cache:
                loader = self._safe_import("app.services.city_service", "get_city_analytics_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.CITY] = loader()
                        logger.info("✅ City service loaded")
                    except Exception as e:
                        logger.error(f"❌ City service init failed: {e}")
                        self._loader_cache[ModuleType.CITY] = None
                else:
                    self._loader_cache[ModuleType.CITY] = None
            return self._loader_cache[ModuleType.CITY]
    
    def _load_ai_service(self):
        with self._cache_lock:
            if ModuleType.AI not in self._loader_cache:
                loader = self._safe_import("app.services.groq_service", "get_groq_service")
                if loader:
                    try:
                        self._loader_cache[ModuleType.AI] = loader()
                        logger.info("✅ AI Assistant service loaded")
                    except Exception as e:
                        logger.error(f"❌ AI Assistant service init failed: {e}")
                        self._loader_cache[ModuleType.AI] = None
                else:
                    self._loader_cache[ModuleType.AI] = None
            return self._loader_cache[ModuleType.AI]
    
    # ============================================================
    # PUBLIC METHODS
    # ============================================================
    
    def get_menu_items(self) -> List[MenuItem]:
        return self._menu_items
    
    def get_menu_item_by_type(self, module_type: ModuleType) -> Optional[MenuItem]:
        return self._module_map.get(module_type)
    
    def detect_menu_item(self, text: str) -> Optional[MenuItem]:
        text_clean = text.strip()
        
        # Try exact matches first
        for item in self._menu_items:
            if item.matches(text_clean):
                return item
        
        # Try AI detection
        if USE_AI_DETECTION:
            result = self._ai_detector.detect_dashboard(text_clean, self._menu_items)
            if result:
                item, confidence = result
                if confidence >= AI_CONFIDENCE_THRESHOLD:
                    logger.info(f"✅ AI detection confirmed: {item.name} ({confidence:.2f})")
                    return item
        
        return None
    
    def get_service(self, module_type: ModuleType) -> Optional[Any]:
        item = self._module_map.get(module_type)
        if not item:
            return None
        try:
            return item.loader()
        except Exception as e:
            logger.error(f"❌ Service load failed for {module_type.value}: {e}")
            return None
    
    def get_service_by_text(self, text: str) -> Optional[tuple[MenuItem, Any]]:
        try:
            logger.info(f"🔍 Detecting service for: '{text}'")
            item = self.detect_menu_item(text)
            if not item:
                logger.info(f"❌ No menu item found for '{text}'")
                return None
            
            logger.info(f"✅ Found menu item: {item.name} (ID: {item.id})")
            service = self.get_service(item.module_type)
            if not service:
                logger.warning(f"⚠️ Service not available for {item.name}")
                return None
            
            return (item, service)
        except Exception as e:
            logger.error(f"❌ get_service_by_text error: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def get_ai_detector_health(self) -> Dict[str, Any]:
        """Get AI detector health."""
        return self._ai_detector.health_check()

# ============================================================
# BLOCK 7: MAIN GATEWAY SERVICE
# ============================================================

class AIProviderService:
    _instance: Optional["AIProviderService"] = None
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
        
        self._sessions: Dict[str, Session] = {}
        self._session_lock = threading.RLock()
        self._registry = ServiceRegistry()
        
        logger.info("=" * 70)
        logger.info("🚀 ENTERPRISE GATEWAY v54.0 initialized")
        logger.info("   AI Dashboard Detection: ✅")
        logger.info("   Session Locking: ✅")
        logger.info("   Routes to EXACTLY 7 modules")
        logger.info("   🚫 NO business logic")
        logger.info("   🚫 NO SQL queries")
        logger.info("   🚫 NO analytics")
        logger.info("   🚫 NO answering questions directly")
        logger.info("=" * 70)
        
        for item in self._registry.get_menu_items():
            logger.info(f"   {item.id}. {item.name} → {item.file}")
    
    # ============================================================
    # SESSION MANAGEMENT
    # ============================================================
    
    def _get_session(self, sender: str) -> Session:
        with self._session_lock:
            if sender not in self._sessions:
                self._sessions[sender] = Session(sender=sender)
                logger.info(f"🆕 New session created for {sender}")
                return self._sessions[sender]
            
            session = self._sessions[sender]
            if session.is_expired():
                logger.info(f"⏰ Session expired for {sender}, creating new")
                del self._sessions[sender]
                session = Session(sender=sender)
                self._sessions[sender] = session
            
            return session
    
    def _lock_session(self, sender: str, menu_item: MenuItem, service_instance: Any) -> bool:
        with self._session_lock:
            session = self._get_session(sender)
            session.lock(
                module_type=menu_item.module_type,
                module_name=menu_item.name,
                file_name=menu_item.file,
                menu_id=menu_item.id,
                service_instance=service_instance
            )
            return True
    
    def _unlock_session(self, sender: str) -> bool:
        with self._session_lock:
            if sender not in self._sessions:
                return False
            session = self._sessions[sender]
            session.unlock()
            return True
    
    def _is_locked(self, sender: str) -> bool:
        with self._session_lock:
            if sender not in self._sessions:
                return False
            return self._sessions[sender].locked
    
    # ============================================================
    # ROUTING - ONLY ROUTING, NO BUSINESS LOGIC
    # ============================================================
    
    def _detect_dashboard(self, message: str) -> Optional[tuple[MenuItem, Any]]:
        try:
            return self._registry.get_service_by_text(message)
        except Exception as e:
            logger.error(f"❌ Dashboard detection error: {e}")
            logger.error(traceback.format_exc())
            return None
    
    def _forward_to_module(self, session: Session, message: str, sender: str) -> str:
        if not session.service_instance:
            logger.error(f"❌ No service instance for {session.module_name}")
            self._unlock_session(sender)
            return self._get_main_dashboard()
        
        service = session.service_instance
        
        if not hasattr(service, "process_whatsapp_query"):
            logger.error(f"❌ Service {session.module_name} missing process_whatsapp_query")
            self._unlock_session(sender)
            return "⚠️ Service is misconfigured.\n\n" + self._get_main_dashboard()
        
        try:
            logger.info(f"📤 Forwarding to {session.module_name}: '{message}'")
            result = service.process_whatsapp_query(message, sender)
            
            if result == EXIT_SIGNAL or result == "99":
                logger.info(f"🚪 Module {session.module_name} requested exit ({result})")
                self._unlock_session(sender)
                return self._get_main_dashboard()
            
            session.update_activity()
            session.add_history(message, result)
            return result
            
        except Exception as e:
            logger.error(f"❌ Module {session.module_name} error: {e}")
            logger.error(traceback.format_exc())
            self._unlock_session(sender)
            return f"⚠️ Service error: {str(e)[:200]}\n\n" + self._get_main_dashboard()
    
    # ============================================================
    # MAIN PROCESSING - GATEWAY ONLY
    # ============================================================
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        try:
            logger.info(f"📨 Gateway received: '{message}' from {sender}")
            
            if not message or not message.strip():
                return self._get_main_dashboard()
            
            message_clean = message.strip()
            session = self._get_session(sender)
            
            # STEP 1: CHECK IF SESSION IS LOCKED
            if session.locked:
                logger.info(f"🔒 Session LOCKED for {sender} → {session.module_name}")
                
                if message_clean == "99":
                    logger.info(f"🚪 Manual exit (99) requested by {sender}")
                    self._unlock_session(sender)
                    return self._get_main_dashboard()
                
                return self._forward_to_module(session, message_clean, sender)
            
            # STEP 2: SESSION IDLE - CHECK COMMANDS
            logger.info(f"🔄 Session IDLE for {sender}")
            
            if message_clean.lower() in ["menu", "help", "options", "dashboard", "main", "0"]:
                return self._get_main_dashboard()
            
            # STEP 3: DETECT DASHBOARD
            detected = self._detect_dashboard(message_clean)
            
            if detected:
                menu_item, service = detected
                logger.info(f"🎯 Detected: {menu_item.name} (ID: {menu_item.id})")
                
                self._lock_session(sender, menu_item, service)
                
                try:
                    result = service.process_whatsapp_query(message_clean, sender)
                    
                    if result == EXIT_SIGNAL or result == "99":
                        logger.info(f"🚪 Immediate exit from {menu_item.name}")
                        self._unlock_session(sender)
                        return self._get_main_dashboard()
                    
                    session = self._get_session(sender)
                    session.update_activity()
                    session.add_history(message_clean, result)
                    return result
                    
                except Exception as e:
                    logger.error(f"❌ Module {menu_item.name} error: {e}")
                    logger.error(traceback.format_exc())
                    self._unlock_session(sender)
                    return f"⚠️ {menu_item.name} error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
            
            # STEP 4: NO DASHBOARD DETECTED
            logger.info(f"❌ No dashboard detected for: '{message_clean}'")
            return self._get_out_of_box_response()
            
        except Exception as e:
            logger.error(f"❌ Gateway error: {e}")
            logger.error(traceback.format_exc())
            return f"⚠️ System error: {str(e)[:200]}\n\n{self._get_main_dashboard()}"
    
    # ============================================================
    # RESPONSES - ONLY THE GATEWAY SHOWS THESE
    # ============================================================
    
    def _get_main_dashboard(self) -> str:
        lines = ["🏠 *HPK Logistics AI*", ""]
        
        for item in self._registry.get_menu_items():
            lines.append(f"{item.id}️⃣ {item.name}")
        
        lines.extend([
            "",
            "📌 *Commands:*",
            "• Type a number (1-7) to enter a dashboard",
            "• Type dashboard name (e.g., 'Warehouse Dashboard')",
            "• Type '99' to exit current dashboard",
            "• Type 'menu' or 'help' for this menu",
            "",
            "Reply with a number or dashboard name:"
        ])
        
        return "\n".join(lines)
    
    def _get_out_of_box_response(self) -> str:
        return "\n".join([
            "❌ *Please select a valid option from the menu.*",
            "",
            "You can enter a dashboard by:",
            "• Number (1-7)",
            "• Dashboard name (e.g., 'Warehouse Dashboard')",
            "",
            self._get_main_dashboard()
        ])
    
    # ============================================================
    # HEALTH CHECK
    # ============================================================
    
    def health_check(self) -> Dict[str, Any]:
        with self._session_lock:
            active_sessions = len(self._sessions)
            locked_sessions = sum(1 for s in self._sessions.values() if s.locked)
            session_details = {
                sender: session.to_dict()
                for sender, session in self._sessions.items()
            }
        
        return {
            "service": "ai_provider_service",
            "version": "54.0",
            "type": "enterprise_gateway",
            "status": "healthy",
            "active_sessions": active_sessions,
            "locked_sessions": locked_sessions,
            "session_details": session_details,
            "available_modules": [
                {
                    "id": item.id,
                    "name": item.name,
                    "file": item.file,
                    "aliases": item.aliases
                }
                for item in self._registry.get_menu_items()
            ],
            "ai_detector": self._registry.get_ai_detector_health(),
            "features": {
                "session_locking": True,
                "module_routing": True,
                "exit_signal": EXIT_SIGNAL,
                "main_dashboard": True,
                "alias_detection": True,
                "ai_dashboard_detection": USE_AI_DETECTION,
            }
        }


# ============================================================
# BLOCK 8: SINGLETON
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


def process_whatsapp_query(message: str, sender: str = "default") -> str:
    try:
        service = get_ai_provider_service()
        return service.process_whatsapp_query(message, sender)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return "⚠️ Service is temporarily unavailable. Please try again later."


# ============================================================
# BLOCK 9: EXPORTS
# ============================================================

__all__ = [
    "AIProviderService",
    "ModuleType",
    "Session",
    "MenuItem",
    "ServiceRegistry",
    "get_ai_provider_service",
    "process_whatsapp_query",
    "EXIT_SIGNAL",
]
