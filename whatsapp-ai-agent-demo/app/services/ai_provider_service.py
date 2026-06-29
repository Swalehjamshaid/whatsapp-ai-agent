"""
File: app/services/ai_provider_service_intents.py
Version: 3.0 - ENTERPRISE INTENT DETECTION ENGINE (SIMPLIFIED)
Purpose: PURE intent detection - NO business logic, NO SQL, NO formatting
         Uses: spaCy, SentenceTransformer, RapidFuzz, FlashRank, Semantic Router
         Enterprise features: Text Normalization, Entity Extraction, Dependency Injection
"""

import logging
import re
import time
import hashlib
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# LIBRARY IMPORTS WITH FALLBACK
# ============================================================

# spaCy - Entity Extraction
try:
    import spacy
    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False
    spacy = None

# SentenceTransformer - Semantic Detection
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    SentenceTransformer = None

# RapidFuzz - Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None
    process = None

# FlashRank - Re-ranking
try:
    from flashrank import Ranker
    FLASHRANK_AVAILABLE = True
except ImportError:
    FLASHRANK_AVAILABLE = False
    Ranker = None

# Cachetools - Caching
try:
    from cachetools import TTLCache, cached
    CACHETOOLS_AVAILABLE = True
except ImportError:
    CACHETOOLS_AVAILABLE = False
    TTLCache = None

# Semantic Router
try:
    from semantic_router import Route, Router, SemanticRouter
    SEMANTIC_ROUTER_AVAILABLE = True
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False
    Route = None
    Router = None
    SemanticRouter = None

# NumPy - Numerical operations
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None

# ============================================================
# ENUMS & CONSTANTS
# ============================================================

class EntityType(Enum):
    """Entity types for extraction"""
    DEALER = "dealer"
    CITY = "city"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    DN = "dn"
    UNKNOWN = "unknown"

class IntentType(Enum):
    """Intent types"""
    DN_LOOKUP = "dn_lookup"
    PENDING_DN = "pending_dn"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_COMPARISON = "comparison"
    TOP_DEALERS = "top_dealers"
    BOTTOM_DEALERS = "bottom_dealers"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    CITY_DASHBOARD = "city_dashboard"
    PRODUCT_DASHBOARD = "product_dashboard"
    NATIONAL_KPI = "national_kpi"
    HELP = "help"
    GREETING = "greeting"
    CONVERSATIONAL = "conversational"
    GENERAL_AI = "general_ai"

