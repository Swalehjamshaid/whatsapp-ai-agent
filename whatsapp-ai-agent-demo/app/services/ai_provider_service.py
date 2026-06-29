"""
File: app/services/ai_provider_service_intents.py
Version: 1.0 - ENTERPRISE INTENT DETECTION ENGINE
Purpose: PURE intent detection - NO business logic, NO SQL, NO formatting
         Uses: spaCy, SentenceTransformer, RapidFuzz, FlashRank
"""

import logging
import re
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

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
    from cachetools import TTLCache
    CACHETOOLS_AVAILABLE = True
except ImportError:
    CACHETOOLS_AVAILABLE = False
    TTLCache = None

# ============================================================
# ROUTING DECISION
# ============================================================

@dataclass
class RoutingDecision:
    """
    PURE ROUTING DECISION - NO business logic
    
    This class ONLY contains routing information.
    It does NOT contain business data, KPIs, or analytics.
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
    
    # Detection metadata (NOT business data)
    detection_time_ms: float = 0.0
    detection_method: str = "unknown"
    pattern_score: float = 0.0
    semantic_score: float = 0.0
    fuzzy_score: float = 0.0
    flashrank_score: float = 0.0
    combined_score: float = 0.0
    
    # Entity extraction (NOT business data)
    extracted_entities: Dict[str, List[str]] = field(default_factory=dict)
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
            "original_message": self.original_message[:100],
            "suggestions": self.suggestions,
            "detection_method": self.detection_method,
            "combined_score": self.combined_score,
            "extracted_entities": self.extracted_entities
        }

# ============================================================
# INTENT PATTERNS (PURE PATTERN MATCHING)
# ============================================================

class IntentPatterns:
    """Pure regex patterns for intent detection - NO business logic"""
    
    def __init__(self):
        # DN Patterns - detect numbers only
        self.DN_NUMBER = re.compile(r'\b(\d{8,12})\b')
        self.DN_PREFIX = re.compile(r'\b(DN|DN/)\s*(\d{6,10})\b', re.IGNORECASE)
        
        # Pending Patterns - detect intent only
        self.PENDING_DN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
        self.PENDING_PGI = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue|goods issuance)', re.IGNORECASE)
        self.PENDING_POD = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
        self.PENDING_GENERAL = re.compile(r'(?:pending|open|outstanding|waiting|delayed)', re.IGNORECASE)
        
        # Dealer Patterns - detect dealer references
        self.DEALER = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_DASHBOARD = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics|performance)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking Patterns - detect ranking intent
        self.RANKING = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom|leading|performance)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        
        # Comparison Patterns - detect comparison intent
        self.COMPARISON = re.compile(
            r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        
        # Location Patterns - detect location intent
        self.WAREHOUSE = re.compile(r'(?:warehouse|wh|depot|distribution|store)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
        self.CITY = re.compile(r'(?:city|in|at|location|region|area)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
        self.PRODUCT = re.compile(r'(?:product|model|material|item|sku|article|goods)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
        
        # National KPI - detect national intent
        self.NATIONAL_KPI = re.compile(
            r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard|company wide|corporate|head office)',
            re.IGNORECASE
        )
        
        # Conversational Patterns - detect conversational intent
        self.HELP = re.compile(r'(?:help|menu|commands|what can you do|available commands|how to use|guide|tutorial)', re.IGNORECASE)
        self.GREETING = re.compile(r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings|yo|sup)', re.IGNORECASE)
        self.CONVERSATIONAL = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|something|anything|what is|how to|how do|'
            r'where is|when is|why is|who is|explain|describe|tell about|'
            r'know about|information about|details about)',
            re.IGNORECASE
        )

# ============================================================
# ENTERPRISE INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    """
    PURE INTENT DETECTION ENGINE
    
    ONLY detects intent - NO business logic, NO SQL, NO formatting.
    Uses multi-strategy detection:
    1. Pattern matching (fastest)
    2. spaCy entity extraction
    3. SentenceTransformer semantic detection
    4. RapidFuzz fuzzy matching
    5. FlashRank re-ranking
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.start_time = time.time()
        self.patterns = IntentPatterns()
        
        # Dealer aliases (pure mapping, NOT business data)
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
        # 1. INITIALIZE SPACY (Entity Extraction ONLY)
        # ============================================================
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
        
        # ============================================================
        # 2. INITIALIZE SENTENCE TRANSFORMER (Semantic Detection ONLY)
        # ============================================================
        self.sentence_model = None
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self.sentence_model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
                self.logger.info("✅ SentenceTransformer loaded")
            except Exception as e:
                self.logger.warning(f"⚠️ SentenceTransformer init failed: {e}")
        
        # ============================================================
        # 3. INITIALIZE FLASHRANK (Re-ranking ONLY)
        # ============================================================
        self.ranker = None
        if FLASHRANK_AVAILABLE:
            try:
                self.ranker = Ranker(model="ms-marco-MiniLM-L-12-v2")
                self.logger.info("✅ FlashRank loaded")
            except Exception as e:
                self.logger.warning(f"⚠️ FlashRank init failed: {e}")
        
        # ============================================================
        # 4. SETUP CACHE (Pure caching, NO business data)
        # ============================================================
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # ============================================================
        # 5. INTENT EXAMPLES FOR SEMANTIC MATCHING (Pure examples)
        # ============================================================
        self._init_intent_examples()
        
        # ============================================================
        # 6. LOG SUMMARY
        # ============================================================
        init_time = (time.time() - self.start_time) * 1000
        self.logger.info("=" * 60)
        self.logger.info(f"✅ IntentDetectionEngine initialized in {init_time:.2f}ms")
        self.logger.info(f"   spaCy: {'✅' if self.nlp else '❌'} Entity Extraction ONLY")
        self.logger.info(f"   SentenceTransformer: {'✅' if self.sentence_model else '❌'} Semantic Detection ONLY")
        self.logger.info(f"   RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'} Fuzzy Matching ONLY")
        self.logger.info(f"   FlashRank: {'✅' if self.ranker else '❌'} Re-ranking ONLY")
        self.logger.info("=" * 60)
    
    def _init_intent_examples(self):
        """Initialize intent examples for semantic matching (PURE examples)"""
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
        PURE INTENT DETECTION - NO business logic
        
        Returns:
            RoutingDecision with ONLY routing information
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
        # STEP 2: SPACY ENTITY EXTRACTION (Entity detection ONLY)
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
            except Exception as e:
                self.logger.warning(f"spaCy processing failed: {e}")
        
        # ============================================================
        # STEP 3: SEMANTIC DETECTION (Semantic matching ONLY)
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
        # STEP 4: RAPIDFUZZ DEALER MATCHING (Fuzzy matching ONLY)
        # ============================================================
        fuzzy_result = None
        if RAPIDFUZZ_AVAILABLE:
            fuzzy_result = self._detect_with_rapidfuzz(cleaned)
            if fuzzy_result:
                self.logger.debug(f"RapidFuzz: {fuzzy_result.get('entity')} (score: {fuzzy_result.get('score', 0):.2f})")
        
        # ============================================================
        # STEP 5: FLASHRANK RE-RANKING (Re-ranking ONLY)
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
            
            final_decision = self._create_decision(
                intent=intent,
                service_key=self._get_service_key(intent),
                method=self._get_method(intent),
                entity=entity,
                confidence=best.get("confidence", 0.5),
                reason=f"Best candidate: {best.get('id', 'unknown')}",
                original=cleaned
            )
            
            # Add detection metadata
            final_decision.extracted_entities = spacy_entities
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
        """PURE pattern-based detection - NO business logic"""
        normalized = cleaned.lower()
        
        # 1. DN Detection (Pure number detection)
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
        
        dn_match = self.patterns.DN_NUMBER.search(cleaned)
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
        
        # 2. Pending Detection (Pure intent detection)
        if self.patterns.PENDING_DN.search(normalized):
            return self._create_decision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.98,
                reason="Pending DN query",
                original=cleaned
            )
        
        if self.patterns.PENDING_PGI.search(normalized):
            return self._create_decision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                reason="Pending PGI query",
                original=cleaned
            )
        
        if self.patterns.PENDING_POD.search(normalized):
            return self._create_decision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                reason="Pending POD query",
                original=cleaned
            )
        
        if self.patterns.PENDING_GENERAL.search(normalized):
            return self._create_decision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.80,
                reason="General pending query",
                original=cleaned
            )
        
        # 3. National KPI (Pure intent detection)
        if self.patterns.NATIONAL_KPI.search(normalized):
            return self._create_decision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                confidence=0.95,
                reason="National KPI query",
                original=cleaned
            )
        
        # 4. Ranking (Pure intent detection)
        ranking_match = self.patterns.RANKING.search(normalized)
        if ranking_match:
            if 'dealer' in normalized:
                if 'bottom' in normalized or 'worst' in normalized:
                    return self._create_decision(
                        intent="bottom_dealers",
                        service_key="dealer",
                        method="get_bottom_dealers",
                        confidence=0.90,
                        reason="Bottom dealers ranking",
                        original=cleaned
                    )
                else:
                    return self._create_decision(
                        intent="top_dealers",
                        service_key="dealer",
                        method="get_top_dealers",
                        confidence=0.90,
                        reason="Top dealers ranking",
                        original=cleaned
                    )
        
        # 5. Comparison (Pure intent detection)
        comparison_match = self.patterns.COMPARISON.search(cleaned)
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
        
        # 6. Dealer Detection (Pure pattern matching)
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
        
        dealer_name = None
        dashboard_match = self.patterns.DEALER_DASHBOARD.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
            dealer_name = self._clean_entity_name(dealer_name)
        
        if not dealer_name:
            dealer_match = self.patterns.DEALER.search(cleaned)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
                dealer_name = self._clean_entity_name(dealer_name)
        
        if not dealer_name and len(cleaned.split()) <= 4 and len(cleaned) > 2:
            if not re.match(r'^\d+$', cleaned):
                dealer_name = self._clean_entity_name(cleaned)
        
        if dealer_name and len(dealer_name) > 1:
            return self._create_decision(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=dealer_name,
                confidence=0.85,
                reason=f"Dealer name: {dealer_name}",
                original=cleaned
            )
        
        # 7. Location (Pure pattern matching)
        warehouse_match = self.patterns.WAREHOUSE.search(cleaned)
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
        
        city_match = self.patterns.CITY.search(cleaned)
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
        
        product_match = self.patterns.PRODUCT.search(cleaned)
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
        
        # 8. Conversational (Pure intent detection)
        if self.patterns.HELP.search(normalized):
            return self._create_decision(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Help query",
                original=cleaned
            )
        
        if self.patterns.GREETING.search(normalized):
            return self._create_decision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original=cleaned
            )
        
        if self.patterns.CONVERSATIONAL.search(normalized):
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
        """PURE fuzzy matching - NO business logic"""
        # This would need dealer data from database for matching
        # Simplified version - returns None if no data
        return None
    
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
            detection_method="combined"
        )
    
    def _create_fallback_decision(self, message: str, reason: str) -> RoutingDecision:
        return self._create_decision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason=reason,
            original=message
        )
    
    def _get_service_key(self, intent: str) -> str:
        """PURE mapping - NO business logic"""
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
        """PURE mapping - NO business logic"""
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
    
    def _is_dn_number(self, text: str) -> bool:
        """Pure number detection"""
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _clean_entity_name(self, name: str) -> Optional[str]:
        """Clean entity name - NO business logic"""
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
    
    def _calculate_combined_score(self, pattern_score: float, semantic_score: float, flashrank_score: float) -> float:
        """Calculate combined confidence score"""
        weights = {'pattern': 0.4, 'semantic': 0.3, 'flashrank': 0.3}
        combined = (
            pattern_score * weights['pattern'] +
            semantic_score * weights['semantic'] +
            flashrank_score * weights['flashrank']
        )
        return min(combined, 1.0)
    
    def _finalize_decision(self, decision: RoutingDecision, cache_key: str) -> RoutingDecision:
        """Finalize decision with caching"""
        if self._cache_enabled():
            self._cache[cache_key] = decision
        self.logger.info(f"🎯 Final Intent: {decision.intent} (confidence: {decision.confidence:.2f})")
        return decision
    
    def _cache_enabled(self) -> bool:
        return CACHETOOLS_AVAILABLE and hasattr(self, '_cache')
    
    def clear_cache(self):
        if self._cache_enabled():
            self._cache.clear()
            self.logger.info("✅ Cache cleared")

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'IntentDetectionEngine',
    'RoutingDecision',
    'SPACY_AVAILABLE',
    'SENTENCE_TRANSFORMERS_AVAILABLE',
    'RAPIDFUZZ_AVAILABLE',
    'FLASHRANK_AVAILABLE'
]

logger.info("=" * 70)
logger.info("Intent Detection Engine v1.0 - PURE INTENT DETECTION")
logger.info("=" * 70)
logger.info("✅ NO business logic")
logger.info("✅ NO SQL")
logger.info("✅ NO formatting")
logger.info("✅ ONLY intent detection")
logger.info("=" * 70)
