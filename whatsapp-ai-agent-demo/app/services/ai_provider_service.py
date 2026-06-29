"""
File: app/services/ai_provider_service_intents.py
Version: 5.0 - ENTERPRISE INTENT DETECTION with Full Library Stack
Purpose: Intent detection using all available libraries from requirements.txt
         Libraries: spaCy, sentence-transformers, RapidFuzz, FlashRank, scikit-learn
"""

import re
import logging
import threading
import time
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

logger = logging.getLogger(__name__)

# ============================================================
# LIBRARY IMPORTS - ALL FROM YOUR REQUIREMENTS.TXT
# ============================================================

# 1. spaCy - Entity Extraction
try:
    import spacy
    SPACY_AVAILABLE = True
    logger.info("✅ spaCy available for entity extraction")
except ImportError as e:
    SPACY_AVAILABLE = False
    logger.warning(f"⚠️ spaCy not available: {e}")

# 2. Sentence-Transformers - Semantic Intent Detection
try:
    from sentence_transformers import SentenceTransformer
    import torch
    SENTENCE_TRANSFORMERS_AVAILABLE = True
    logger.info("✅ Sentence-Transformers available for semantic intent detection")
except ImportError as e:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning(f"⚠️ Sentence-Transformers not available: {e}")

# 3. RapidFuzz - Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
    logger.info("✅ RapidFuzz available for fuzzy matching")
except ImportError as e:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning(f"⚠️ RapidFuzz not available: {e}")

# 4. FlashRank - Re-ranking
try:
    from flashrank import Ranker
    FLASHRANK_AVAILABLE = True
    logger.info("✅ FlashRank available for re-ranking")
except ImportError as e:
    FLASHRANK_AVAILABLE = False
    logger.warning(f"⚠️ FlashRank not available: {e}")

# 5. Cachetools - Caching
try:
    from cachetools import TTLCache, cached
    CACHETOOLS_AVAILABLE = True
    logger.info("✅ Cachetools available for caching")
except ImportError as e:
    CACHETOOLS_AVAILABLE = False
    logger.warning(f"⚠️ Cachetools not available: {e}")

# 6. NumPy - Numerical operations
try:
    import numpy as np
    NUMPY_AVAILABLE = True
    logger.info("✅ NumPy available for numerical operations")
except ImportError as e:
    NUMPY_AVAILABLE = False
    logger.warning(f"⚠️ NumPy not available: {e}")

# 7. Scikit-learn - ML utilities
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
    logger.info("✅ Scikit-learn available for ML utilities")
except ImportError as e:
    SKLEARN_AVAILABLE = False
    logger.warning(f"⚠️ Scikit-learn not available: {e}")

# 8. Tenacity - Retry logic
try:
    from tenacity import retry, stop_after_attempt, wait_exponential
    TENACITY_AVAILABLE = True
    logger.info("✅ Tenacity available for retry logic")
except ImportError as e:
    TENACITY_AVAILABLE = False
    logger.warning(f"⚠️ Tenacity not available: {e}")

# 9. Pydantic - Data validation
try:
    from pydantic import BaseModel, Field
    PYDANTIC_AVAILABLE = True
    logger.info("✅ Pydantic available for data validation")
except ImportError as e:
    PYDANTIC_AVAILABLE = False
    logger.warning(f"⚠️ Pydantic not available: {e}")

# ============================================================
# ROUTING DECISION
# ============================================================

@dataclass
class RoutingDecision:
    """Routing decision with enhanced metadata from all libraries"""
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
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Library-specific scores
    spacy_entities: Dict[str, List[str]] = field(default_factory=dict)
    semantic_score: float = 0.0
    fuzzy_score: float = 0.0
    flashrank_score: float = 0.0
    pattern_score: float = 0.0
    ml_score: float = 0.0
    combined_score: float = 0.0
    
    # Ranking results
    ranked_candidates: List[Dict[str, Any]] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "entity2": self.entity2,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message,
            "suggestions": self.suggestions,
            "metadata": self.metadata,
            "spacy_entities": self.spacy_entities,
            "semantic_score": self.semantic_score,
            "fuzzy_score": self.fuzzy_score,
            "flashrank_score": self.flashrank_score,
            "pattern_score": self.pattern_score,
            "ml_score": self.ml_score,
            "combined_score": self.combined_score,
            "ranked_candidates": self.ranked_candidates
        }

# ============================================================
# SPACY ENTITY EXTRACTOR
# ============================================================

class SpacyEntityExtractor:
    """Entity extraction using spaCy"""
    
    def __init__(self):
        self.nlp = None
        self.logger = logging.getLogger(__name__)
        
        if SPACY_AVAILABLE:
            try:
                # Try different models
                models = ["en_core_web_sm", "en_core_web_lg", "en_core_web_md"]
                for model in models:
                    try:
                        self.nlp = spacy.load(model)
                        self.logger.info(f"✅ spaCy loaded: {model}")
                        break
                    except OSError:
                        continue
                
                if not self.nlp:
                    self.logger.warning("⚠️ No spaCy model found. Download: python -m spacy download en_core_web_sm")
            except Exception as e:
                self.logger.warning(f"⚠️ spaCy initialization failed: {e}")
                self.nlp = None
    
    def extract_entities(self, text: str) -> Dict[str, List[str]]:
        """Extract entities from text"""
        if not self.nlp:
            return {}
        
        try:
            doc = self.nlp(text)
            entities = {
                "PERSON": [],
                "ORG": [],
                "GPE": [],  # Cities, countries
                "LOC": [],  # Locations
                "PRODUCT": [],
                "DATE": [],
                "MONEY": [],
                "QUANTITY": [],
                "FAC": [],  # Facilities (warehouses)
                "EVENT": []
            }
            
            for ent in doc.ents:
                if ent.label_ in entities:
                    entities[ent.label_].append(ent.text)
            
            # Additional custom extraction for dealer names
            dealer_pattern = re.compile(r'(?:dealer|customer|partner)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', re.IGNORECASE)
            dealer_matches = dealer_pattern.findall(text)
            if dealer_matches:
                entities["DEALER"] = dealer_matches
            
            # Extract warehouse from text
            warehouse_pattern = re.compile(r'(?:warehouse|wh|depot|distribution)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', re.IGNORECASE)
            warehouse_matches = warehouse_pattern.findall(text)
            if warehouse_matches:
                entities["WAREHOUSE"] = warehouse_matches
            
            self.logger.debug(f"Extracted entities: {entities}")
            return entities
            
        except Exception as e:
            self.logger.warning(f"spaCy entity extraction failed: {e}")
            return {}