class ServiceKey(Enum):
    """Service keys for routing"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    NATIONAL_KPI = "national_kpi"
    GROQ = "groq"

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class NormalizedText:
    """Normalized text result"""
    original: str
    cleaned: str
    normalized: str
    tokens: List[str]
    entity_candidates: Dict[str, List[str]]

@dataclass
class ExtractedEntity:
    """Extracted entity result"""
    type: EntityType
    text: str
    normalized: str
    confidence: float
    position: int

@dataclass
class IntentCandidate:
    """Candidate intent from a detection strategy"""
    intent: str
    service_key: str
    method: str
    entity: Optional[str] = None
    entity2: Optional[str] = None
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    strategy: str = ""
    extracted_entities: List[ExtractedEntity] = field(default_factory=list)

@dataclass
class RoutingDecision:
    """
    PURE ROUTING DECISION - NO business logic
    
    Contains ONLY routing information.
    """
    intent: str
    service_key: str
    method: str
    entity: Optional[str] = None
    entity2: Optional[str] = None
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    suggestions: List[str] = field(default_factory=list)
    
    # Detection metadata
    detection_method: str = "unknown"
    combined_score: float = 0.0
    pattern_score: float = 0.0
    semantic_score: float = 0.0
    fuzzy_score: float = 0.0
    flashrank_score: float = 0.0
    entity_confidence: float = 0.0
    
    # Entities
    extracted_entities: List[ExtractedEntity] = field(default_factory=list)
    primary_entity: Optional[ExtractedEntity] = None
    
    # Validation
    validated: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message[:100],
            "suggestions": self.suggestions,
            "detection_method": self.detection_method,
            "combined_score": self.combined_score,
            "validated": self.validated
        }

# ============================================================
# ROUTING TABLE (PURE ROUTING - NO BUSINESS LOGIC)
# ============================================================

class RoutingTable:
    """
    PURE ROUTING TABLE - Maps intents to services and methods.
    NO business logic, NO SQL, NO formatting.
    """
    
    # Intent to route mapping
    INTENT_ROUTES = {
        IntentType.DN_LOOKUP.value: {"service": ServiceKey.DN.value, "method": "get_dn_dashboard"},
        IntentType.PENDING_DN.value: {"service": ServiceKey.DN.value, "method": "get_pending_dns"},
        IntentType.PENDING_PGI.value: {"service": ServiceKey.DN.value, "method": "get_pending_pgi"},
        IntentType.PENDING_POD.value: {"service": ServiceKey.DN.value, "method": "get_pending_pod"},
        IntentType.DEALER_DASHBOARD.value: {"service": ServiceKey.DEALER.value, "method": "get_dealer_dashboard"},
        IntentType.DEALER_COMPARISON.value: {"service": ServiceKey.DEALER.value, "method": "compare_dealers"},
        IntentType.TOP_DEALERS.value: {"service": ServiceKey.DEALER.value, "method": "get_top_dealers"},
        IntentType.BOTTOM_DEALERS.value: {"service": ServiceKey.DEALER.value, "method": "get_bottom_dealers"},
        IntentType.WAREHOUSE_DASHBOARD.value: {"service": ServiceKey.WAREHOUSE.value, "method": "get_warehouse_dashboard"},
        IntentType.CITY_DASHBOARD.value: {"service": ServiceKey.CITY.value, "method": "get_city_dashboard"},
        IntentType.PRODUCT_DASHBOARD.value: {"service": ServiceKey.PRODUCT.value, "method": "get_product_dashboard"},
        IntentType.NATIONAL_KPI.value: {"service": ServiceKey.NATIONAL_KPI.value, "method": "get_national_kpi_dashboard"},
        IntentType.HELP.value: {"service": ServiceKey.GROQ.value, "method": "process_query"},
        IntentType.GREETING.value: {"service": ServiceKey.GROQ.value, "method": "process_query"},
        IntentType.CONVERSATIONAL.value: {"service": ServiceKey.GROQ.value, "method": "process_query"},
        IntentType.GENERAL_AI.value: {"service": ServiceKey.GROQ.value, "method": "process_query"},
    }
    
    # Confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        IntentType.DN_LOOKUP.value: 0.90,
        IntentType.PENDING_DN.value: 0.85,
        IntentType.PENDING_PGI.value: 0.85,
        IntentType.PENDING_POD.value: 0.85,
        IntentType.DEALER_DASHBOARD.value: 0.80,
        IntentType.TOP_DEALERS.value: 0.80,
        IntentType.BOTTOM_DEALERS.value: 0.80,
        IntentType.DEALER_COMPARISON.value: 0.80,
        IntentType.WAREHOUSE_DASHBOARD.value: 0.75,
        IntentType.CITY_DASHBOARD.value: 0.75,
        IntentType.PRODUCT_DASHBOARD.value: 0.75,
        IntentType.NATIONAL_KPI.value: 0.85,
        IntentType.HELP.value: 0.70,
        IntentType.GREETING.value: 0.70,
        IntentType.CONVERSATIONAL.value: 0.65,
        IntentType.GENERAL_AI.value: 0.30,
    }
    
    @classmethod
    def get_route(cls, intent: str) -> Optional[Dict[str, str]]:
        """Get route for intent"""
        return cls.INTENT_ROUTES.get(intent)
    
    @classmethod
    def get_threshold(cls, intent: str) -> float:
        """Get confidence threshold for intent"""
        return cls.CONFIDENCE_THRESHOLDS.get(intent, 0.50)
    
    @classmethod
    def validate_route(cls, intent: str, service_key: str, method: str) -> bool:
        """Validate that route exists and matches"""
        route = cls.get_route(intent)
        if not route:
            return False
        return route["service"] == service_key and route["method"] == method

# ============================================================
# TEXT NORMALIZER
# ============================================================

class TextNormalizer:
    """
    Text Normalization - Pure text processing.
    NO business logic, NO SQL, NO formatting.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        # Patterns for normalization
        self.space_pattern = re.compile(r'\s+')
        self.punctuation_pattern = re.compile(r'[^\w\s-]')
        self.dealer_indicators = re.compile(
            r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|'
            r'dashboard|profile|summary|overview|info|information|details|status|'
            r'statistics|performance|the|a|an)\b',
            re.IGNORECASE
        )
        self.logger.info("✅ TextNormalizer initialized")
    
    def normalize(self, text: str) -> NormalizedText:
        """
        Normalize text for processing
        
        Steps:
        1. Convert to lowercase
        2. Remove extra spaces
        3. Remove unwanted punctuation
        4. Normalize entity names
        5. Tokenize
        """
        if not text:
            return NormalizedText(
                original="",
                cleaned="",
                normalized="",
                tokens=[],
                entity_candidates={}
            )
        
        # Step 1: Lowercase
        lower_text = text.lower()
        
        # Step 2: Remove extra spaces
        cleaned = self.space_pattern.sub(' ', lower_text).strip()
        
        # Step 3: Remove unwanted punctuation (keep hyphens for entity names)
        cleaned = self.punctuation_pattern.sub(' ', cleaned)
        cleaned = self.space_pattern.sub(' ', cleaned).strip()
        
        # Step 4: Tokenize
        tokens = cleaned.split()
        
        # Step 5: Extract entity candidates
        entity_candidates = self._extract_entity_candidates(text, cleaned)
        
        return NormalizedText(
            original=text,
            cleaned=cleaned,
            normalized=cleaned,
            tokens=tokens,
            entity_candidates=entity_candidates
        )
    
    def _extract_entity_candidates(self, original: str, cleaned: str) -> Dict[str, List[str]]:
        """Extract potential entity candidates from text"""
        candidates = {}
        
        # Remove common indicators for cleaner entity extraction
        entity_text = self.dealer_indicators.sub('', cleaned)
        entity_text = self.space_pattern.sub(' ', entity_text).strip()
        
        if entity_text and len(entity_text) > 1:
            candidates["potential_entity"] = [entity_text]
        
        # Look for phrases after "dealer", "warehouse", "city", "product"
        patterns = {
            "dealer": re.compile(r'dealer\s+([a-z0-9\s\-\.]+)', re.IGNORECASE),
            "warehouse": re.compile(r'warehouse\s+([a-z0-9\s\-\.]+)', re.IGNORECASE),
            "city": re.compile(r'city\s+([a-z0-9\s\-\.]+)', re.IGNORECASE),
            "product": re.compile(r'product\s+([a-z0-9\s\-\.]+)', re.IGNORECASE),
        }
        
        for entity_type, pattern in patterns.items():
            match = pattern.search(original)
            if match:
                value = match.group(1).strip()
                if value and len(value) > 1:
                    if entity_type not in candidates:
                        candidates[entity_type] = []
                    candidates[entity_type].append(value)
        
        # Look for short queries (2-4 words) that might be entity names
        if len(cleaned.split()) <= 4 and len(cleaned) > 2:
            if "dealer" not in cleaned and "warehouse" not in cleaned and "city" not in cleaned and "product" not in cleaned:
                if not re.match(r'^\d+$', cleaned):
                    candidates["entity_name"] = [cleaned]
        
        return candidates

