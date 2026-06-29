"""
File: app/services/ai_provider_service.py
Version: 10.4 - ENTERPRISE ARCHITECTURE with All Libraries
Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
         Orchestrates: spaCy → RapidFuzz → SentenceTransformer → FlashRank → Routing → Service → Groq
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
import sys
import asyncio
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import wraps
import uuid

logger = logging.getLogger(__name__)

# ============================================================
# TENACITY FOR RETRY LOGIC
# ============================================================

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.warning("⚠️ Tenacity not installed. Install with: pip install tenacity>=8.5.0")

# ============================================================
# CACHETOOLS FOR CACHING
# ============================================================

try:
    from cachetools import TTLCache, cached
    CACHETOOLS_AVAILABLE = True
except ImportError:
    CACHETOOLS_AVAILABLE = False
    TTLCache = None

# ============================================================
# IMPORTS WITH FALLBACK
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ============================================================
# ROUTING DECISION
# ============================================================

@dataclass
class RoutingDecision:
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
    combined_score: float = 0.0
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
            "combined_score": self.combined_score,
            "ranked_candidates": self.ranked_candidates
        }

# ============================================================
# INTENT DETECTION ENGINE - WITH ALL LIBRARIES
# ============================================================

class IntentDetectionEngine:
    """
    Enterprise Intent Detection Engine using:
    1. spaCy - Entity extraction (dealer, city, warehouse, product)
    2. RapidFuzz - Fuzzy matching for dealer names
    3. SentenceTransformer - Semantic intent detection
    4. FlashRank - Re-rank candidates
    5. Cachetools - TTL caching
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.start_time = time.time()
        
        # ============================================================
        # 1. LOAD SPACY
        # ============================================================
        self.nlp = None
        try:
            import spacy
            try:
                self.nlp = spacy.load("en_core_web_sm")
                self.logger.info("✅ spaCy loaded: en_core_web_sm")
            except OSError:
                try:
                    self.nlp = spacy.load("en_core_web_lg")
                    self.logger.info("✅ spaCy loaded: en_core_web_lg")
                except OSError:
                    self.logger.warning("⚠️ spaCy model not found. Download: python -m spacy download en_core_web_sm")
                    self.nlp = None
        except ImportError:
            self.logger.warning("⚠️ spaCy not installed")
        except Exception as e:
            self.logger.warning(f"⚠️ spaCy initialization failed: {e}")
        
        # ============================================================
        # 2. LOAD SENTENCE TRANSFORMER
        # ============================================================
        self.sentence_model = None
        try:
            from sentence_transformers import SentenceTransformer
            self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
            self.logger.info("✅ SentenceTransformer loaded: all-MiniLM-L6-v2")
        except ImportError:
            self.logger.warning("⚠️ SentenceTransformer not installed")
        except Exception as e:
            self.logger.warning(f"⚠️ SentenceTransformer initialization failed: {e}")
        
        # ============================================================
        # 3. LOAD RAPIDFUZZ
        # ============================================================
        self.rapidfuzz_available = False
        try:
            from rapidfuzz import fuzz, process
            self.rapidfuzz_available = True
            self.logger.info("✅ RapidFuzz available")
        except ImportError:
            self.logger.warning("⚠️ RapidFuzz not installed")
        
        # ============================================================
        # 4. LOAD FLASHRANK
        # ============================================================
        self.ranker = None
        try:
            from flashrank import Ranker
            self.ranker = Ranker(model="ms-marco-MiniLM-L-12-v2")
            self.logger.info("✅ FlashRank loaded: ms-marco-MiniLM-L-12-v2")
        except ImportError:
            self.logger.warning("⚠️ FlashRank not installed")
        except Exception as e:
            self.logger.warning(f"⚠️ FlashRank initialization failed: {e}")
        
        # ============================================================
        # 5. SETUP CACHE
        # ============================================================
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # ============================================================
        # 6. DEALER ALIASES
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
        }
        
        # ============================================================
        # 7. PATTERNS
        # ============================================================
        self._init_patterns()
        
        # ============================================================
        # 8. DEALER CACHE
        # ============================================================
        self._dealer_names = []
        self._dealer_normalized = []
        self._dealer_cache_loaded = False
        self._dealer_cache_lock = threading.RLock()
        self._load_dealer_cache()
        
        # ============================================================
        # 9. INTENT EXAMPLES FOR SEMANTIC MATCHING
        # ============================================================
        self._init_intent_examples()
        
        # ============================================================
        # 10. LOG SUMMARY
        # ============================================================
        init_time = (time.time() - self.start_time) * 1000
        self.logger.info("=" * 70)
        self.logger.info(f"✅ IntentDetectionEngine initialized in {init_time:.2f}ms")
        self.logger.info(f"   spaCy: {'✅' if self.nlp else '❌'} Entity Extraction")
        self.logger.info(f"   SentenceTransformer: {'✅' if self.sentence_model else '❌'} Semantic Detection")
        self.logger.info(f"   RapidFuzz: {'✅' if self.rapidfuzz_available else '❌'} Fuzzy Matching")
        self.logger.info(f"   FlashRank: {'✅' if self.ranker else '❌'} Re-ranking")
        self.logger.info(f"   Cachetools: {'✅' if CACHETOOLS_AVAILABLE else '❌'} Caching")
        self.logger.info(f"   Dealer Cache: {'✅' if self._dealer_cache_loaded else '❌'} Loaded")
        self.logger.info("=" * 70)
    
    def _init_patterns(self):
        """Initialize regex patterns"""
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        self.DN_PREFIX_PATTERN = re.compile(r'\b(DN|DN/)\s*(\d{6,10})\b', re.IGNORECASE)
        
        self.PENDING_DN_PATTERN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
        self.PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue)', re.IGNORECASE)
        self.PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
        
        self.DEALER_PATTERN = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_DASHBOARD_PATTERN = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        self.RANKING_PATTERN = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        
        self.HELP_PATTERN = re.compile(
            r'(?:help|menu|commands|what can you do|available commands|how to use)',
            re.IGNORECASE
        )
        self.GREETING_PATTERN = re.compile(
            r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings)',
            re.IGNORECASE
        )
        self.CONVERSATIONAL_PATTERN = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|something|anything|what is|how to|how do|'
            r'where is|when is|why is|who is|explain|describe|tell about)',
            re.IGNORECASE
        )
        
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
    
    def _init_intent_examples(self):
        """Initialize intent examples for semantic matching"""
        self.intent_examples = {
            'dn_lookup': [
                "check delivery note number", "track DN status", "delivery note details",
                "DN lookup", "find delivery note", "DN number", "delivery note"
            ],
            'pending_dn': [
                "pending deliveries", "open delivery notes", "outstanding DNs",
                "deliveries not completed", "waiting for delivery", "pending DN"
            ],
            'pending_pgi': [
                "goods issue pending", "PGI not done", "pending goods issuance", "PGI status"
            ],
            'pending_pod': [
                "proof of delivery pending", "POD not received", "delivery proof missing", "POD status"
            ],
            'dealer_dashboard': [
                "dealer performance summary", "show dealer metrics", "dealer revenue and units",
                "dealer dashboard overview", "dealer analytics", "dealer profile"
            ],
            'top_dealers': [
                "best performing dealers", "top revenue dealers", "dealer ranking",
                "highest performing dealers", "top dealers"
            ],
            'bottom_dealers': [
                "lowest performing dealers", "dealers with low revenue", "bottom dealer ranking",
                "worst performing dealers"
            ],
            'national_kpi': [
                "country-wide performance", "national key performance indicators",
                "Pakistan overall metrics", "executive dashboard"
            ],
            'warehouse_dashboard': [
                "warehouse performance summary", "depot metrics", "distribution center overview"
            ],
            'city_dashboard': [
                "city performance summary", "city revenue and units", "city analytics"
            ],
            'product_dashboard': [
                "product performance summary", "product revenue and units", "product analytics"
            ],
            'help': [
                "show available commands", "what can you do", "help menu", "how to use this bot"
            ],
            'greeting': [
                "hello", "hi there", "good morning", "hey"
            ],
            'conversational': [
                "how are you", "what is your name", "tell me about yourself", "thank you"
            ]
        }
        
        # Pre-compute embeddings for semantic matching
        self.intent_embeddings = {}
        if self.sentence_model:
            try:
                import numpy as np
                for intent, examples in self.intent_examples.items():
                    embeddings = self.sentence_model.encode(examples, convert_to_numpy=True)
                    self.intent_embeddings[intent] = embeddings.mean(axis=0)
                self.logger.info(f"✅ Pre-computed {len(self.intent_embeddings)} intent embeddings")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to pre-compute embeddings: {e}")
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent using all libraries:
        1. Pattern matching (fastest)
        2. spaCy entity extraction
        3. Semantic detection (SentenceTransformer)
        4. Fuzzy matching (RapidFuzz)
        5. FlashRank re-ranking
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
        
        # ============================================================
        # STEP 1: PATTERN MATCHING (HIGHEST PRIORITY)
        # ============================================================
        pattern_decision = self._detect_by_pattern(cleaned)
        if pattern_decision and pattern_decision.confidence >= 0.95:
            self.logger.info(f"✅ Pattern match: {pattern_decision.intent} (confidence: {pattern_decision.confidence:.2f})")
            return self._finalize_decision(pattern_decision, cache_key)
        
        # ============================================================
        # STEP 2: SPACY ENTITY EXTRACTION
        # ============================================================
        spacy_entities = {}
        if self.nlp:
            try:
                doc = self.nlp(cleaned)
                spacy_entities = {
                    "PERSON": [ent.text for ent in doc.ents if ent.label_ == "PERSON"],
                    "ORG": [ent.text for ent in doc.ents if ent.label_ == "ORG"],
                    "GPE": [ent.text for ent in doc.ents if ent.label_ == "GPE"],
                    "LOC": [ent.text for ent in doc.ents if ent.label_ == "LOC"],
                    "PRODUCT": [ent.text for ent in doc.ents if ent.label_ == "PRODUCT"],
                    "FAC": [ent.text for ent in doc.ents if ent.label_ == "FAC"],
                }
                self.logger.debug(f"spaCy entities: {spacy_entities}")
                
                # Check for dealer in ORG entities
                if spacy_entities.get("ORG"):
                    for org in spacy_entities["ORG"]:
                        found_dealer = self._find_dealer_fuzzy(org)
                        if found_dealer:
                            decision = self._create_decision(
                                intent="dealer_dashboard",
                                service_key="dealer",
                                method="get_dealer_dashboard",
                                entity=found_dealer,
                                confidence=0.95,
                                reason=f"spaCy ORG entity: {found_dealer}",
                                original=cleaned
                            )
                            decision.spacy_entities = spacy_entities
                            return self._finalize_decision(decision, cache_key)
                
                # Check for city in GPE entities
                if spacy_entities.get("GPE") and "city" in cleaned.lower():
                    for gpe in spacy_entities["GPE"]:
                        decision = self._create_decision(
                            intent="city_dashboard",
                            service_key="city",
                            method="get_city_dashboard",
                            entity=gpe,
                            confidence=0.85,
                            reason=f"spaCy GPE entity: {gpe}",
                            original=cleaned
                        )
                        decision.spacy_entities = spacy_entities
                        return self._finalize_decision(decision, cache_key)
            except Exception as e:
                self.logger.warning(f"spaCy processing failed: {e}")
        
        # ============================================================
        # STEP 3: SEMANTIC DETECTION (SentenceTransformer)
        # ============================================================
        semantic_intent = None
        semantic_score = 0.0
        if self.sentence_model and self.intent_embeddings:
            try:
                import numpy as np
                text_embedding = self.sentence_model.encode([cleaned], convert_to_numpy=True)[0]
                
                similarities = {}
                for intent, embedding in self.intent_embeddings.items():
                    text_norm = text_embedding / np.linalg.norm(text_embedding)
                    intent_norm = embedding / np.linalg.norm(embedding)
                    similarity = np.dot(text_norm, intent_norm)
                    similarities[intent] = float(similarity)
                
                semantic_intent = max(similarities, key=similarities.get)
                semantic_score = similarities[semantic_intent]
                self.logger.debug(f"Semantic: {semantic_intent} (score: {semantic_score:.2f})")
            except Exception as e:
                self.logger.warning(f"Semantic detection failed: {e}")
        
        # ============================================================
        # STEP 4: RAPIDFUZZ DEALER MATCHING
        # ============================================================
        fuzzy_result = None
        if self.rapidfuzz_available:
            fuzzy_result = self._detect_with_rapidfuzz(cleaned)
            if fuzzy_result:
                self.logger.debug(f"RapidFuzz: {fuzzy_result.get('entity')} (score: {fuzzy_result.get('score', 0):.2f})")
        
        # ============================================================
        # STEP 5: FLASHRANK RE-RANKING
        # ============================================================
        candidates = []
        
        if pattern_decision:
            candidates.append({
                "id": "pattern",
                "text": f"{pattern_decision.intent}: {pattern_decision.entity or ''}",
                "confidence": pattern_decision.confidence,
                "decision": pattern_decision
            })
        
        if semantic_intent and semantic_score > 0.3:
            candidates.append({
                "id": "semantic",
                "text": semantic_intent,
                "confidence": semantic_score,
                "intent": semantic_intent
            })
        
        if fuzzy_result:
            candidates.append({
                "id": "fuzzy",
                "text": fuzzy_result.get("entity", ""),
                "confidence": fuzzy_result.get("score", 0),
                "entity": fuzzy_result.get("entity", ""),
                "intent": fuzzy_result.get("intent", "dealer_dashboard")
            })
        
        # FlashRank re-ranking
        if candidates and self.ranker:
            try:
                passages = [
                    {"id": i, "text": c.get("text", ""), "meta": c}
                    for i, c in enumerate(candidates)
                ]
                results = self.ranker.rerank(query=cleaned, passages=passages)
                for result in results:
                    idx = result["id"]
                    if idx < len(candidates):
                        candidates[idx]["flashrank_score"] = result["score"]
                        candidates[idx]["flashrank_rank"] = result["rank"]
                candidates.sort(key=lambda x: x.get("flashrank_score", 0), reverse=True)
                self.logger.debug(f"FlashRank re-ranked {len(candidates)} candidates")
            except Exception as e:
                self.logger.warning(f"FlashRank failed: {e}")
        
        # ============================================================
        # STEP 6: FINAL DECISION
        # ============================================================
        if candidates:
            best = candidates[0]
            intent = best.get("intent", best.get("text", "general_ai"))
            entity = best.get("entity", pattern_decision.entity if pattern_decision else None)
            
            # Determine service key
            service_key = self._get_service_key(intent)
            
            final_decision = self._create_decision(
                intent=intent,
                service_key=service_key,
                method=self._get_method(intent),
                entity=entity,
                confidence=best.get("confidence", 0.5),
                reason=f"Best candidate: {best.get('id', 'unknown')}",
                original=cleaned
            )
            
            # Add library scores
            final_decision.spacy_entities = spacy_entities
            final_decision.semantic_score = semantic_score
            final_decision.flashrank_score = best.get("flashrank_score", 0)
            final_decision.ranked_candidates = candidates
            
            # Calculate combined confidence
            final_decision.combined_score = self._calculate_combined_score(
                pattern_decision.confidence if pattern_decision else 0,
                semantic_score,
                final_decision.flashrank_score
            )
            
            self.logger.info(f"✅ Combined: {final_decision.intent} (confidence: {final_decision.combined_score:.2f})")
            return self._finalize_decision(final_decision, cache_key)
        
        # ============================================================
        # STEP 7: FALLBACK
        # ============================================================
        fallback = self._create_fallback_decision(cleaned, "No intent detected")
        return self._finalize_decision(fallback, cache_key)
    
    def _detect_by_pattern(self, cleaned: str) -> Optional[RoutingDecision]:
        """Detect intent using regex patterns"""
        normalized = cleaned.lower()
        
        # DN Detection
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
        
        # Pending Detection
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
        
        # Dealer Detection with aliases
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
        
        # Dealer Patterns
        dealer_name = None
        dashboard_match = self.DEALER_DASHBOARD_PATTERN.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
            dealer_name = self._clean_dealer_name(dealer_name)
        
        if not dealer_name:
            dealer_match = self.DEALER_PATTERN.search(cleaned)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
                dealer_name = self._clean_dealer_name(dealer_name)
        
        if not dealer_name and len(cleaned.split()) <= 4 and len(cleaned) > 2:
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
                    confidence=0.90,
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
        
        # Ranking
        ranking_match = self.RANKING_PATTERN.search(normalized)
        if ranking_match:
            if 'dealer' in normalized:
                if 'bottom' in normalized or 'worst' in normalized:
                    return self._create_decision(
                        intent="bottom_dealers",
                        service_key="dealer",
                        method="get_bottom_dealers",
                        confidence=0.90,
                        reason="Bottom dealers",
                        original=cleaned
                    )
                else:
                    return self._create_decision(
                        intent="top_dealers",
                        service_key="dealer",
                        method="get_top_dealers",
                        confidence=0.90,
                        reason="Top dealers",
                        original=cleaned
                    )
        
        # Location
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
        
        # Conversational
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
                reason="Conversational",
                original=cleaned
            )
        
        return None
    
    def _detect_with_rapidfuzz(self, cleaned: str) -> Optional[Dict[str, Any]]:
        """Detect using RapidFuzz"""
        if not self.rapidfuzz_available or not self._dealer_names:
            return None
        
        try:
            from rapidfuzz import fuzz, process
            
            # Check for dealer names
            results = process.extract(
                cleaned,
                self._dealer_normalized,
                scorer=fuzz.WRatio,
                limit=3
            )
            
            if results and results[0][1] >= 80:
                best_match, score, idx = results[0]
                return {
                    "entity": self._dealer_names[idx],
                    "score": score / 100.0,
                    "intent": "dealer_dashboard"
                }
        except Exception as e:
            self.logger.warning(f"RapidFuzz detection failed: {e}")
        
        return None
    
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
        if not dealer_name or not self.rapidfuzz_available:
            return None
        
        self._load_dealer_cache()
        if not self._dealer_names:
            return None
        
        normalized = self._normalize_name(dealer_name)
        
        # Exact match
        for i, name in enumerate(self._dealer_names):
            if self._dealer_normalized[i] == normalized:
                return name
        
        # Contains match
        for i, name in enumerate(self._dealer_names):
            if normalized in self._dealer_normalized[i] or self._dealer_normalized[i] in normalized:
                return name
        
        # Fuzzy match
        try:
            from rapidfuzz import fuzz, process
            results = process.extract(
                normalized,
                self._dealer_normalized,
                scorer=fuzz.ratio,
                limit=1
            )
            
            if results and results[0][1] >= 75:
                best_match, score, idx = results[0]
                return self._dealer_names[idx]
        except Exception as e:
            self.logger.warning(f"RapidFuzz matching failed: {e}")
        
        return None
    
    def _find_similar_dealers(self, dealer_name: str, limit: int = 5) -> List[str]:
        """Find similar dealers using RapidFuzz"""
        if not dealer_name or not self.rapidfuzz_available:
            return []
        
        self._load_dealer_cache()
        if not self._dealer_names:
            return []
        
        normalized = self._normalize_name(dealer_name)
        results = []
        
        # Exact and contains matches
        for i, name in enumerate(self._dealer_names):
            if normalized == self._dealer_normalized[i] or \
               normalized in self._dealer_normalized[i] or \
               self._dealer_normalized[i] in normalized:
                results.append(name)
        
        # Fuzzy matches
        if len(results) < limit:
            try:
                from rapidfuzz import fuzz, process
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
    
    def _calculate_combined_score(self, pattern_score: float, semantic_score: float, flashrank_score: float) -> float:
        """Calculate combined confidence score"""
        weights = {'pattern': 0.4, 'semantic': 0.3, 'flashrank': 0.3}
        combined = (
            pattern_score * weights['pattern'] +
            semantic_score * weights['semantic'] +
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
            'warehouse_dashboard': 'warehouse',
            'city_dashboard': 'city',
            'product_dashboard': 'product',
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
            'warehouse_dashboard': 'get_warehouse_dashboard',
            'city_dashboard': 'get_city_dashboard',
            'product_dashboard': 'get_product_dashboard',
            'help': 'process_query',
            'greeting': 'process_query',
            'conversational': 'process_query',
            'general_ai': 'process_query'
        }
        return method_map.get(intent, 'process_query')
    
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
        """Finalize decision with caching"""
        if self._cache_enabled():
            self._cache[cache_key] = decision
        self.logger.info(f"🎯 Final Intent: {decision.intent} (confidence: {decision.confidence:.2f})")
        return decision
    
    def _cache_enabled(self) -> bool:
        """Check if caching is enabled"""
        return CACHETOOLS_AVAILABLE and hasattr(self, '_cache')
    
    def clear_cache(self):
        """Clear cache"""
        if self._cache_enabled():
            self._cache.clear()
            self.logger.info("✅ Cache cleared")

