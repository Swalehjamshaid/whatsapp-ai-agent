"""
File: app/services/ai_provider_service_intents.py
Version: 3.1 - FIXED: Proper database imports and fallback
Purpose: Pure intent detection logic using spaCy, RapidFuzz, and caching
         NO business logic, NO SQL, NO formatting
"""

import re
import logging
import asyncio
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
import threading

logger = logging.getLogger(__name__)

# ============================================================
# LIBRARY IMPORTS WITH FALLBACK
# ============================================================

try:
    import spacy
    SPACY_AVAILABLE = True
    logger.info("✅ spaCy available for intent detection")
except ImportError:
    SPACY_AVAILABLE = False
    logger.warning("⚠️ spaCy not installed. Install with: pip install spacy>=3.8.2")

try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
    logger.info("✅ RapidFuzz available for fuzzy matching")
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("⚠️ RapidFuzz not installed. Install with: pip install rapidfuzz>=3.0.0")

try:
    from cachetools import TTLCache, cached
    CACHETOOLS_AVAILABLE = True
    logger.info("✅ Cachetools available for caching")
except ImportError:
    CACHETOOLS_AVAILABLE = False
    logger.warning("⚠️ Cachetools not installed. Install with: pip install cachetools>=5.0.0")

# ============================================================
# DATABASE IMPORTS WITH FALLBACK
# ============================================================

DB_AVAILABLE = False
try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    DB_AVAILABLE = True
    logger.info("✅ Database imports successful for intent detection")
except ImportError as e:
    logger.warning(f"⚠️ Database imports failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ============================================================
# ROUTING DECISION
# ============================================================

@dataclass
class RoutingDecision:
    """Routing decision for intent routing with enhanced metadata"""
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
    entities: Dict[str, Any] = field(default_factory=dict)
    intent_score: float = 0.0
    
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
            "entities": self.entities,
            "intent_score": self.intent_score
        }

# ============================================================
# INTENT PATTERNS
# ============================================================