# ============================================================
# ENTITY EXTRACTOR (PURE ENTITY EXTRACTION)
# ============================================================

class EntityExtractor:
    """
    Pure Entity Extraction using spaCy.
    NO business logic, NO SQL, NO formatting.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.nlp = None
        
        if SPACY_AVAILABLE:
            try:
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                except OSError:
                    try:
                        self.nlp = spacy.load("en_core_web_lg")
                    except OSError:
                        self.logger.warning("⚠️ spaCy model not found. Download: python -m spacy download en_core_web_sm")
            except Exception as e:
                self.logger.warning(f"⚠️ spaCy initialization failed: {e}")
        
        self.logger.info(f"✅ EntityExtractor initialized (spaCy: {'✅' if self.nlp else '❌'})")
    
    def extract_entities(self, text: str) -> List[ExtractedEntity]:
        """
        Extract entities from text using spaCy.
        ONLY entity extraction - NO business logic.
        """
        if not self.nlp:
            return []
        
        try:
            doc = self.nlp(text)
            entities = []
            
            # Define entity type mapping
            entity_map = {
                "ORG": EntityType.DEALER,
                "GPE": EntityType.CITY,
                "LOC": EntityType.WAREHOUSE,
                "PRODUCT": EntityType.PRODUCT,
                "FAC": EntityType.WAREHOUSE,
            }
            
            for ent in doc.ents:
                if ent.label_ in entity_map:
                    entity_type = entity_map[ent.label_]
                    # Determine confidence based on entity type
                    confidence = self._calculate_entity_confidence(ent)
                    
                    extracted = ExtractedEntity(
                        type=entity_type,
                        text=ent.text,
                        normalized=ent.text.lower().strip(),
                        confidence=confidence,
                        position=ent.start_char
                    )
                    entities.append(extracted)
            
            # Sort by position (earliest first)
            entities.sort(key=lambda x: x.position)
            
            return entities
            
        except Exception as e:
            self.logger.warning(f"spaCy entity extraction failed: {e}")
            return []
    
    def _calculate_entity_confidence(self, ent) -> float:
        """Calculate confidence for extracted entity"""
        # Higher confidence for longer entities
        length_factor = min(1.0, len(ent.text) / 20)
        
        # Higher confidence for certain entity types
        type_confidence = {
            "ORG": 0.85,
            "GPE": 0.80,
            "LOC": 0.80,
            "PRODUCT": 0.75,
            "FAC": 0.75,
        }
        
        base = type_confidence.get(ent.label_, 0.70)
        return min(1.0, base + (length_factor * 0.10))

# ============================================================
# ENTITY MATCHER (RAPIDFUZZ)
# ============================================================

class EntityMatcher:
    """
    Pure Entity Matching using RapidFuzz.
    NO business logic, NO SQL, NO formatting.
    
    This resolves entities by matching against known patterns.
    """
    
    # Known entity patterns (business data kept separate)
    # These would normally come from a database or configuration
    DEALER_PATTERNS = [
        "sham electronics", "ruba digital wah", "taj electronics",
        "haroon electronics", "mian group chakwal", "arco electronics",
        "shah electronics"
    ]
    
    CITY_PATTERNS = [
        "lahore", "karachi", "islamabad", "rawalpindi", "faisalabad",
        "multan", "peshawar", "quetta", "gujranwala"
    ]
    
    WAREHOUSE_PATTERNS = [
        "main warehouse", "north warehouse", "south warehouse",
        "central warehouse", "east warehouse", "west warehouse"
    ]
    
    PRODUCT_PATTERNS = [
        "ac-123", "tv-456", "refrigerator", "washing machine",
        "microwave", "oven", "dishwasher"
    ]
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.info("✅ EntityMatcher initialized")
        
        # Pre-compile patterns for matching
        self._normalize_patterns()
    
    def _normalize_patterns(self):
        """Normalize patterns for matching"""
        self._dealer_norm = [p.lower().strip() for p in self.DEALER_PATTERNS]
        self._city_norm = [p.lower().strip() for p in self.CITY_PATTERNS]
        self._warehouse_norm = [p.lower().strip() for p in self.WAREHOUSE_PATTERNS]
        self._product_norm = [p.lower().strip() for p in self.PRODUCT_PATTERNS]
    
    def match_entity(self, text: str, entity_type: EntityType) -> Optional[ExtractedEntity]:
        """
        Match entity against known patterns using RapidFuzz.
        """
        if not RAPIDFUZZ_AVAILABLE or not text:
            return None
        
        text_lower = text.lower().strip()
        patterns = []
        pattern_type = entity_type
        
        if entity_type == EntityType.DEALER:
            patterns = self._dealer_norm
        elif entity_type == EntityType.CITY:
            patterns = self._city_norm
        elif entity_type == EntityType.WAREHOUSE:
            patterns = self._warehouse_norm
        elif entity_type == EntityType.PRODUCT:
            patterns = self._product_norm
        else:
            return None
        
        if not patterns:
            return None
        
        try:
            # Try exact match first
            for pattern in patterns:
                if text_lower == pattern:
                    return ExtractedEntity(
                        type=entity_type,
                        text=pattern,
                        normalized=pattern,
                        confidence=1.0,
                        position=0
                    )
            
            # Try contains match
            for pattern in patterns:
                if text_lower in pattern or pattern in text_lower:
                    return ExtractedEntity(
                        type=entity_type,
                        text=pattern,
                        normalized=pattern,
                        confidence=0.85,
                        position=0
                    )
            
            # Try fuzzy match
            results = process.extract(
                text_lower,
                patterns,
                scorer=fuzz.ratio,
                limit=1
            )
            
            if results and results[0][1] >= 70:
                best_match, score, _ = results[0]
                return ExtractedEntity(
                    type=entity_type,
                    text=best_match,
                    normalized=best_match,
                    confidence=score / 100.0,
                    position=0
                )
            
            return None
            
        except Exception as e:
            self.logger.warning(f"Entity matching failed: {e}")
            return None

# ============================================================
# SEMANTIC ROUTER
# ============================================================

class SemanticRouter:
    """
    Semantic Router for intent detection.
    Uses sentence-transformers for semantic understanding.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.model = None
        self.intent_embeddings = {}
        self._init_model()
    
    def _init_model(self):
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            return
        
        try:
            self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
            
            # Intent examples for semantic routing
            intent_examples = {
                IntentType.DN_LOOKUP.value: [
                    "check delivery note number", "track DN", "delivery note details",
                    "DN lookup", "DN number", "delivery note"
                ],
                IntentType.PENDING_DN.value: [
                    "pending deliveries", "open delivery notes", "outstanding DNs",
                    "deliveries not completed", "pending DN"
                ],
                IntentType.PENDING_PGI.value: [
                    "goods issue pending", "PGI not done", "pending goods issuance"
                ],
                IntentType.PENDING_POD.value: [
                    "proof of delivery pending", "POD not received", "POD status"
                ],
                IntentType.DEALER_DASHBOARD.value: [
                    "dealer performance", "dealer revenue", "dealer dashboard",
                    "show dealer metrics", "dealer analytics", "dealer profile"
                ],
                IntentType.TOP_DEALERS.value: [
                    "best performing dealers", "top revenue dealers", "dealer ranking",
                    "highest performing dealers", "top dealers"
                ],
                IntentType.BOTTOM_DEALERS.value: [
                    "lowest performing dealers", "bottom dealers", "worst dealers"
                ],
                IntentType.WAREHOUSE_DASHBOARD.value: [
                    "warehouse performance", "depot metrics", "warehouse dashboard"
                ],
                IntentType.CITY_DASHBOARD.value: [
                    "city performance", "city revenue", "city dashboard"
                ],
                IntentType.PRODUCT_DASHBOARD.value: [
                    "product performance", "product revenue", "product dashboard"
                ],
                IntentType.NATIONAL_KPI.value: [
                    "national performance", "country-wide metrics", "executive dashboard",
                    "Pakistan performance", "company performance"
                ],
                IntentType.HELP.value: [
                    "show commands", "available commands", "help menu",
                    "what can you do", "how to use"
                ],
                IntentType.GREETING.value: [
                    "hello", "hi there", "good morning", "hey"
                ],
                IntentType.CONVERSATIONAL.value: [
                    "how are you", "what is your name", "tell me about yourself",
                    "thank you", "thanks"
                ]
            }
            
            import numpy as np
            for intent, examples in intent_examples.items():
                if examples:
                    embeddings = self.model.encode(examples, convert_to_numpy=True)
                    self.intent_embeddings[intent] = embeddings.mean(axis=0)
            
            self.logger.info(f"✅ Semantic Router initialized with {len(self.intent_embeddings)} intents")
            
        except Exception as e:
            self.logger.warning(f"⚠️ Semantic Router initialization failed: {e}")
    
    def route(self, text: str) -> Tuple[str, float]:
        """
        Route text to intent using semantic similarity.
        
        Returns:
            Tuple of (intent, confidence)
        """
        if not self.model or not self.intent_embeddings:
            return IntentType.GENERAL_AI.value, 0.0
        
        try:
            import numpy as np
            text_embedding = self.model.encode([text], convert_to_numpy=True)[0]
            
            similarities = {}
            for intent, embedding in self.intent_embeddings.items():
                text_norm = text_embedding / np.linalg.norm(text_embedding)
                intent_norm = embedding / np.linalg.norm(embedding)
                similarity = np.dot(text_norm, intent_norm)
                similarities[intent] = float(similarity)
            
            best_intent = max(similarities, key=similarities.get)
            best_score = similarities[best_intent]
            
            # Apply threshold
            if best_score < 0.3:
                return IntentType.GENERAL_AI.value, best_score
            
            return best_intent, best_score
            
        except Exception as e:
            self.logger.warning(f"Semantic routing failed: {e}")
            return IntentType.GENERAL_AI.value, 0.0