# ============================================================
# SEMANTIC INTENT DETECTOR
# ============================================================

class SemanticIntentDetector:
    """Semantic intent detection using Sentence-Transformers"""
    
    def __init__(self):
        self.model = None
        self.logger = logging.getLogger(__name__)
        
        # Intent examples for semantic matching
        self.intent_examples = {
            'dn_lookup': [
                "check delivery note number",
                "track DN status",
                "delivery note details",
                "DN lookup",
                "find delivery note",
                "DN number",
                "delivery note"
            ],
            'pending_dn': [
                "pending deliveries",
                "open delivery notes",
                "outstanding DNs",
                "deliveries not completed",
                "waiting for delivery",
                "pending DN",
                "pending delivery"
            ],
            'pending_pgi': [
                "goods issue pending",
                "PGI not done",
                "pending goods issuance",
                "PGI status",
                "goods issue"
            ],
            'pending_pod': [
                "proof of delivery pending",
                "POD not received",
                "delivery proof missing",
                "POD status",
                "proof of delivery"
            ],
            'dealer_dashboard': [
                "dealer performance summary",
                "show dealer metrics",
                "dealer revenue and units",
                "dealer dashboard overview",
                "dealer analytics",
                "dealer profile",
                "dealer status"
            ],
            'top_dealers': [
                "best performing dealers",
                "top revenue dealers",
                "dealer ranking",
                "highest performing dealers",
                "top dealers",
                "best dealers"
            ],
            'bottom_dealers': [
                "lowest performing dealers",
                "dealers with low revenue",
                "bottom dealer ranking",
                "worst performing dealers",
                "bottom dealers",
                "worst dealers"
            ],
            'national_kpi': [
                "country-wide performance",
                "national key performance indicators",
                "Pakistan overall metrics",
                "executive dashboard",
                "company performance summary",
                "national KPI",
                "Pakistan performance"
            ],
            'warehouse_dashboard': [
                "warehouse performance summary",
                "depot metrics",
                "distribution center overview",
                "warehouse analytics",
                "warehouse dashboard"
            ],
            'city_dashboard': [
                "city performance summary",
                "city revenue and units",
                "city analytics",
                "regional performance",
                "city dashboard"
            ],
            'product_dashboard': [
                "product performance summary",
                "product revenue and units",
                "product analytics",
                "model performance",
                "product dashboard"
            ],
            'help': [
                "show available commands",
                "what can you do",
                "help menu",
                "how to use this bot",
                "help",
                "commands"
            ],
            'greeting': [
                "hello",
                "hi there",
                "good morning",
                "hey",
                "greetings"
            ],
            'conversational': [
                "how are you",
                "what is your name",
                "tell me about yourself",
                "thank you",
                "thanks"
            ]
        }
        
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                # Use lightweight model
                self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
                self.logger.info("✅ Sentence-Transformers model loaded: all-MiniLM-L6-v2")
                
                # Pre-encode intent examples
                self.intent_embeddings = {}
                for intent, examples in self.intent_examples.items():
                    embeddings = self.model.encode(examples, convert_to_numpy=True)
                    self.intent_embeddings[intent] = embeddings.mean(axis=0)
                
                self.logger.info(f"✅ Pre-encoded {len(self.intent_embeddings)} intent embeddings")
                
            except Exception as e:
                self.logger.warning(f"⚠️ Sentence-Transformers initialization failed: {e}")
                self.model = None
    
    def predict(self, text: str) -> Tuple[str, float]:
        """Predict intent using semantic similarity"""
        if not self.model or not self.intent_embeddings:
            return "general_ai", 0.0
        
        try:
            # Encode input text
            text_embedding = self.model.encode([text], convert_to_numpy=True)[0]
            
            # Calculate cosine similarity with each intent
            similarities = {}
            for intent, embedding in self.intent_embeddings.items():
                # Normalize
                text_norm = text_embedding / np.linalg.norm(text_embedding)
                intent_norm = embedding / np.linalg.norm(embedding)
                similarity = np.dot(text_norm, intent_norm)
                similarities[intent] = float(similarity)
            
            # Get best match
            best_intent = max(similarities, key=similarities.get)
            best_score = similarities[best_intent]
            
            # Apply threshold
            if best_score < 0.5:
                return "general_ai", best_score
            
            return best_intent, best_score
            
        except Exception as e:
            self.logger.warning(f"Semantic prediction failed: {e}")
            return "general_ai", 0.0

# ============================================================
# ML INTENT CLASSIFIER (using scikit-learn)
# ============================================================