class IntentPatterns:
    """Centralized pattern definitions with scoring weights"""
    
    # DN Patterns (weight: 1.0)
    DN_NUMBER = re.compile(r'\b(\d{8,12})\b')
    DN_PREFIX = re.compile(r'\b(DN|DN/)\s*(\d{6,10})\b', re.IGNORECASE)
    
    # Pending Patterns (weight: 0.95)
    PENDING_DN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
    PENDING_PGI = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue|goods issuance)', re.IGNORECASE)
    PENDING_POD = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
    PENDING_GENERAL = re.compile(r'(?:pending|open|outstanding|waiting|delayed)', re.IGNORECASE)
    
    # Dealer Patterns (weight: 0.9)
    DEALER = re.compile(
        r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    DEALER_DASHBOARD = re.compile(
        r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics|performance)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    DEALER_KEYWORDS = {'dealer', 'dealers', 'customer', 'customers', 'partner', 'partners', 'distributor', 'distributors'}
    
    # Ranking Patterns (weight: 0.85)
    RANKING = re.compile(
        r'(?:top|best|highest|lowest|worst|bottom|leading|performance)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
        re.IGNORECASE
    )
    RANKING_KEYWORDS = {'top', 'best', 'highest', 'lowest', 'worst', 'bottom', 'leading', 'ranking', 'rank'}
    
    # Comparison Patterns (weight: 0.85)
    COMPARISON = re.compile(
        r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
        re.IGNORECASE
    )
    COMPARISON_KEYWORDS = {'compare', 'comparison', 'vs', 'versus', 'difference', 'diff'}
    
    # Location Patterns (weight: 0.85)
    WAREHOUSE = re.compile(r'(?:warehouse|wh|depot|distribution|store)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    WAREHOUSE_KEYWORDS = {'warehouse', 'wh', 'depot', 'distribution', 'store'}
    
    CITY = re.compile(r'(?:city|in|at|location|region|area)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    CITY_KEYWORDS = {'city', 'cities', 'region', 'area', 'location'}
    
    PRODUCT = re.compile(r'(?:product|model|material|item|sku|article|goods)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    PRODUCT_KEYWORDS = {'product', 'products', 'model', 'models', 'material', 'sku', 'item', 'items'}
    
    # National KPIs (weight: 0.9)
    NATIONAL_KPI = re.compile(
        r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard|company wide|corporate|head office)',
        re.IGNORECASE
    )
    NATIONAL_KEYWORDS = {'national', 'pakistan', 'country', 'overall', 'executive', 'corporate', 'company wide'}
    
    # Conversational Patterns (weight: 0.7)
    HELP = re.compile(r'(?:help|menu|commands|what can you do|available commands|how to use|guide|tutorial)', re.IGNORECASE)
    GREETING = re.compile(r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings|yo|sup)', re.IGNORECASE)
    CONVERSATIONAL = re.compile(
        r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
        r'question|ask you|something|anything|what is|how to|how do|'
        r'where is|when is|why is|who is|explain|describe|tell about|'
        r'know about|information about|details about)',
        re.IGNORECASE
    )
    
    # Intent weights for scoring
    INTENT_WEIGHTS = {
        'dn_lookup': 1.0,
        'pending_dn': 0.98,
        'pending_pgi': 0.95,
        'pending_pod': 0.95,
        'national_kpi': 0.95,
        'dealer_dashboard': 0.90,
        'dealer_suggestion': 0.85,
        'top_dealers': 0.90,
        'bottom_dealers': 0.85,
        'comparison': 0.85,
        'warehouse_dashboard': 0.85,
        'city_dashboard': 0.85,
        'product_dashboard': 0.85,
        'help': 0.95,
        'greeting': 0.95,
        'conversational': 0.80,
        'general_ai': 0.50
    }

# ============================================================
# ENHANCED INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    """
    ENHANCED Intent Detection Engine using:
    - spaCy for NLP and entity extraction
    - RapidFuzz for fuzzy matching
    - Cachetools for caching results
    - Pattern-based detection with scoring
    """
    
    def __init__(self):
        self.patterns = IntentPatterns()
        self.logger = logging.getLogger(__name__)
        
        # Initialize spaCy
        self.nlp = None
        if SPACY_AVAILABLE:
            try:
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                except OSError:
                    try:
                        self.nlp = spacy.load("en_core_web_lg")
                    except OSError:
                        self.logger.warning("⚠️ spaCy model not found. Download with: python -m spacy download en_core_web_sm")
                        self.nlp = None
                
                if self.nlp:
                    self.logger.info("✅ spaCy NLP model loaded")
            except Exception as e:
                self.logger.warning(f"⚠️ spaCy initialization failed: {e}")
                self.nlp = None
        
        # Cache for intent detection
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # Dealer cache for fuzzy matching
        self._dealer_names = []
        self._dealer_normalized = []
        self._dealer_cache_loaded = False
        self._dealer_cache_lock = threading.RLock()
        
        # Intent tracking for analytics
        self._intent_stats = {}
        
        # Try to load dealer cache on init
        self._load_dealer_cache()
        
        self.logger.info("✅ Enhanced IntentDetectionEngine initialized")
        self.logger.info(f"   spaCy: {'✅ Available' if self.nlp else '❌ Not Available'}")
        self.logger.info(f"   RapidFuzz: {'✅ Available' if RAPIDFUZZ_AVAILABLE else '❌ Not Available'}")
        self.logger.info(f"   Cachetools: {'✅ Available' if CACHETOOLS_AVAILABLE else '❌ Not Available'}")
        self.logger.info(f"   Dealer Cache: {'✅ Loaded' if self._dealer_cache_loaded else '❌ Not Loaded'}")
        self.logger.info(f"   Dealers: {len(self._dealer_names)} loaded")
    
    # ============================================================
    # MAIN DETECT INTENT METHOD
    # ============================================================
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent from message with enhanced NLP and fuzzy matching.
        
        Args:
            message: User message
        
        Returns:
            RoutingDecision with intent and entities
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
        
        # Normalize for pattern matching
        normalized = cleaned.lower()
        self.logger.debug(f"Detecting intent for: {cleaned[:100]}")
        
        # Extract entities with spaCy
        entities = {}
        if self.nlp:
            try:
                doc = self.nlp(cleaned)
                entities = {
                    "PERSON": [ent.text for ent in doc.ents if ent.label_ == "PERSON"],
                    "ORG": [ent.text for ent in doc.ents if ent.label_ == "ORG"],
                    "GPE": [ent.text for ent in doc.ents if ent.label_ == "GPE"],
                    "DATE": [ent.text for ent in doc.ents if ent.label_ == "DATE"],
                    "MONEY": [ent.text for ent in doc.ents if ent.label_ == "MONEY"],
                    "QUANTITY": [ent.text for ent in doc.ents if ent.label_ == "QUANTITY"],
                    "PRODUCT": [ent.text for ent in doc.ents if ent.label_ == "PRODUCT"],
                }
                self.logger.debug(f"Extracted entities: {entities}")
            except Exception as e:
                self.logger.warning(f"spaCy entity extraction failed: {e}")
        
        # ============================================================
        # INTENT DETECTION WITH SCORING - PRIORITY ORDER
        # ============================================================
        
        decisions = []
        
        # 1. DN Intent (HIGHEST PRIORITY)
        dn_decision = self._detect_dn_intent(cleaned, normalized, entities)
        if dn_decision:
            decisions.append(dn_decision)
            self.logger.info(f"✅ DN Intent detected: {dn_decision.entity}")
            # DN has highest priority, return immediately
            return self._finalize_decision(dn_decision, entities, cache_key)
        
        # 2. Pending Intent
        pending_decision = self._detect_pending_intent(cleaned, normalized)
        if pending_decision:
            decisions.append(pending_decision)
        
        # 3. National KPI Intent
        national_decision = self._detect_national_intent(cleaned, normalized)
        if national_decision:
            decisions.append(national_decision)
        
        # 4. Ranking Intent
        ranking_decision = self._detect_ranking_intent(cleaned, normalized)
        if ranking_decision:
            decisions.append(ranking_decision)
        
        # 5. Comparison Intent
        comparison_decision = self._detect_comparison_intent(cleaned, normalized)
        if comparison_decision:
            decisions.append(comparison_decision)
        
        # 6. Dealer Intent
        dealer_decision = self._detect_dealer_intent(cleaned, normalized, entities)
        if dealer_decision:
            decisions.append(dealer_decision)
            self.logger.info(f"✅ Dealer Intent detected: {dealer_decision.entity}")
        
        # 7. Warehouse/City/Product Intent
        location_decision = self._detect_location_intent(cleaned, normalized)
        if location_decision:
            decisions.append(location_decision)
        
        # 8. Conversational Intent (LOWEST PRIORITY)
        conv_decision = self._detect_conversational_intent(cleaned, normalized)
        if conv_decision:
            decisions.append(conv_decision)
        
        # ============================================================
        # SELECT BEST DECISION
        # ============================================================
        
        if decisions:
            # Sort by confidence (highest first)
            decisions.sort(key=lambda d: d.confidence, reverse=True)
            best = decisions[0]
            return self._finalize_decision(best, entities, cache_key)
        
        # ============================================================
        # FALLBACK - If no intent detected, treat as DN or dealer fallback
        # ============================================================
        
        # Check if it looks like a DN number (even without pattern)
        if re.sub(r'\D', '', cleaned) and 8 <= len(re.sub(r'\D', '', cleaned)) <= 12:
            dn_number = re.sub(r'\D', '', cleaned)
            fallback = self._create_decision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=0.70,
                reason="DN number fallback",
                original=cleaned
            )
            return self._finalize_decision(fallback, entities, cache_key)
        
        # Check if it looks like a dealer name (2-3 words, not a number)
        if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
            fallback = self._create_decision(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=cleaned,
                confidence=0.50,
                reason="Dealer fallback",
                original=cleaned
            )
            return self._finalize_decision(fallback, entities, cache_key)
        
        # Final fallback to Groq
        fallback = self._create_fallback_decision(cleaned, "No intent detected")
        return self._finalize_decision(fallback, entities, cache_key)
    
    def _finalize_decision(self, decision: RoutingDecision, entities: Dict, cache_key: str) -> RoutingDecision:
        """Finalize decision with metadata and caching"""
        # Add entities to metadata
        decision.metadata['entities'] = entities
        decision.metadata['cache_hits'] = self._cache_hits
        
        # Cache result
        if self._cache_enabled():
            self._cache[cache_key] = decision
        
        self._track_intent(decision.intent)
        self.logger.info(f"🎯 Intent: {decision.intent} (confidence: {decision.confidence:.2f})")
        return decision
    
    # ============================================================
    # INTENT DETECTION METHODS
    # ============================================================
    
    def _detect_dn_intent(self, cleaned: str, normalized: str, entities: Dict) -> Optional[RoutingDecision]:
        """Detect DN-related intent"""
        # Pure DN number
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
        
        # DN with prefix
        dn_match = self.patterns.DN_PREFIX.search(cleaned)
        if dn_match:
            dn_number = dn_match.group(2)
            return self._create_decision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                reason="DN with prefix detected",
                original=cleaned
            )
        
        # DN number in text
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
        
        return None
    
    def _detect_pending_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect pending-related intent"""
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
        
        return None
    
    def _detect_national_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect national KPI intent"""
        if self.patterns.NATIONAL_KPI.search(normalized):
            return self._create_decision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                confidence=0.95,
                reason="National KPI query",
                original=cleaned
            )
        
        return None
    
    def _detect_ranking_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect ranking intent"""
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
            
            elif 'city' in normalized:
                return self._create_decision(
                    intent="top_cities",
                    service_key="city",
                    method="get_top_cities",
                    confidence=0.85,
                    reason="Top cities ranking",
                    original=cleaned
                )
            
            elif 'warehouse' in normalized or 'wh' in normalized:
                return self._create_decision(
                    intent="top_warehouses",
                    service_key="warehouse",
                    method="get_top_warehouses",
                    confidence=0.85,
                    reason="Top warehouses ranking",
                    original=cleaned
                )
            
            elif 'product' in normalized or 'item' in normalized:
                return self._create_decision(
                    intent="top_products",
                    service_key="product",
                    method="get_top_products",
                    confidence=0.85,
                    reason="Top products ranking",
                    original=cleaned
                )
        
        return None
    
    def _detect_comparison_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect comparison intent"""
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
        
        return None
    
    def _detect_dealer_intent(self, cleaned: str, normalized: str, entities: Dict) -> Optional[RoutingDecision]:
        """Detect dealer-related intent"""
        dealer_name = None
        
        # Try dashboard pattern
        dashboard_match = self.patterns.DEALER_DASHBOARD.search(cleaned)
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
                        confidence=0.95,
                        reason=f"Dealer dashboard: {found_dealer}",
                        original=cleaned
                    )
        
        # Try dealer pattern
        dealer_match = self.patterns.DEALER.search(cleaned)
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
        
        # Check if it's just a dealer name (short query)
        if len(cleaned.split()) <= 4 and len(cleaned) > 2:
            if not re.match(r'^\d+$', cleaned):
                dealer_name = self._clean_dealer_name(cleaned)
                if dealer_name:
                    found_dealer = self._find_dealer_fuzzy(dealer_name)
                    if found_dealer:
                        return self._create_decision(
                            intent="dealer_dashboard",
                            service_key="dealer",
                            method="get_dealer_dashboard",
                            entity=found_dealer,
                            confidence=0.85,
                            reason=f"Dealer from short query: {found_dealer}",
                            original=cleaned
                        )
        
        # Use spaCy entities
        if entities and entities.get("ORG"):
            for org in entities["ORG"]:
                found_dealer = self._find_dealer_fuzzy(org)
                if found_dealer:
                    return self._create_decision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=found_dealer,
                        confidence=0.85,
                        reason=f"Dealer from spaCy ORG entity: {found_dealer}",
                        original=cleaned
                    )
        
        return None
    
    def _detect_location_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect warehouse/city/product intent"""
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
        
        return None
    
    def _detect_conversational_intent(self, cleaned: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect conversational intent"""
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
                reason="Conversational question",
                original=cleaned
            )
        
        return None
    
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
        """Create routing decision with defaults"""
        base_weight = self.patterns.INTENT_WEIGHTS.get(intent, 0.5)
        final_confidence = min(confidence * base_weight, 1.0)
        
        return RoutingDecision(
            intent=intent,
            service_key=service_key,
            method=method,
            entity=entity,
            entity2=entity2,
            confidence=final_confidence,
            needs_groq=needs_groq,
            reason=reason,
            original_message=original,
            suggestions=suggestions or [],
            metadata=metadata or {},
            intent_score=final_confidence
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
        """Clean dealer name by removing common keywords"""
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
        
        return cleaned if len(cleaned) > 1 else None
    
    # ============================================================
    # FUZZY MATCHING WITH RAPIDFUZZ - FIXED WITH PROPER FALLBACK
    # ============================================================
    
    def _load_dealer_cache(self):
        """Load dealer names for fuzzy matching with proper error handling"""
        if self._dealer_cache_loaded:
            return
        
        with self._dealer_cache_lock:
            if self._dealer_cache_loaded:
                return
            
            try:
                if not DB_AVAILABLE or not SessionLocal or not DeliveryReport:
                    self.logger.warning("⚠️ Database not available for dealer cache")
                    return
                
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
        """Find dealer using fuzzy matching with RapidFuzz"""
        if not dealer_name:
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
            if normalized in self._dealer_normalized[i] or \
               self._dealer_normalized[i] in normalized:
                return name
        
        # Try fuzzy match with RapidFuzz
        if RAPIDFUZZ_AVAILABLE:
            try:
                results = process.extract(
                    normalized,
                    self._dealer_normalized,
                    scorer=fuzz.ratio,
                    limit=1
                )
                
                if results:
                    best_match, score, _ = results[0]
                    if score >= 75:  # Threshold for good match
                        idx = self._dealer_normalized.index(best_match)
                        return self._dealer_names[idx]
            except Exception as e:
                self.logger.warning(f"RapidFuzz matching failed: {e}")
        
        return None
    
    def _find_similar_dealers(self, dealer_name: str, limit: int = 5) -> List[str]:
        """Find similar dealers using RapidFuzz"""
        if not dealer_name:
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
        if len(results) < limit and RAPIDFUZZ_AVAILABLE:
            try:
                fuzzy_results = process.extract(
                    normalized,
                    self._dealer_normalized,
                    scorer=fuzz.ratio,
                    limit=limit
                )
                
                for match, score, idx in fuzzy_results:
                    if score >= 60:  # Lower threshold for suggestions
                        name = self._dealer_names[idx]
                        if name not in results:
                            results.append(name)
            except Exception as e:
                self.logger.warning(f"Similar matching failed: {e}")
        
        return results[:limit]
    
    # ============================================================
    # CACHE UTILITIES
    # ============================================================
    
    def _cache_enabled(self) -> bool:
        """Check if caching is enabled"""
        return CACHETOOLS_AVAILABLE and hasattr(self, '_cache')
    
    def clear_cache(self):
        """Clear intent detection cache"""
        if self._cache_enabled():
            self._cache.clear()
            self._cache_hits = 0
            self._cache_misses = 0
            self.logger.info("✅ Intent detection cache cleared")
    
    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics"""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache) if self._cache_enabled() else 0
        }
    
    # ============================================================
    # INTENT TRACKING
    # ============================================================
    
    def _track_intent(self, intent: str):
        """Track intent usage for analytics"""
        if intent not in self._intent_stats:
            self._intent_stats[intent] = 0
        self._intent_stats[intent] += 1
    
    def get_intent_stats(self) -> Dict[str, int]:
        """Get intent usage statistics"""
        return self._intent_stats.copy()
    
    # ============================================================
    # SYSTEM HEALTH
    # ============================================================
    
    def get_health(self) -> Dict[str, Any]:
        """Get health status of intent detection system"""
        return {
            "status": "healthy",
            "spacy_available": SPACY_AVAILABLE and self.nlp is not None,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
            "cache_available": self._cache_enabled(),
            "dealer_cache_loaded": self._dealer_cache_loaded,
            "dealer_count": len(self._dealer_names),
            "cache_stats": self.get_cache_stats(),
            "intent_stats": self._intent_stats,
            "db_available": DB_AVAILABLE
        }

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'IntentDetectionEngine',
    'RoutingDecision',
    'IntentPatterns',
    'SPACY_AVAILABLE',
    'RAPIDFUZZ_AVAILABLE',
    'CACHETOOLS_AVAILABLE'
]

logger.info("=" * 70)
logger.info("Intent Detection Engine v3.1 - FIXED")
logger.info("=" * 70)
logger.info(f"✅ spaCy: {'Available' if SPACY_AVAILABLE else 'Not Available'}")
logger.info(f"✅ RapidFuzz: {'Available' if RAPIDFUZZ_AVAILABLE else 'Not Available'}")
logger.info(f"✅ Cachetools: {'Available' if CACHETOOLS_AVAILABLE else 'Not Available'}")
logger.info(f"✅ Database: {'Available' if DB_AVAILABLE else 'Not Available'}")
logger.info("=" * 70)