# ============================================================
# CONFIDENCE ENGINE
# ============================================================

class ConfidenceEngine:
    """
    Dynamic confidence scoring engine.
    Combines multiple scores into a single confidence value.
    """
    
    # Weights for different strategies
    STRATEGY_WEIGHTS = {
        "pattern": 0.35,
        "semantic": 0.25,
        "fuzzy": 0.15,
        "spacy": 0.10,
        "flashrank": 0.10,
        "entity": 0.05
    }
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.logger.info("✅ ConfidenceEngine initialized")
    
    def calculate_combined_score(
        self,
        pattern_score: float = 0.0,
        semantic_score: float = 0.0,
        fuzzy_score: float = 0.0,
        spacy_score: float = 0.0,
        flashrank_score: float = 0.0,
        entity_score: float = 0.0
    ) -> float:
        """
        Calculate combined confidence score with weights.
        """
        scores = {
            "pattern": pattern_score,
            "semantic": semantic_score,
            "fuzzy": fuzzy_score,
            "spacy": spacy_score,
            "flashrank": flashrank_score,
            "entity": entity_score
        }
        
        weighted_sum = 0.0
        total_weight = 0.0
        
        for strategy, score in scores.items():
            weight = self.STRATEGY_WEIGHTS.get(strategy, 0.10)
            weighted_sum += score * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        combined = weighted_sum / total_weight
        return min(combined, 1.0)