class MLIntentClassifier:
    """Machine Learning based intent classifier using TF-IDF"""
    
    def __init__(self):
        self.vectorizer = None
        self.intent_vectors = {}
        self.logger = logging.getLogger(__name__)
        
        # Same intent examples as semantic detector
        self.intent_examples = {
            'dn_lookup': [
                "check DN", "DN number", "delivery note", "track DN",
                "DN status", "DN lookup", "find DN", "DN details"
            ],
            'pending_dn': [
                "pending DN", "open delivery", "outstanding DN",
                "pending delivery", "delivery pending", "waiting for DN"
            ],
            'dealer_dashboard': [
                "dealer dashboard", "dealer performance", "dealer revenue",
                "dealer units", "dealer profile", "dealer summary"
            ],
            'top_dealers': [
                "top dealers", "best dealers", "top performing dealers",
                "dealer ranking", "highest revenue dealers"
            ],
            'national_kpi': [
                "national KPI", "Pakistan performance", "country metrics",
                "executive dashboard", "company performance"
            ],
            'help': [
                "help", "menu", "commands", "what can you do",
                "available commands", "how to use"
            ],
            'greeting': [
                "hello", "hi", "hey", "good morning", "greetings"
            ]
        }
        
        self._init_vectorizer()
    
    def _init_vectorizer(self):
        """Initialize TF-IDF vectorizer"""
        if not SKLEARN_AVAILABLE:
            return
        
        try:
            self.vectorizer = TfidfVectorizer(
                ngram_range=(1, 3),
                max_features=1000,
                stop_words='english',
                lowercase=True
            )
            
            # Prepare training data
            all_examples = []
            intent_labels = []
            
            for intent, examples in self.intent_examples.items():
                all_examples.extend(examples)
                intent_labels.extend([intent] * len(examples))
            
            # Fit vectorizer
            vectors = self.vectorizer.fit_transform(all_examples)
            
            # Store vectors per intent
            for intent in self.intent_examples.keys():
                indices = [i for i, label in enumerate(intent_labels) if label == intent]
                if indices:
                    intent_vectors = vectors[indices]
                    self.intent_vectors[intent] = intent_vectors.mean(axis=0)
            
            self.logger.info(f"✅ ML classifier trained on {len(all_examples)} examples")
            
        except Exception as e:
            self.logger.warning(f"⚠️ ML initialization failed: {e}")
            self.vectorizer = None
    
    def predict(self, text: str) -> Tuple[str, float]:
        """Predict intent using ML"""
        if not SKLEARN_AVAILABLE or not self.vectorizer:
            return "general_ai", 0.0
        
        try:
            text_vector = self.vectorizer.transform([text])
            
            similarities = {}
            for intent, intent_vector in self.intent_vectors.items():
                if intent_vector is not None:
                    similarity = cosine_similarity(text_vector, intent_vector)[0][0]
                    similarities[intent] = similarity
            
            if not similarities:
                return "general_ai", 0.0
            
            best_intent = max(similarities, key=similarities.get)
            best_score = similarities[best_intent]
            
            if best_score < 0.1:
                return "general_ai", best_score
            
            return best_intent, best_score
            
        except Exception as e:
            self.logger.warning(f"ML prediction failed: {e}")
            return "general_ai", 0.0

# ============================================================
# FLASHRANK RE-RANKER
# ============================================================