# ============================================================
# SERVICE REGISTRY
# ============================================================

class ServiceRegistry:
    SERVICES = {
        "dn": {
            "module": "app.services.dn_service",
            "class_name": "DNAnalysisService",
            "fallback_class_names": ["DNService", "DnService"],
            "expected_methods": ["get_dn_dashboard", "get_pending_dns"],
            "description": "DN Analytics Service",
        },
        "dealer": {
            "module": "app.services.dealer_service",
            "class_name": "DealerAnalyticsService",
            "fallback_class_names": ["DealerService"],
            "expected_methods": ["get_dealer_dashboard", "get_top_dealers"],
            "description": "Dealer Analytics Service",
        },
        "warehouse": {
            "module": "app.services.warehouse_service",
            "class_name": "WarehouseAnalyticsService",
            "fallback_class_names": ["WarehouseService"],
            "expected_methods": ["get_warehouse_dashboard"],
            "description": "Warehouse Analytics Service",
        },
        "city": {
            "module": "app.services.city_service",
            "class_name": "CityAnalyticsService",
            "fallback_class_names": ["CityService"],
            "expected_methods": ["get_city_dashboard"],
            "description": "City Analytics Service",
        },
        "product": {
            "module": "app.services.product_service",
            "class_name": "ProductAnalyticsService",
            "fallback_class_names": ["ProductService"],
            "expected_methods": ["get_product_dashboard"],
            "description": "Product Analytics Service",
        },
        "groq": {
            "module": "app.services.groq_service",
            "class_name": "GroqService",
            "fallback_class_names": ["GroqService"],
            "expected_methods": ["process_query"],
            "description": "Groq AI Service",
        }
    }
    
    def __init__(self):
        self._instance_cache = {}
        self._lock = threading.RLock()
        self._service_health = {}
    
    def get_service_instance(self, service_key: str):
        """Get service instance with caching and fallback"""
        if service_key in self._instance_cache:
            return self._instance_cache[service_key]
        
        with self._lock:
            try:
                service_def = self.SERVICES.get(service_key)
                if not service_def:
                    logger.error(f"Service '{service_key}' not registered")
                    return None
                
                module = importlib.import_module(service_def["module"])
                
                # Try primary class
                cls = None
                class_name = service_def["class_name"]
                if hasattr(module, class_name):
                    cls = getattr(module, class_name)
                else:
                    # Try fallback classes
                    for fallback in service_def.get("fallback_class_names", []):
                        if hasattr(module, fallback):
                            cls = getattr(module, fallback)
                            break
                
                if not cls:
                    logger.error(f"❌ No matching class in {service_def['module']}")
                    return None
                
                instance = cls()
                
                # Validate methods
                for method in service_def.get("expected_methods", []):
                    if not hasattr(instance, method):
                        logger.warning(f"⚠️ Service '{service_key}' missing method: {method}")
                
                self._instance_cache[service_key] = instance
                self._service_health[service_key] = {"loaded": True, "class": cls.__name__}
                logger.info(f"✅ Service '{service_key}' initialized")
                return instance
                
            except Exception as e:
                logger.error(f"❌ Failed to load '{service_key}': {e}")
                return None