# ============================================================
# ENTERPRISE INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    """
    ENTERPRISE INTENT DETECTION ENGINE - v3.0 SIMPLIFIED
    
    Features:
    - Text Normalization
    - Entity Extraction (spaCy)
    - Entity Matching (RapidFuzz)
    - Semantic Routing (SentenceTransformer)
    - FlashRank Re-ranking
    - Confidence Engine
    - Caching
    
    NO business logic, NO SQL, NO formatting.
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.start_time = time.time()
        
        # ============================================================
        # 1. INITIALIZE COMPONENTS
        # ============================================================
        
        # Text Normalizer
        self.normalizer = TextNormalizer()
        self.logger.info("✅ TextNormalizer initialized")
        
        # Entity Extractor (spaCy)
        self.entity_extractor = EntityExtractor()
        self.logger.info("✅ EntityExtractor initialized")
        
        # Entity Matcher (RapidFuzz)
        self.entity_matcher = EntityMatcher()
        self.logger.info("✅ EntityMatcher initialized")
        
        # Semantic Router (SentenceTransformer)
        self.semantic_router = SemanticRouter()
        self.logger.info("✅ SemanticRouter initialized")
        
        # FlashRank Re-ranker
        self.ranker = None
        if FLASHRANK_AVAILABLE:
            try:
                self.ranker = Ranker(model="ms-marco-MiniLM-L-12-v2")
                self.logger.info("✅ FlashRank initialized")
            except Exception as e:
                self.logger.warning(f"⚠️ FlashRank init failed: {e}")
        
        # Confidence Engine
        self.confidence_engine = ConfidenceEngine()
        self.logger.info("✅ ConfidenceEngine initialized")
        
        # ============================================================
        # 2. SETUP CACHE
        # ============================================================
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # ============================================================
        # 3. PATTERNS
        # ============================================================
        self._init_patterns()
        
        # ============================================================
        # 4. LOG SUMMARY
        # ============================================================
        init_time = (time.time() - self.start_time) * 1000
        self.logger.info("=" * 60)
        self.logger.info(f"✅ IntentDetectionEngine v3.0 initialized in {init_time:.2f}ms")
        self.logger.info(f"   spaCy: {'✅' if SPACY_AVAILABLE else '❌'}")
        self.logger.info(f"   SentenceTransformer: {'✅' if SENTENCE_TRANSFORMERS_AVAILABLE else '❌'}")
        self.logger.info(f"   RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
        self.logger.info(f"   FlashRank: {'✅' if self.ranker else '❌'}")
        self.logger.info("=" * 60)
    
    def _init_patterns(self):
        """Initialize regex patterns for pattern-based detection"""
        # DN Pattern
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        
        # Pending Patterns
        self.PENDING_DN_PATTERN = re.compile(
            r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)',
            re.IGNORECASE
        )
        self.PENDING_PGI_PATTERN = re.compile(
            r'(?:pending|open)\s*(?:pgi|goods issue)',
            re.IGNORECASE
        )
        self.PENDING_POD_PATTERN = re.compile(
            r'(?:pending|open)\s*(?:pod|proof of delivery)',
            re.IGNORECASE
        )
        
        # Ranking Patterns
        self.TOP_DEALERS_PATTERN = re.compile(
            r'(?:top|best|highest)\s*(?:dealers?|performers?)',
            re.IGNORECASE
        )
        self.BOTTOM_DEALERS_PATTERN = re.compile(
            r'(?:bottom|lowest|worst)\s*(?:dealers?|performers?)',
            re.IGNORECASE
        )
        
        # Comparison Pattern
        self.COMPARISON_PATTERN = re.compile(
            r'(?:compare|vs|versus|difference between)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        
        # National KPI Pattern
        self.NATIONAL_KPI_PATTERN = re.compile(
            r'(?:national|pakistan|country|overall|executive)\s*(?:kpi|performance|dashboard)',
            re.IGNORECASE
        )
        
        # Help/Greeting Patterns
        self.HELP_PATTERN = re.compile(
            r'(?:help|menu|commands|what can you do)',
            re.IGNORECASE
        )
        self.GREETING_PATTERN = re.compile(
            r'^(?:hello|hi|hey|good morning|good evening|howdy|greetings)',
            re.IGNORECASE
        )
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent from message and return RoutingDecision.
        
        Steps:
        1. Normalize text
        2. Extract entities
        3. Match entities
        4. Detect intent (pattern + semantic)
        5. Re-rank candidates (FlashRank)
        6. Calculate confidence
        7. Return RoutingDecision
        """
        if not message or not message.strip():
            return self._create_fallback_decision(message, "Empty message")
        
        # Check cache
        cache_key = self._get_cache_key(message)
        if self._cache_enabled() and cache_key in self._cache:
            self._cache_hits += 1
            self.logger.debug(f"Cache hit for: {message[:50]}")
            return self._cache[cache_key]
        
        self._cache_misses += 1
        self.logger.info(f"🔍 Detecting intent for: {message[:100]}")
        
        # ============================================================
        # STEP 1: TEXT NORMALIZATION
        # ============================================================
        normalized = self.normalizer.normalize(message)
        self.logger.debug(f"Normalized: {normalized.cleaned}")
        
        # ============================================================
        # STEP 2: ENTITY EXTRACTION (spaCy)
        # ============================================================
        extracted_entities = self.entity_extractor.extract_entities(message)
        self.logger.debug(f"Extracted entities: {len(extracted_entities)}")
        
        # ============================================================
        # STEP 3: ENTITY MATCHING (RapidFuzz)
        # ============================================================
        matched_entities = []
        for entity in extracted_entities:
            matched = self.entity_matcher.match_entity(entity.text, entity.type)
            if matched and matched.confidence > 0.7:
                matched_entities.append(matched)
        
        # ============================================================
        # STEP 4: INTENT DETECTION (Pattern + Semantic)
        # ============================================================
        
        # 4a: Pattern-based detection
        pattern_intent, pattern_score, pattern_entity = self._detect_by_pattern(
            message, normalized, matched_entities
        )
        
        # 4b: Semantic detection
        semantic_intent, semantic_score = self.semantic_router.route(normalized.cleaned)
        
        # ============================================================
        # STEP 5: CANDIDATE SELECTION
        # ============================================================
        
        candidates = []
        
        # Pattern candidate
        if pattern_intent and pattern_score > 0.5:
            route = RoutingTable.get_route(pattern_intent)
            if route:
                candidates.append(IntentCandidate(
                    intent=pattern_intent,
                    service_key=route["service"],
                    method=route["method"],
                    entity=pattern_entity,
                    confidence=pattern_score,
                    needs_groq=pattern_intent in [IntentType.HELP.value, IntentType.GREETING.value, IntentType.CONVERSATIONAL.value, IntentType.GENERAL_AI.value],
                    reason=f"Pattern match: {pattern_intent}",
                    strategy="pattern"
                ))
        
        # Semantic candidate
        if semantic_intent and semantic_score > 0.3:
            route = RoutingTable.get_route(semantic_intent)
            if route:
                candidates.append(IntentCandidate(
                    intent=semantic_intent,
                    service_key=route["service"],
                    method=route["method"],
                    confidence=semantic_score,
                    needs_groq=semantic_intent in [IntentType.HELP.value, IntentType.GREETING.value, IntentType.CONVERSATIONAL.value, IntentType.GENERAL_AI.value],
                    reason=f"Semantic match: {semantic_intent}",
                    strategy="semantic"
                ))
        
        # Entity-based candidate
        if matched_entities:
            primary_entity = matched_entities[0]
            if primary_entity.type == EntityType.DEALER:
                route = RoutingTable.get_route(IntentType.DEALER_DASHBOARD.value)
                if route:
                    candidates.append(IntentCandidate(
                        intent=IntentType.DEALER_DASHBOARD.value,
                        service_key=route["service"],
                        method=route["method"],
                        entity=primary_entity.text,
                        confidence=primary_entity.confidence * 0.85,
                        needs_groq=False,
                        reason=f"Entity match: {primary_entity.text}",
                        strategy="entity",
                        extracted_entities=matched_entities
                    ))
            elif primary_entity.type == EntityType.CITY:
                route = RoutingTable.get_route(IntentType.CITY_DASHBOARD.value)
                if route:
                    candidates.append(IntentCandidate(
                        intent=IntentType.CITY_DASHBOARD.value,
                        service_key=route["service"],
                        method=route["method"],
                        entity=primary_entity.text,
                        confidence=primary_entity.confidence * 0.80,
                        needs_groq=False,
                        reason=f"Entity match: {primary_entity.text}",
                        strategy="entity",
                        extracted_entities=matched_entities
                    ))
            elif primary_entity.type == EntityType.WAREHOUSE:
                route = RoutingTable.get_route(IntentType.WAREHOUSE_DASHBOARD.value)
                if route:
                    candidates.append(IntentCandidate(
                        intent=IntentType.WAREHOUSE_DASHBOARD.value,
                        service_key=route["service"],
                        method=route["method"],
                        entity=primary_entity.text,
                        confidence=primary_entity.confidence * 0.80,
                        needs_groq=False,
                        reason=f"Entity match: {primary_entity.text}",
                        strategy="entity",
                        extracted_entities=matched_entities
                    ))
            elif primary_entity.type == EntityType.PRODUCT:
                route = RoutingTable.get_route(IntentType.PRODUCT_DASHBOARD.value)
                if route:
                    candidates.append(IntentCandidate(
                        intent=IntentType.PRODUCT_DASHBOARD.value,
                        service_key=route["service"],
                        method=route["method"],
                        entity=primary_entity.text,
                        confidence=primary_entity.confidence * 0.80,
                        needs_groq=False,
                        reason=f"Entity match: {primary_entity.text}",
                        strategy="entity",
                        extracted_entities=matched_entities
                    ))
        
        # ============================================================
        # STEP 6: FLASHRANK RE-RANKING
        # ============================================================
        if self.ranker and len(candidates) > 1:
            try:
                passages = [
                    {"id": i, "text": f"{c.intent}: {c.entity or ''}", "meta": c}
                    for i, c in enumerate(candidates)
                ]
                results = self.ranker.rerank(query=message, passages=passages)
                for result in results:
                    idx = result["id"]
                    if idx < len(candidates):
                        candidates[idx].confidence = result["score"]
                candidates.sort(key=lambda x: x.confidence, reverse=True)
            except Exception as e:
                self.logger.warning(f"FlashRank failed: {e}")
        
        # ============================================================
        # STEP 7: SELECT BEST CANDIDATE
        # ============================================================
        if not candidates:
            return self._create_fallback_decision(message, "No intent detected")
        
        best = candidates[0]
        
        # Get confidence threshold
        threshold = RoutingTable.get_threshold(best.intent)
        if best.confidence < threshold:
            self.logger.info(f"⚠️ Low confidence: {best.confidence:.2f} < {threshold:.2f}")
            # Try to find a better candidate
            better = next((c for c in candidates if c.intent == IntentType.GENERAL_AI.value), None)
            if better and better.confidence >= 0.3:
                best = better
        
        # ============================================================
        # STEP 8: CREATE ROUTING DECISION
        # ============================================================
        routing_decision = self._create_routing_decision(
            candidate=best,
            pattern_score=pattern_score,
            semantic_score=semantic_score,
            extracted_entities=matched_entities,
            original_message=message
        )
        
        # ============================================================
        # STEP 9: VALIDATE ROUTE
        # ============================================================
        routing_decision.validated = RoutingTable.validate_route(
            routing_decision.intent,
            routing_decision.service_key,
            routing_decision.method
        )
        
        if not routing_decision.validated:
            routing_decision.validation_errors.append(
                f"Invalid route: {routing_decision.intent} → {routing_decision.service_key}.{routing_decision.method}"
            )
            self.logger.warning(f"⚠️ {routing_decision.validation_errors[0]}")
        
        # ============================================================
        # STEP 10: CACHE AND RETURN
        # ============================================================
        self._cache[cache_key] = routing_decision
        self.logger.info(f"🎯 Final: {routing_decision.intent} ({routing_decision.confidence:.2f})")
        return routing_decision
    
    def _detect_by_pattern(self, text: str, normalized: NormalizedText, entities: List[ExtractedEntity]) -> Tuple[Optional[str], float, Optional[str]]:
        """
        Detect intent using regex patterns.
        
        Returns:
            Tuple of (intent, confidence, entity)
        """
        cleaned = text.strip()
        cleaned_lower = cleaned.lower()
        
        # DN Detection
        if self.DN_PATTERN.search(cleaned):
            dn_number = self.DN_PATTERN.search(cleaned).group(1)
            return IntentType.DN_LOOKUP.value, 1.0, dn_number
        
        # Pending DN
        if self.PENDING_DN_PATTERN.search(cleaned_lower):
            return IntentType.PENDING_DN.value, 0.95, None
        
        # Pending PGI
        if self.PENDING_PGI_PATTERN.search(cleaned_lower):
            return IntentType.PENDING_PGI.value, 0.95, None
        
        # Pending POD
        if self.PENDING_POD_PATTERN.search(cleaned_lower):
            return IntentType.PENDING_POD.value, 0.95, None
        
        # Top Dealers
        if self.TOP_DEALERS_PATTERN.search(cleaned_lower):
            return IntentType.TOP_DEALERS.value, 0.90, None
        
        # Bottom Dealers
        if self.BOTTOM_DEALERS_PATTERN.search(cleaned_lower):
            return IntentType.BOTTOM_DEALERS.value, 0.90, None
        
        # Comparison
        comparison_match = self.COMPARISON_PATTERN.search(cleaned)
        if comparison_match:
            entity1 = comparison_match.group(1).strip()
            entity2 = comparison_match.group(2).strip()
            return IntentType.DEALER_COMPARISON.value, 0.85, entity1
        
        # National KPI
        if self.NATIONAL_KPI_PATTERN.search(cleaned_lower):
            return IntentType.NATIONAL_KPI.value, 0.90, None
        
        # Help
        if self.HELP_PATTERN.search(cleaned_lower):
            return IntentType.HELP.value, 0.95, None
        
        # Greeting
        if self.GREETING_PATTERN.search(cleaned_lower):
            return IntentType.GREETING.value, 0.95, None
        
        # Short text with entity - check if it's a dealer name
        if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
            # Check if any entity matches
            for entity in entities:
                if entity.type == EntityType.DEALER:
                    return IntentType.DEALER_DASHBOARD.value, 0.80, entity.text
                elif entity.type == EntityType.CITY:
                    return IntentType.CITY_DASHBOARD.value, 0.80, entity.text
                elif entity.type == EntityType.WAREHOUSE:
                    return IntentType.WAREHOUSE_DASHBOARD.value, 0.80, entity.text
                elif entity.type == EntityType.PRODUCT:
                    return IntentType.PRODUCT_DASHBOARD.value, 0.80, entity.text
            
            # If no entity matched but looks like a dealer name
            if any(word in cleaned_lower for word in ['electronics', 'traders', 'enterprises', 'group', 'mart']):
                return IntentType.DEALER_DASHBOARD.value, 0.75, cleaned
        
        return None, 0.0, None
    
    def _create_routing_decision(
        self,
        candidate: IntentCandidate,
        pattern_score: float,
        semantic_score: float,
        extracted_entities: List[ExtractedEntity],
        original_message: str
    ) -> RoutingDecision:
        """Create RoutingDecision from candidate"""
        # Calculate combined confidence
        combined_score = self.confidence_engine.calculate_combined_score(
            pattern_score=pattern_score,
            semantic_score=semantic_score,
            fuzzy_score=candidate.confidence if candidate.strategy == "fuzzy" else 0.0,
            spacy_score=0.0,
            flashrank_score=0.0,
            entity_score=0.0
        )
        
        decision = RoutingDecision(
            intent=candidate.intent,
            service_key=candidate.service_key,
            method=candidate.method,
            entity=candidate.entity,
            entity2=candidate.entity2,
            confidence=candidate.confidence,
            needs_groq=candidate.needs_groq,
            reason=candidate.reason,
            original_message=original_message,
            suggestions=[],
            detection_method=candidate.strategy,
            combined_score=combined_score,
            pattern_score=pattern_score,
            semantic_score=semantic_score,
            extracted_entities=extracted_entities,
            primary_entity=extracted_entities[0] if extracted_entities else None
        )
        
        return decision
    
    def _create_fallback_decision(self, message: str, reason: str) -> RoutingDecision:
        """Create fallback routing decision"""
        return RoutingDecision(
            intent=IntentType.GENERAL_AI.value,
            service_key=ServiceKey.GROQ.value,
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason=reason,
            original_message=message,
            detection_method="fallback",
            combined_score=0.30
        )
    
    def _get_cache_key(self, text: str) -> str:
        """Generate cache key from text"""
        return f"intent:{hashlib.md5(text[:100].encode()).hexdigest()}"
    
    def _cache_enabled(self) -> bool:
        return CACHETOOLS_AVAILABLE and hasattr(self, '_cache')
    
    def clear_cache(self):
        if self._cache_enabled():
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self.logger.info("✅ Cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache) if self._cache_enabled() else 0
        }
    
    def get_health(self) -> Dict[str, Any]:
        return {
            "status": "healthy",
            "version": "3.0",
            "spacy_available": SPACY_AVAILABLE,
            "sentence_transformers_available": SENTENCE_TRANSFORMERS_AVAILABLE,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
            "flashrank_available": FLASHRANK_AVAILABLE and self.ranker is not None,
            "cache_stats": self.get_cache_stats()
        }

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'IntentDetectionEngine',
    'RoutingDecision',
    'RoutingTable',
    'EntityType',
    'IntentType',
    'ServiceKey',
    'SPACY_AVAILABLE',
    'SENTENCE_TRANSFORMERS_AVAILABLE',
    'RAPIDFUZZ_AVAILABLE',
    'FLASHRANK_AVAILABLE'
]

logger.info("=" * 70)
logger.info("Intent Detection Engine v3.0 - ENTERPRISE SIMPLIFIED")
logger.info("=" * 70)
logger.info("✅ Text Normalization")
logger.info("✅ Entity Extraction (spaCy)")
logger.info("✅ Entity Matching (RapidFuzz)")
logger.info("✅ Semantic Routing (SentenceTransformer)")
logger.info("✅ FlashRank Re-ranking")
logger.info("✅ Confidence Engine")
logger.info("✅ Caching")
logger.info("✅ NO business logic, NO SQL, NO formatting")
logger.info("=" * 70)