class FlashRankReRanker:
    """Re-rank candidate intents using FlashRank"""
    
    def __init__(self):
        self.ranker = None
        self.logger = logging.getLogger(__name__)
        
        if FLASHRANK_AVAILABLE:
            try:
                # Try different models
                models = ["ms-marco-MiniLM-L-12-v2", "ms-marco-TinyBERT-L-2-v2"]
                for model in models:
                    try:
                        self.ranker = Ranker(model=model)
                        self.logger.info(f"✅ FlashRank loaded: {model}")
                        break
                    except Exception:
                        continue
                
                if not self.ranker:
                    self.logger.warning("⚠️ No FlashRank model found")
            except Exception as e:
                self.logger.warning(f"⚠️ FlashRank initialization failed: {e}")
                self.ranker = None
    
    def rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Re-rank candidates using FlashRank"""
        if not self.ranker or not candidates:
            return candidates
        
        try:
            # Format for FlashRank
            passages = [
                {
                    "id": i,
                    "text": candidate.get("text", candidate.get("name", str(candidate))),
                    "meta": candidate
                }
                for i, candidate in enumerate(candidates)
            ]
            
            # Re-rank
            results = self.ranker.rerank(query=query, passages=passages)
            
            # Update candidates with scores
            for result in results:
                idx = result["id"]
                if idx < len(candidates):
                    candidates[idx]["flashrank_score"] = result["score"]
                    candidates[idx]["flashrank_rank"] = result["rank"]
            
            # Sort by FlashRank score
            candidates.sort(key=lambda x: x.get("flashrank_score", 0), reverse=True)
            
            return candidates
            
        except Exception as e:
            self.logger.warning(f"FlashRank re-ranking failed: {e}")
            return candidates

# ============================================================
# MAIN INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    """
    ENTERPRISE Intent Detection Engine using all available libraries:
    - spaCy for entity extraction
    - Sentence-Transformers for semantic detection
    - scikit-learn for ML-based classification
    - RapidFuzz for fuzzy matching
    - FlashRank for re-ranking
    - Cachetools for caching
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.start_time = time.time()
        
        # ============================================================
        # 1. INITIALIZE ALL COMPONENTS
        # ============================================================
        
        self.logger.info("=" * 70)
        self.logger.info("🚀 Initializing Enterprise Intent Detection Engine")
        self.logger.info("=" * 70)
        
        # spaCy Entity Extractor
        self.entity_extractor = SpacyEntityExtractor()
        self.logger.info(f"   spaCy: {'✅' if self.entity_extractor.nlp else '❌'}")
        
        # Semantic Intent Detector
        self.semantic_detector = SemanticIntentDetector()
        self.logger.info(f"   Sentence-Transformers: {'✅' if self.semantic_detector.model else '❌'}")
        
        # ML Intent Classifier
        self.ml_classifier = MLIntentClassifier()
        self.logger.info(f"   scikit-learn: {'✅' if SKLEARN_AVAILABLE else '❌'}")
        
        # FlashRank Re-Ranker
        self.reranker = FlashRankReRanker()
        self.logger.info(f"   FlashRank: {'✅' if self.reranker.ranker else '❌'}")
        
        # RapidFuzz
        self.logger.info(f"   RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
        
        # ============================================================
        # 2. CACHE
        # ============================================================
        
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # ============================================================
        # 3. PATTERNS
        # ============================================================
        
        self._init_patterns()
        
        # ============================================================
        # 4. DEALER ALIASES
        # ============================================================
        
        self.DEALER_ALIASES = {
            "sham": "Sham Electronics",
            "sham electronics": "Sham Electronics",
            "ruba": "Ruba Digital Wah",
            "ruba digital": "Ruba Digital Wah",
            "ruba digital wah": "Ruba Digital Wah",
            "taj": "Taj Electronics",
            "taj electronics": "Taj Electronics",
            "haroon": "Haroon Electronics",
            "haroon electronics": "Haroon Electronics",
            "mian": "Mian Group Chakwal",
            "mian group": "Mian Group Chakwal",
            "arco": "Arco Electronics",
            "arco electronics": "Arco Electronics",
            "shah": "Shah Electronics",
            "shah electronics": "Shah Electronics",
        }
        
        # ============================================================
        # 5. DEALER CACHE
        # ============================================================
        
        self._dealer_names = []
        self._dealer_normalized = []
        self._dealer_cache_loaded = False
        self._dealer_cache_lock = threading.RLock()
        
        # ============================================================
        # 6. INTENT STATS
        # ============================================================
        
        self._intent_stats = {}
        
        # ============================================================
        # 7. LOAD DEALER CACHE
        # ============================================================
        
        self._load_dealer_cache()
        
        # ============================================================
        # 8. SUMMARY
        # ============================================================
        
        init_time = (time.time() - self.start_time) * 1000
        self.logger.info("=" * 70)
        self.logger.info(f"✅ Intent Detection Engine initialized in {init_time:.2f}ms")
        self.logger.info("   Components:")
        self.logger.info(f"   • spaCy: {'✅' if self.entity_extractor.nlp else '❌'} Entity Extraction")
        self.logger.info(f"   • Sentence-Transformers: {'✅' if self.semantic_detector.model else '❌'} Semantic Detection")
        self.logger.info(f"   • scikit-learn: {'✅' if SKLEARN_AVAILABLE else '❌'} ML Classification")
        self.logger.info(f"   • RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'} Fuzzy Matching")
        self.logger.info(f"   • FlashRank: {'✅' if self.reranker.ranker else '❌'} Re-ranking")
        self.logger.info(f"   • Cachetools: {'✅' if CACHETOOLS_AVAILABLE else '❌'} Caching")
        self.logger.info("=" * 70)
    
    def _init_patterns(self):
        """Initialize regex patterns"""
        # DN Patterns
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        self.DN_PREFIX_PATTERN = re.compile(r'\b(DN|DN/)\s*(\d{6,10})\b', re.IGNORECASE)
        
        # Pending Patterns
        self.PENDING_DN_PATTERN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
        self.PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue|goods issuance)', re.IGNORECASE)
        self.PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
        self.PENDING_GENERAL_PATTERN = re.compile(r'(?:pending|open|outstanding|waiting|delayed)', re.IGNORECASE)
        
        # Dealer Patterns
        self.DEALER_PATTERN = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_DASHBOARD_PATTERN = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics|performance)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking Patterns
        self.RANKING_PATTERN = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom|leading|performance)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        
        # Comparison Patterns
        self.COMPARISON_PATTERN = re.compile(
            r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        
        # Location Patterns
        self.WAREHOUSE_PATTERN = re.compile(
            r'(?:warehouse|wh|depot|distribution|store)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.CITY_PATTERN = re.compile(
            r'(?:city|in|at|location|region|area)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.PRODUCT_PATTERN = re.compile(
            r'(?:product|model|material|item|sku|article|goods)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # National KPI
        self.NATIONAL_KPI_PATTERN = re.compile(
            r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard|company wide|corporate|head office)',
            re.IGNORECASE
        )
        
        # Conversational
        self.HELP_PATTERN = re.compile(
            r'(?:help|menu|commands|what can you do|available commands|how to use|guide|tutorial)',
            re.IGNORECASE
        )
        self.GREETING_PATTERN = re.compile(
            r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings|yo|sup)',
            re.IGNORECASE
        )
        self.CONVERSATIONAL_PATTERN = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|something|anything|what is|how to|how do|'
            r'where is|when is|why is|who is|explain|describe|tell about|'
            r'know about|information about|details about)',
            re.IGNORECASE
        )
    
    # ============================================================
    # MAIN DETECT INTENT METHOD
    # ============================================================
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent using hybrid approach with all libraries:
        1. Pattern matching (fast, deterministic)
        2. spaCy entity extraction
        3. Semantic detection (Sentence-Transformers)
        4. ML-based classification (scikit-learn)
        5. Fuzzy matching (RapidFuzz)
        6. FlashRank re-ranking
        7. Combined confidence scoring
        """
        cleaned = message.strip()
        if not cleaned:
            return self._create_fallback_decision(cleaned, "Empty message")
        
        # Check cache
        cache_key = f"intent:{cleaned[:100]}"
        if self._cache_enabled() and cache_key in self._cache:
            self._cache_hits += 1
            self.logger.debug(f"Cache hit for: {cleaned[:50]}")
            return self._cache[cache_key]
        
        self._cache_misses += 1
        
        self.logger.info(f"🔍 Detecting intent for: {cleaned[:100]}")
        start_time = time.time()
        
        # ============================================================
        # STEP 1: PATTERN-BASED DETECTION (HIGHEST PRIORITY)
        # ============================================================
        
        pattern_decision = self._detect_by_pattern(cleaned)
        if pattern_decision and pattern_decision.confidence >= 0.98:
            self.logger.info(f"✅ Pattern match: {pattern_decision.intent} (confidence: {pattern_decision.confidence:.2f})")
            return self._finalize_decision(pattern_decision, cache_key)
        
        # ============================================================
        # STEP 2: SPACY ENTITY EXTRACTION
        # ============================================================
        
        spacy_entities = self.entity_extractor.extract_entities(cleaned)
        self.logger.debug(f"spaCy entities: {spacy_entities}")
        
        # ============================================================
        # STEP 3: SEMANTIC INTENT DETECTION
        # ============================================================
        
        semantic_intent, semantic_score = self.semantic_detector.predict(cleaned)
        self.logger.debug(f"Semantic intent: {semantic_intent} (score: {semantic_score:.2f})")
        
        # ============================================================
        # STEP 4: ML-BASED CLASSIFICATION
        # ============================================================
        
        ml_intent, ml_score = self.ml_classifier.predict(cleaned)
        self.logger.debug(f"ML intent: {ml_intent} (score: {ml_score:.2f})")
        
        # ============================================================
        # STEP 5: RAPIDFUZZ MATCHING
        # ============================================================
        
        fuzzy_result = self._detect_with_fuzzy(cleaned)
        
        # ============================================================
        # STEP 6: COMBINE DECISIONS
        # ============================================================
        
        # Build candidate list for FlashRank
        candidates = []
        
        # Add pattern match
        if pattern_decision:
            candidates.append({
                "id": "pattern",
                "text": pattern_decision.intent,
                "confidence": pattern_decision.confidence,
                "decision": pattern_decision,
                "entity": pattern_decision.entity
            })
        
        # Add semantic match
        if semantic_score > 0.3:
            candidates.append({
                "id": "semantic",
                "text": semantic_intent,
                "confidence": semantic_score,
                "intent": semantic_intent
            })
        
        # Add ML match
        if ml_score > 0.2:
            candidates.append({
                "id": "ml",
                "text": ml_intent,
                "confidence": ml_score,
                "intent": ml_intent
            })
        
        # Add fuzzy match
        if fuzzy_result:
            candidates.append({
                "id": "fuzzy",
                "text": fuzzy_result.get("intent", ""),
                "confidence": fuzzy_result.get("score", 0),
                "entity": fuzzy_result.get("entity", ""),
                "decision": fuzzy_result.get("decision")
            })
        
        # ============================================================
        # STEP 7: FLASHRANK RE-RANKING
        # ============================================================
        
        if candidates and self.reranker.ranker:
            candidates = self.reranker.rerank(cleaned, candidates)
            self.logger.debug(f"FlashRank re-ranked {len(candidates)} candidates")
        
        # ============================================================
        # STEP 8: FINAL DECISION
        # ============================================================
        
        if candidates:
            best = candidates[0]
            
            # Extract spaCy entities for dealer detection
            if spacy_entities:
                # Check if ORG entities match dealer names
                if spacy_entities.get("ORG"):
                    for org in spacy_entities["ORG"]:
                        found_dealer = self._find_dealer_fuzzy(org)
                        if found_dealer:
                            best["entity"] = found_dealer
                            best["intent"] = "dealer_dashboard"
                            break
                
                # Check if GPE entities match city names
                if spacy_entities.get("GPE"):
                    for gpe in spacy_entities["GPE"]:
                        # Could be a city
                        pass
            
            # Create final decision
            intent = best.get("intent", best.get("text", "general_ai"))
            entity = best.get("entity", pattern_decision.entity if pattern_decision else None)
            
            final_decision = self._create_decision(
                intent=intent,
                service_key=self._get_service_key(intent),
                method=self._get_method(intent),
                entity=entity,
                confidence=best.get("confidence", 0.5),
                reason=f"Best candidate from combined analysis: {best.get('id', 'unknown')}",
                original=cleaned
            )
            
            # Add library scores
            final_decision.spacy_entities = spacy_entities
            final_decision.semantic_score = semantic_score
            final_decision.ml_score = ml_score
            final_decision.flashrank_score = best.get("flashrank_score", 0)
            final_decision.ranked_candidates = candidates
            
            # Calculate combined confidence
            final_decision.combined_score = self._calculate_combined_score(
                pattern_decision.confidence if pattern_decision else 0,
                semantic_score,
                ml_score,
                final_decision.flashrank_score
            )
            
            return self._finalize_decision(final_decision, cache_key)
        
        # ============================================================
        # STEP 9: FALLBACK
        # ============================================================
        
        fallback = self._create_fallback_decision(cleaned, "No intent detected")
        return self._finalize_decision(fallback, cache_key)
    
    def _calculate_combined_score(self, pattern_score: float, semantic_score: float, ml_score: float, flashrank_score: float) -> float:
        """Calculate combined confidence score with weights"""
        weights = {
            'pattern': 0.35,
            'semantic': 0.25,
            'ml': 0.20,
            'flashrank': 0.20
        }
        
        combined = (
            pattern_score * weights['pattern'] +
            semantic_score * weights['semantic'] +
            ml_score * weights['ml'] +
            flashrank_score * weights['flashrank']
        )
        
        return min(combined, 1.0)
    
    def _get_service_key(self, intent: str) -> str:
        """Map intent to service key"""
        service_map = {
            'dn_lookup': 'dn',
            'pending_dn': 'dn',
            'pending_pgi': 'dn',
            'pending_pod': 'dn',
            'dealer_dashboard': 'dealer',
            'dealer_suggestion': 'dealer',
            'top_dealers': 'dealer',
            'bottom_dealers': 'dealer',
            'comparison': 'dealer',
            'warehouse_dashboard': 'warehouse',
            'city_dashboard': 'city',
            'product_dashboard': 'product',
            'national_kpi': 'national_kpi',
            'help': 'groq',
            'greeting': 'groq',
            'conversational': 'groq',
            'general_ai': 'groq'
        }
        return service_map.get(intent, 'groq')
    
    def _get_method(self, intent: str) -> str:
        """Map intent to method"""
        method_map = {
            'dn_lookup': 'get_dn_dashboard',
            'pending_dn': 'get_pending_dns',
            'pending_pgi': 'get_pending_pgi',
            'pending_pod': 'get_pending_pod',
            'dealer_dashboard': 'get_dealer_dashboard',
            'dealer_suggestion': 'suggest_dealers',
            'top_dealers': 'get_top_dealers',
            'bottom_dealers': 'get_bottom_dealers',
            'comparison': 'compare_dealers',
            'warehouse_dashboard': 'get_warehouse_dashboard',
            'city_dashboard': 'get_city_dashboard',
            'product_dashboard': 'get_product_dashboard',
            'national_kpi': 'get_national_kpi_dashboard',
            'help': 'process_query',
            'greeting': 'process_query',
            'conversational': 'process_query',
            'general_ai': 'process_query'
        }
        return method_map.get(intent, 'process_query')
    
    # ============================================================
    # PATTERN-BASED DETECTION
    # ============================================================
    
    def _detect_by_pattern(self, cleaned: str) -> Optional[RoutingDecision]:
        """Detect intent using regex patterns"""
        normalized = cleaned.lower()
        
        # 1. DN Detection
        if self._is_dn_number(cleaned):
            dn_number = re.sub(r'\D', '', cleaned)
            return self._create_decision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                reason="DN number detected",
                original=cleaned
            )
        
        dn_prefix_match = self.DN_PREFIX_PATTERN.search(cleaned)
        if dn_prefix_match:
            dn_number = dn_prefix_match.group(2)
            return self._create_decision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                reason="DN with prefix detected",
                original=cleaned
            )
        
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            dn_number = dn_match.group(1)
            return self._create_decision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                reason="DN number extracted",
                original=cleaned
            )
        
        # 2. Pending Detection
        if self.PENDING_DN_PATTERN.search(normalized):
            return self._create_decision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.98,
                reason="Pending DN query",
                original=cleaned
            )
        
        if self.PENDING_PGI_PATTERN.search(normalized):
            return self._create_decision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                reason="Pending PGI query",
                original=cleaned
            )
        
        if self.PENDING_POD_PATTERN.search(normalized):
            return self._create_decision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                reason="Pending POD query",
                original=cleaned
            )
        
        if self.PENDING_GENERAL_PATTERN.search(normalized):
            return self._create_decision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.80,
                reason="General pending query",
                original=cleaned
            )
        
        # 3. National KPI
        if self.NATIONAL_KPI_PATTERN.search(normalized):
            return self._create_decision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                confidence=0.95,
                reason="National KPI query",
                original=cleaned
            )
        
        # 4. Ranking
        ranking_result = self._detect_ranking(normalized)
        if ranking_result:
            intent, service_key, method = ranking_result
            return self._create_decision(
                intent=intent,
                service_key=service_key,
                method=method,
                confidence=0.90,
                reason=f"Ranking: {intent}",
                original=cleaned
            )
        
        # 5. Comparison
        comparison_match = self.COMPARISON_PATTERN.search(cleaned)
        if comparison_match:
            entity1 = comparison_match.group(1).strip()
            entity2 = comparison_match.group(2).strip()
            return self._create_decision(
                intent="comparison",
                service_key="dealer",
                method="compare_dealers",
                entity=entity1,
                entity2=entity2,
                confidence=0.90,
                reason=f"Comparison: {entity1} vs {entity2}",
                original=cleaned
            )
        
        # 6. Dealer
        dealer_result = self._detect_dealer(cleaned, normalized)
        if dealer_result:
            return dealer_result
        
        # 7. Location
        location_result = self._detect_location(cleaned, normalized)
        if location_result:
            return location_result
        
        # 8. Conversational
        if self.HELP_PATTERN.search(normalized):
            return self._create_decision(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Help query",
                original=cleaned
            )
        
        if self.GREETING_PATTERN.search(normalized):
            return self._create_decision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original=cleaned
            )
        
        if self.CONVERSATIONAL_PATTERN.search(normalized):
            return self._create_decision(
                intent="conversational",
                service_key="groq",
                method="process_query",
                confidence=0.85,
                needs_groq=True,
                reason="Conversational question",
                original=cleaned
            )
        
        return None
    
    # ============================================================
    # DEALER DETECTION WITH RAPIDFUZZ
    # ============================================================
    
    def _detect_dealer(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect dealer using RapidFuzz and aliases"""
        
        # Check aliases first
        cleaned_lower = cleaned.lower()
        for alias, full_name in self.DEALER_ALIASES.items():
            if alias in cleaned_lower or cleaned_lower in alias:
                return self._create_decision(
                    intent="dealer_dashboard",
                    service_key="dealer",
                    method="get_dealer_dashboard",
                    entity=full_name,
                    confidence=0.95,
                    reason=f"Dealer alias: {full_name}",
                    original=cleaned
                )
        
        # Try dashboard pattern
        dashboard_match = self.DEALER_DASHBOARD_PATTERN.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
            dealer_name = self._clean_dealer_name(dealer_name)
            if dealer_name:
                found_dealer = self._find_dealer_fuzzy(dealer_name)
                if found_dealer:
                    return self._create_decision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=found_dealer,
                        confidence=0.90,
                        reason=f"Dealer dashboard: {found_dealer}",
                        original=cleaned
                    )
        
        # Try dealer pattern
        dealer_match = self.DEALER_PATTERN.search(cleaned)
        if dealer_match:
            dealer_name = dealer_match.group(1).strip()
            dealer_name = self._clean_dealer_name(dealer_name)
            if dealer_name:
                found_dealer = self._find_dealer_fuzzy(dealer_name)
                if found_dealer:
                    return self._create_decision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=found_dealer,
                        confidence=0.85,
                        reason=f"Dealer found: {found_dealer}",
                        original=cleaned
                    )
                else:
                    suggestions = self._find_similar_dealers(dealer_name, limit=5)
                    if suggestions:
                        return self._create_decision(
                            intent="dealer_suggestion",
                            service_key="dealer",
                            method="suggest_dealers",
                            entity=dealer_name,
                            suggestions=suggestions,
                            confidence=0.70,
                            reason=f"Dealer not found, suggestions: {suggestions[:3]}",
                            original=cleaned
                        )
        
        # Check if it's just a dealer name (short query)
        words = cleaned.split()
        if 1 <= len(words) <= 4 and len(cleaned) > 2:
            if not re.match(r'^\d+$', cleaned):
                dealer_name = self._clean_dealer_name(cleaned)
                if dealer_name and len(dealer_name) > 1:
                    found_dealer = self._find_dealer_fuzzy(dealer_name)
                    if found_dealer:
                        return self._create_decision(
                            intent="dealer_dashboard",
                            service_key="dealer",
                            method="get_dealer_dashboard",
                            entity=found_dealer,
                            confidence=0.80,
                            reason=f"Dealer from short query: {found_dealer}",
                            original=cleaned
                        )
        
        return None
    
    def _detect_with_fuzzy(self, cleaned: str) -> Optional[Dict[str, Any]]:
        """Detect using RapidFuzz"""
        if not RAPIDFUZZ_AVAILABLE:
            return None
        
        # Check against known patterns
        # This would match against a database of known entities
        return None
    
    def _detect_location(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect warehouse/city/product"""
        warehouse_match = self.WAREHOUSE_PATTERN.search(cleaned)
        if warehouse_match:
            warehouse_name = warehouse_match.group(1).strip()
            return self._create_decision(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse_name,
                confidence=0.90,
                reason=f"Warehouse: {warehouse_name}",
                original=cleaned
            )
        
        city_match = self.CITY_PATTERN.search(cleaned)
        if city_match:
            city_name = city_match.group(1).strip()
            return self._create_decision(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city_name,
                confidence=0.90,
                reason=f"City: {city_name}",
                original=cleaned
            )
        
        product_match = self.PRODUCT_PATTERN.search(cleaned)
        if product_match:
            product_name = product_match.group(1).strip()
            return self._create_decision(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_name,
                confidence=0.90,
                reason=f"Product: {product_name}",
                original=cleaned
            )
        
        return None
    
    def _detect_ranking(self, normalized: str) -> Optional[Tuple[str, str, str]]:
        """Detect ranking"""
        if self.RANKING_PATTERN.search(normalized):
            if 'dealer' in normalized:
                if 'bottom' in normalized or 'worst' in normalized:
                    return ("bottom_dealers", "dealer", "get_bottom_dealers")
                else:
                    return ("top_dealers", "dealer", "get_top_dealers")
            
            elif 'city' in normalized:
                return ("top_cities", "city", "get_top_cities")
            
            elif 'warehouse' in normalized or 'wh' in normalized:
                return ("top_warehouses", "warehouse", "get_top_warehouses")
            
            elif 'product' in normalized or 'item' in normalized:
                return ("top_products", "product", "get_top_products")
        
        return None
    
    # ============================================================
    # FUZZY MATCHING WITH RAPIDFUZZ
    # ============================================================
    
    def _load_dealer_cache(self):
        """Load dealer names from database"""
        if self._dealer_cache_loaded:
            return
        
        with self._dealer_cache_lock:
            if self._dealer_cache_loaded:
                return
            
            try:
                from app.database import SessionLocal
                from app.models import DeliveryReport
                
                session = SessionLocal()
                try:
                    dealers = session.query(
                        DeliveryReport.customer_name
                    ).filter(
                        DeliveryReport.customer_name.isnot(None)
                    ).distinct().limit(10000).all()
                    
                    self._dealer_names = [d.customer_name for d in dealers if d.customer_name]
                    self._dealer_normalized = [self._normalize_name(d) for d in self._dealer_names]
                    self._dealer_cache_loaded = True
                    self.logger.info(f"✅ Loaded {len(self._dealer_names)} dealers for fuzzy matching")
                except Exception as e:
                    self.logger.warning(f"⚠️ Failed to load dealers: {e}")
                finally:
                    session.close()
            except Exception as e:
                self.logger.warning(f"⚠️ Dealer cache load failed: {e}")
    
    def _normalize_name(self, name: str) -> str:
        """Normalize name for matching"""
        if not name:
            return ""
        name = re.sub(r'[^\w\s]', ' ', name)
        name = re.sub(r'\s+', ' ', name)
        return name.strip().lower()
    
    def _find_dealer_fuzzy(self, dealer_name: str) -> Optional[str]:
        """Find dealer using RapidFuzz"""
        if not dealer_name or not RAPIDFUZZ_AVAILABLE:
            return None
        
        self._load_dealer_cache()
        if not self._dealer_names:
            return None
        
        normalized = self._normalize_name(dealer_name)
        
        # Try exact match first
        for i, name in enumerate(self._dealer_names):
            if self._dealer_normalized[i] == normalized:
                return name
        
        # Try contains match
        for i, name in enumerate(self._dealer_names):
            if normalized in self._dealer_normalized[i] or self._dealer_normalized[i] in normalized:
                return name
        
        # Try fuzzy match
        try:
            results = process.extract(
                normalized,
                self._dealer_normalized,
                scorer=fuzz.ratio,
                limit=1
            )
            
            if results:
                best_match, score, _ = results[0]
                if score >= 75:  # Threshold
                    idx = self._dealer_normalized.index(best_match)
                    return self._dealer_names[idx]
        except Exception as e:
            self.logger.warning(f"RapidFuzz matching failed: {e}")
        
        return None
    
    def _find_similar_dealers(self, dealer_name: str, limit: int = 5) -> List[str]:
        """Find similar dealers using RapidFuzz"""
        if not dealer_name or not RAPIDFUZZ_AVAILABLE:
            return []
        
        self._load_dealer_cache()
        if not self._dealer_names:
            return []
        
        normalized = self._normalize_name(dealer_name)
        results = []
        
        # First, exact and contains matches
        for i, name in enumerate(self._dealer_names):
            if normalized == self._dealer_normalized[i] or \
               normalized in self._dealer_normalized[i] or \
               self._dealer_normalized[i] in normalized:
                results.append(name)
        
        # Then fuzzy matches
        if len(results) < limit:
            try:
                fuzzy_results = process.extract(
                    normalized,
                    self._dealer_normalized,
                    scorer=fuzz.ratio,
                    limit=limit
                )
                
                for match, score, idx in fuzzy_results:
                    if score >= 60:
                        name = self._dealer_names[idx]
                        if name not in results:
                            results.append(name)
            except Exception as e:
                self.logger.warning(f"Similar matching failed: {e}")
        
        return results[:limit]
    
    # ============================================================
    # HELPER METHODS
    # ============================================================
    
    def _create_decision(
        self,
        intent: str,
        service_key: str,
        method: str,
        entity: Optional[str] = None,
        entity2: Optional[str] = None,
        confidence: float = 0.0,
        needs_groq: bool = False,
        reason: str = "",
        original: str = "",
        suggestions: List[str] = None,
        metadata: Dict[str, Any] = None
    ) -> RoutingDecision:
        """Create routing decision"""
        return RoutingDecision(
            intent=intent,
            service_key=service_key,
            method=method,
            entity=entity,
            entity2=entity2,
            confidence=min(confidence, 1.0),
            needs_groq=needs_groq,
            reason=reason,
            original_message=original,
            suggestions=suggestions or [],
            metadata=metadata or {},
            pattern_score=confidence
        )
    
    def _create_fallback_decision(self, message: str, reason: str) -> RoutingDecision:
        """Create fallback routing decision"""
        return self._create_decision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason=reason,
            original=message
        )
    
    def _is_dn_number(self, text: str) -> bool:
        """Check if text is a valid DN number"""
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _clean_dealer_name(self, name: str) -> Optional[str]:
        """Clean dealer name"""
        if not name:
            return None
        
        cleaned = re.sub(
            r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|'
            r'dashboard|profile|summary|overview|info|information|details|status|'
            r'statistics|performance|the|a|an)\b',
            '',
            name,
            flags=re.IGNORECASE
        ).strip()
        
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned if len(cleaned) > 1 else None
    
    def _finalize_decision(self, decision: RoutingDecision, cache_key: str) -> RoutingDecision:
        """Finalize decision with caching and tracking"""
        # Cache result
        if self._cache_enabled():
            self._cache[cache_key] = decision
        
        # Track intent
        if decision.intent not in self._intent_stats:
            self._intent_stats[decision.intent] = 0
        self._intent_stats[decision.intent] += 1
        
        self.logger.info(f"🎯 Final Intent: {decision.intent} (confidence: {decision.confidence:.2f})")
        return decision
    
    def _cache_enabled(self) -> bool:
        """Check if caching is enabled"""
        return CACHETOOLS_AVAILABLE and hasattr(self, '_cache')
    
    def clear_cache(self):
        """Clear cache"""
        if self._cache_enabled():
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self.logger.info("✅ Cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache) if self._cache_enabled() else 0
        }
    
    def get_health(self) -> Dict[str, Any]:
        """Get health status"""
        return {
            "status": "healthy",
            "spacy_available": SPACY_AVAILABLE and self.entity_extractor.nlp is not None,
            "sentence_transformers_available": SENTENCE_TRANSFORMERS_AVAILABLE and self.semantic_detector.model is not None,
            "sklearn_available": SKLEARN_AVAILABLE,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
            "flashrank_available": FLASHRANK_AVAILABLE and self.reranker.ranker is not None,
            "cache_available": self._cache_enabled(),
            "dealer_cache_loaded": self._dealer_cache_loaded,
            "dealer_count": len(self._dealer_names),
            "cache_stats": self.get_cache_stats(),
            "intent_stats": self._intent_stats
        }

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'IntentDetectionEngine',
    'RoutingDecision',
    'SpacyEntityExtractor',
    'SemanticIntentDetector',
    'MLIntentClassifier',
    'FlashRankReRanker'
]

logger.info("=" * 70)
logger.info("Intent Detection Engine v5.0 - ENTERPRISE")
logger.info("=" * 70)
logger.info(f"✅ spaCy: {'Available' if SPACY_AVAILABLE else 'Not Available'}")
logger.info(f"✅ Sentence-Transformers: {'Available' if SENTENCE_TRANSFORMERS_AVAILABLE else 'Not Available'}")
logger.info(f"✅ scikit-learn: {'Available' if SKLEARN_AVAILABLE else 'Not Available'}")
logger.info(f"✅ RapidFuzz: {'Available' if RAPIDFUZZ_AVAILABLE else 'Not Available'}")
logger.info(f"✅ FlashRank: {'Available' if FLASHRANK_AVAILABLE else 'Not Available'}")
logger.info(f"✅ Cachetools: {'Available' if CACHETOOLS_AVAILABLE else 'Not Available'}")
logger.info("=" * 70)