# ============================================================
# WHATSAPP PROVIDER SERVICE
# ============================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v10.4 - ENTERPRISE ARCHITECTURE")
            logger.info("=" * 70)
            
            self.registry = ServiceRegistry()
            logger.info("✅ ServiceRegistry initialized")
            
            self.intent_engine = IntentDetectionEngine()
            logger.info("✅ IntentDetectionEngine initialized")
            
            # Load all services
            self.dn_service = self.registry.get_service_instance("dn")
            self.dealer_service = self.registry.get_service_instance("dealer")
            self.warehouse_service = self.registry.get_service_instance("warehouse")
            self.city_service = self.registry.get_service_instance("city")
            self.product_service = self.registry.get_service_instance("product")
            self.groq_service = self.registry.get_service_instance("groq")
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info("   ROUTING: DN → dn_service, Dealer → dealer_service")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process WhatsApp query - ENTRY POINT"""
        request_id = str(uuid.uuid4())[:8]
        logger.info(f"📩 [REQ:{request_id}] Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"🎯 [REQ:{request_id}] Intent: {routing_decision.intent}, Service: {routing_decision.service_key}")
            
            # Route based on service_key
            service_map = {
                "dn": self._handle_dn,
                "dealer": self._handle_dealer,
                "warehouse": self._handle_warehouse,
                "city": self._handle_city,
                "product": self._handle_product,
                "groq": self._handle_groq,
            }
            
            handler = service_map.get(routing_decision.service_key, self._handle_groq)
            result = await handler(routing_decision, request_id)
            
            if result:
                return result
            else:
                return self._format_response(
                    message,
                    f"⚠️ {routing_decision.service_key.title()} service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
        except Exception as e:
            logger.exception(f"❌ [REQ:{request_id}] Failed: {e}")
            return self._format_response(
                message,
                f"⚠️ An unexpected error occurred. Reference: {request_id}",
                error=True,
                request_id=request_id
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ [REQ:{request_id}] Response time: {elapsed_ms:.2f}ms")
    
    async def _handle_dn(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle DN"""
        if not self.dn_service:
            return None
        try:
            result = self.dn_service.get_dn_dashboard(decision.entity)
            if result and result.get("success"):
                return self._format_response(decision.original_message, result.get("data"), error=False, request_id=request_id)
            return self._format_response(decision.original_message, result.get("whatsapp_message"), error=True, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] DN handler failed: {e}")
            return None
    
    async def _handle_dealer(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Dealer"""
        if not self.dealer_service:
            return None
        try:
            entity = decision.entity or decision.original_message
            result = self.dealer_service.get_dealer_dashboard(entity)
            if result and result.get("success"):
                return self._format_response(decision.original_message, result.get("data"), error=False, request_id=request_id)
            return self._format_response(decision.original_message, result.get("whatsapp_message"), error=True, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Dealer handler failed: {e}")
            return None
    
    async def _handle_warehouse(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Warehouse"""
        if not self.warehouse_service:
            return None
        try:
            result = self.warehouse_service.get_warehouse_dashboard(decision.entity)
            if result and result.get("success"):
                return self._format_response(decision.original_message, result.get("data"), error=False, request_id=request_id)
            return self._format_response(decision.original_message, result.get("whatsapp_message"), error=True, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Warehouse handler failed: {e}")
            return None
    
    async def _handle_city(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle City"""
        if not self.city_service:
            return None
        try:
            result = self.city_service.get_city_dashboard(decision.entity)
            if result and result.get("success"):
                return self._format_response(decision.original_message, result.get("data"), error=False, request_id=request_id)
            return self._format_response(decision.original_message, result.get("whatsapp_message"), error=True, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] City handler failed: {e}")
            return None
    
    async def _handle_product(self, decision: RoutingDecision, request_id: str) -> Optional[Dict[str, Any]]:
        """Handle Product"""
        if not self.product_service:
            return None
        try:
            result = self.product_service.get_product_dashboard(decision.entity)
            if result and result.get("success"):
                return self._format_response(decision.original_message, result.get("data"), error=False, request_id=request_id)
            return self._format_response(decision.original_message, result.get("whatsapp_message"), error=True, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Product handler failed: {e}")
            return None
    
    async def _handle_groq(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Groq"""
        try:
            if self.groq_service:
                response = await self.groq_service.process_query(decision.original_message)
                if response:
                    return self._format_response(decision.original_message, response.get("response"), error=False, request_id=request_id)
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Groq failed: {e}")
        
        # Fallback responses
        fallback_responses = {
            "help": "📋 **Available Commands**\n\n📦 **DN Queries:** • Send any 8-12 digit number • 'Pending DN'\n\n🏪 **Dealer Queries:** • Send a dealer name • 'Top dealers'\n\n🤖 **General:** • 'Hello', 'Hi' • 'Help', 'Menu'",
            "greeting": "👋 **Hello! Welcome to Sham Electronics**\n\nI'm your AI assistant. I can help you with:\n\n📦 **DN Tracking** - Send any 8-12 digit number\n🏪 **Dealer Analytics** - Send a dealer name\n\nType **Help** for all commands."
        }
        
        return self._format_response(
            decision.original_message,
            fallback_responses.get(decision.intent, "I'm here to help! Try sending a DN number or dealer name."),
            error=False,
            request_id=request_id
        )
    
    def _format_response(self, original_message: str, data: Any, error: bool = False, request_id: Optional[str] = None) -> Dict[str, Any]:
        """Format response"""
        if hasattr(data, "to_whatsapp_message"):
            try:
                data = data.to_whatsapp_message()
            except:
                pass
        
        if isinstance(data, dict):
            for key in ("whatsapp_message", "formatted_response", "response", "message"):
                if data.get(key) not in (None, ""):
                    data = data[key]
                    break
        
        return {
            "success": not error,
            "message": original_message,
            "response": data,
            "error": error,
            "timestamp": datetime.now().isoformat(),
            "request_id": request_id
        }

# ============================================================
# SINGLETON
# ============================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService initialized (v10.4)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    return _whatsapp_provider_service

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'RoutingDecision',
    'IntentDetectionEngine'
]

logger.info("=" * 70)
logger.info("AI Provider Service v10.4 - ENTERPRISE ARCHITECTURE")
logger.info("=" * 70)
logger.info("✅ spaCy → Entity Extraction")
logger.info("✅ RapidFuzz → Fuzzy Matching")
logger.info("✅ SentenceTransformer → Semantic Detection")
logger.info("✅ FlashRank → Re-ranking")
logger.info("✅ All services: DN, Dealer, Warehouse, City, Product, Groq")
logger.info("=" * 70)
