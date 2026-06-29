"""
File: app/services/ai_provider_service_intents.py
Version: 2.0 - ENTERPRISE INTENT DETECTION ENGINE
Purpose: PURE intent detection - NO business logic, NO SQL, NO formatting
         Uses: spaCy, SentenceTransformer, RapidFuzz, FlashRank
         Enterprise features: Entity Resolver, Confidence Engine, Strategy Pattern
"""

import logging
import re
import time
import threading
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from abc import ABC, abstractmethod

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
    """Entity types"""
    DEALER = "dealer"
    CITY = "city"
    WAREHOUSE = "warehouse"
    PRODUCT = "product"
    DN = "dn"
    UNKNOWN = "unknown"

class ServiceType(Enum):
    """Service types"""
    CRITICAL = "critical"
    OPTIONAL = "optional"
    AI = "ai"

class IntentStrategy(Enum):
    """Intent detection strategies"""
    PATTERN = "pattern"
    SPACY = "spacy"
    SEMANTIC = "semantic"
    RAPIDFUZZ = "rapidfuzz"
    FLASHRANK = "flashrank"
    FALLBACK = "fallback"

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ServiceMetadata:
    """Service metadata for routing decisions"""
    service_key: str
    method: str
    service_type: ServiceType = ServiceType.CRITICAL
    timeout_seconds: float = 5.0
    retry_count: int = 3
    cacheable: bool = True
    requires_database: bool = True
    requires_repository: bool = True
    requires_ai: bool = False
    priority: int = 1
    version: str = "1.0"
    expected_response: str = "structured"

@dataclass
class Entity:
    """Entity resolution result"""
    type: EntityType
    name: str
    original_text: str
    confidence: float
    normalized_name: str
    metadata: Dict[str, Any] = field(default_factory=dict)

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
    strategy: IntentStrategy = IntentStrategy.PATTERN
    extracted_entities: Dict[str, List[str]] = field(default_factory=dict)

@dataclass
class RoutingDecision:
    """
    ENTERPRISE ROUTING DECISION - NO business logic
    
    Contains ONLY routing information with service metadata.
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
    
    # Service metadata
    service_metadata: Optional[ServiceMetadata] = None
    
    # Detection metadata
    detection_method: str = "unknown"
    combined_score: float = 0.0
    pattern_score: float = 0.0
    semantic_score: float = 0.0
    fuzzy_score: float = 0.0
    flashrank_score: float = 0.0
    entity_confidence: float = 0.0
    
    # Entity extraction
    extracted_entities: Dict[str, List[str]] = field(default_factory=dict)
    primary_entity: Optional[Entity] = None
    candidates: List[IntentCandidate] = field(default_factory=list)
    
    # Validation
    validated: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
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
            "service_metadata": {
                "service_type": self.service_metadata.service_type.value if self.service_metadata else None,
                "timeout": self.service_metadata.timeout_seconds if self.service_metadata else None,
                "retry_count": self.service_metadata.retry_count if self.service_metadata else None,
                "requires_database": self.service_metadata.requires_database if self.service_metadata else None
            } if self.service_metadata else {},
            "detection_method": self.detection_method,
            "combined_score": self.combined_score,
            "primary_entity": self.primary_entity.name if self.primary_entity else None,
            "validated": self.validated
        }

# ============================================================
# ROUTING CONFIGURATION (Embedded)
# ============================================================

class RoutingConfig:
    """Centralized routing configuration"""
    
    # Intent to route mapping
    INTENT_ROUTES = {
        "dn_lookup": ServiceMetadata("dn", "get_dn_dashboard", priority=10),
        "pending_dn": ServiceMetadata("dn", "get_pending_dns", priority=9),
        "pending_pgi": ServiceMetadata("dn", "get_pending_pgi", priority=9),
        "pending_pod": ServiceMetadata("dn", "get_pending_pod", priority=9),
        "dealer_dashboard": ServiceMetadata("dealer", "get_dealer_dashboard", priority=8),
        "dealer_suggestion": ServiceMetadata("dealer", "suggest_dealers", priority=7),
        "top_dealers": ServiceMetadata("dealer", "get_top_dealers", priority=8),
        "bottom_dealers": ServiceMetadata("dealer", "get_bottom_dealers", priority=8),
        "comparison": ServiceMetadata("dealer", "compare_dealers", priority=8),
        "warehouse_dashboard": ServiceMetadata("warehouse", "get_warehouse_dashboard", 
                                               priority=7, service_type=ServiceType.OPTIONAL),
        "city_dashboard": ServiceMetadata("city", "get_city_dashboard", 
                                          priority=7, service_type=ServiceType.OPTIONAL),
        "product_dashboard": ServiceMetadata("product", "get_product_dashboard", 
                                             priority=7, service_type=ServiceType.OPTIONAL),
        "national_kpi": ServiceMetadata("national_kpi", "get_national_kpi_dashboard", priority=8),
        "help": ServiceMetadata("groq", "process_query", priority=5, 
                                requires_database=False, requires_repository=False, requires_ai=True),
        "greeting": ServiceMetadata("groq", "process_query", priority=5,
                                    requires_database=False, requires_repository=False, requires_ai=True),
        "conversational": ServiceMetadata("groq", "process_query", priority=4,
                                          requires_database=False, requires_repository=False, requires_ai=True),
        "general_ai": ServiceMetadata("groq", "process_query", priority=1,
                                      requires_database=False, requires_repository=False, requires_ai=True)
    }
    
    # Confidence thresholds
    CONFIDENCE_THRESHOLDS = {
        "dn_lookup": 0.90,
        "pending_dn": 0.85,
        "pending_pgi": 0.85,
        "pending_pod": 0.85,
        "dealer_dashboard": 0.80,
        "top_dealers": 0.80,
        "bottom_dealers": 0.80,
        "comparison": 0.80,
        "warehouse_dashboard": 0.75,
        "city_dashboard": 0.75,
        "product_dashboard": 0.75,
        "national_kpi": 0.85,
        "help": 0.70,
        "greeting": 0.70,
        "conversational": 0.65,
        "general_ai": 0.30
    }
    
    @classmethod
    def get_route(cls, intent: str) -> Optional[ServiceMetadata]:
        return cls.INTENT_ROUTES.get(intent)
    
    @classmethod
    def get_threshold(cls, intent: str) -> float:
        return cls.CONFIDENCE_THRESHOLDS.get(intent, 0.50)
    
    @classmethod
    def validate_route(cls, intent: str, service_key: str, method: str) -> bool:
        route = cls.get_route(intent)
        if not route:
            return False
        return route.service_key == service_key and route.method == method

# ============================================================
# ENTITY RESOLVER LAYER
# ============================================================

class DealerAliasResolver:
    """Resolve dealer aliases - BUSINESS DATA MOVED HERE"""
    
    ALIASES = {
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
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self._cache = TTLCache(maxsize=500, ttl=3600) if CACHETOOLS_AVAILABLE else {}
    
    def resolve(self, text: str) -> Optional[Tuple[str, float]]:
        if not text:
            return None
        
        cache_key = f"alias:{text.lower()}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        text_lower = text.lower()
        for alias, full_name in self.ALIASES.items():
            if alias in text_lower or text_lower in alias:
                result = (full_name, 1.0)
                self._cache[cache_key] = result
                return result
        
        return None

class EntityResolver:
    """Enterprise Entity Resolver"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.alias_resolver = DealerAliasResolver()
        self._dealer_names = []
        self._dealer_normalized = []
        self._loaded = False
        self._lock = threading.RLock()
        self._cache = TTLCache(maxsize=1000, ttl=3600) if CACHETOOLS_AVAILABLE else {}
        self.logger.info("✅ EntityResolver initialized")
    
    def _load_dealers(self):
        if self._loaded:
            return
        
        with self._lock:
            if self._loaded:
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
                    self._dealer_normalized = [self._normalize(d) for d in self._dealer_names]
                    self._loaded = True
                    self.logger.info(f"✅ Loaded {len(self._dealer_names)} dealers")
                except Exception as e:
                    self.logger.warning(f"Failed to load dealers: {e}")
                finally:
                    session.close()
            except Exception as e:
                self.logger.warning(f"Failed to load dealers: {e}")
    
    def _normalize(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'[^\w\s]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip().lower()
    
    def resolve_dealer(self, text: str) -> Optional[Entity]:
        if not text:
            return None
        
        cache_key = f"dealer:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        # Try alias first
        alias_result = self.alias_resolver.resolve(text)
        if alias_result:
            name, confidence = alias_result
            entity = Entity(
                type=EntityType.DEALER,
                name=name,
                original_text=text,
                confidence=confidence,
                normalized_name=self._normalize(name)
            )
            self._cache[cache_key] = entity
            return entity
        
        self._load_dealers()
        if not self._dealer_names:
            return None
        
        normalized = self._normalize(text)
        
        # Exact match
        for i, name in enumerate(self._dealer_names):
            if self._dealer_normalized[i] == normalized:
                entity = Entity(
                    type=EntityType.DEALER,
                    name=name,
                    original_text=text,
                    confidence=1.0,
                    normalized_name=normalized
                )
                self._cache[cache_key] = entity
                return entity
        
        # Contains match
        for i, name in enumerate(self._dealer_names):
            if normalized in self._dealer_normalized[i] or self._dealer_normalized[i] in normalized:
                entity = Entity(
                    type=EntityType.DEALER,
                    name=name,
                    original_text=text,
                    confidence=0.90,
                    normalized_name=normalized
                )
                self._cache[cache_key] = entity
                return entity
        
        # Fuzzy match
        if RAPIDFUZZ_AVAILABLE:
            try:
                results = process.extract(
                    normalized,
                    self._dealer_normalized,
                    scorer=fuzz.ratio,
                    limit=5
                )
                
                if results and results[0][1] >= 75:
                    best_match, score, idx = results[0]
                    entity = Entity(
                        type=EntityType.DEALER,
                        name=self._dealer_names[idx],
                        original_text=text,
                        confidence=score / 100.0,
                        normalized_name=best_match
                    )
                    self._cache[cache_key] = entity
                    return entity
            except Exception as e:
                self.logger.warning(f"Fuzzy matching failed: {e}")
        
        return None
    
    def get_suggestions(self, text: str, limit: int = 5) -> List[Entity]:
        self._load_dealers()
        if not self._dealer_names:
            return []
        
        normalized = self._normalize(text)
        suggestions = []
        
        for i, name in enumerate(self._dealer_names):
            if normalized in self._dealer_normalized[i] or self._dealer_normalized[i] in normalized:
                if len(suggestions) < limit:
                    suggestions.append(Entity(
                        type=EntityType.DEALER,
                        name=name,
                        original_text=text,
                        confidence=0.80,
                        normalized_name=self._dealer_normalized[i]
                    ))
        
        if len(suggestions) < limit and RAPIDFUZZ_AVAILABLE:
            try:
                results = process.extract(
                    normalized,
                    self._dealer_normalized,
                    scorer=fuzz.ratio,
                    limit=limit
                )
                for match, score, idx in results:
                    if score >= 60:
                        suggestions.append(Entity(
                            type=EntityType.DEALER,
                            name=self._dealer_names[idx],
                            original_text=text,
                            confidence=score / 100.0,
                            normalized_name=match
                        ))
            except Exception:
                pass
        
        return suggestions[:limit]

# ============================================================
# CONFIDENCE ENGINE
# ============================================================

class ConfidenceEngine:
    """Dynamic confidence scoring engine"""
    
    # Dynamic weights based on strategy reliability
    STRATEGY_WEIGHTS = {
        IntentStrategy.PATTERN: 0.40,
        IntentStrategy.SEMANTIC: 0.25,
        IntentStrategy.RAPIDFUZZ: 0.15,
        IntentStrategy.SPACY: 0.10,
        IntentStrategy.FLASHRANK: 0.10
    }
    
    # Entity quality boost
    ENTITY_QUALITY_BOOST = 0.05
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def calculate_combined_score(
        self,
        candidates: List[IntentCandidate],
        primary_entity: Optional[Entity] = None
    ) -> float:
        """Calculate dynamic combined confidence score"""
        if not candidates:
            return 0.0
        
        # Group by strategy and take highest confidence
        strategy_scores = {}
        for candidate in candidates:
            strategy = candidate.strategy
            if strategy not in strategy_scores or candidate.confidence > strategy_scores[strategy]:
                strategy_scores[strategy] = candidate.confidence
        
        # Calculate weighted score
        weighted_sum = 0.0
        total_weight = 0.0
        
        for strategy, confidence in strategy_scores.items():
            weight = self.STRATEGY_WEIGHTS.get(strategy, 0.10)
            weighted_sum += confidence * weight
            total_weight += weight
        
        if total_weight == 0:
            return 0.0
        
        base_score = weighted_sum / total_weight
        
        # Entity quality boost
        if primary_entity and primary_entity.confidence > 0.8:
            base_score += self.ENTITY_QUALITY_BOOST
        
        return min(base_score, 1.0)

# ============================================================
# INTENT STRATEGIES
# ============================================================

class IntentStrategyBase(ABC):
    """Base class for intent detection strategies"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    @abstractmethod
    def detect(self, text: str) -> Optional[IntentCandidate]:
        """Detect intent from text"""
        pass
    
    @abstractmethod
    def get_strategy_type(self) -> IntentStrategy:
        pass

class PatternStrategy(IntentStrategyBase):
    """Pattern-based intent detection"""
    
    def __init__(self):
        super().__init__()
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        self.PENDING_DN = re.compile(r'(?:pending|open|outstanding)\s*(?:dn|dns|delivery|deliveries)', re.IGNORECASE)
        self.PENDING_PGI = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue|goods issuance)', re.IGNORECASE)
        self.PENDING_POD = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
        self.HELP = re.compile(r'(?:help|menu|commands|what can you do|available commands)', re.IGNORECASE)
        self.GREETING = re.compile(r'^(?:hello|hi|hey|good morning|good evening|howdy|greetings)', re.IGNORECASE)
        self.CONVERSATIONAL = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|what is|how to|how do|where is|when is|why is|who is)',
            re.IGNORECASE
        )
        self.RANKING = re.compile(
            r'(?:top|best|highest|lowest|worst|bottom|leading)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
            re.IGNORECASE
        )
        self.COMPARISON = re.compile(
            r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        self.WAREHOUSE = re.compile(r'(?:warehouse|wh|depot|distribution|store)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
        self.CITY = re.compile(r'(?:city|in|at|location|region|area)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
        self.PRODUCT = re.compile(r'(?:product|model|material|item|sku|article|goods)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    
    def get_strategy_type(self) -> IntentStrategy:
        return IntentStrategy.PATTERN
    
    def _is_dn_number(self, text: str) -> bool:
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _clean_entity(self, name: str) -> Optional[str]:
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
    
    def detect(self, text: str) -> Optional[IntentCandidate]:
        cleaned = text.strip()
        normalized = cleaned.lower()
        
        # DN Detection
        if self._is_dn_number(cleaned):
            dn_number = re.sub(r'\D', '', cleaned)
            return IntentCandidate(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                reason="DN number detected",
                strategy=IntentStrategy.PATTERN
            )
        
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            return IntentCandidate(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_match.group(1),
                confidence=1.0,
                reason="DN number extracted",
                strategy=IntentStrategy.PATTERN
            )
        
        # Pending Detection
        if self.PENDING_DN.search(normalized):
            return IntentCandidate(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.98,
                reason="Pending DN query",
                strategy=IntentStrategy.PATTERN
            )
        
        if self.PENDING_PGI.search(normalized):
            return IntentCandidate(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                reason="Pending PGI query",
                strategy=IntentStrategy.PATTERN
            )
        
        if self.PENDING_POD.search(normalized):
            return IntentCandidate(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                reason="Pending POD query",
                strategy=IntentStrategy.PATTERN
            )
        
        # Ranking
        if self.RANKING.search(normalized):
            if 'dealer' in normalized:
                if 'bottom' in normalized or 'worst' in normalized:
                    return IntentCandidate(
                        intent="bottom_dealers",
                        service_key="dealer",
                        method="get_bottom_dealers",
                        confidence=0.90,
                        reason="Bottom dealers ranking",
                        strategy=IntentStrategy.PATTERN
                    )
                else:
                    return IntentCandidate(
                        intent="top_dealers",
                        service_key="dealer",
                        method="get_top_dealers",
                        confidence=0.90,
                        reason="Top dealers ranking",
                        strategy=IntentStrategy.PATTERN
                    )
        
        # Comparison
        comparison_match = self.COMPARISON.search(cleaned)
        if comparison_match:
            return IntentCandidate(
                intent="comparison",
                service_key="dealer",
                method="compare_dealers",
                entity=comparison_match.group(1).strip(),
                entity2=comparison_match.group(2).strip(),
                confidence=0.90,
                reason="Comparison detected",
                strategy=IntentStrategy.PATTERN
            )
        
        # Short entity detection
        if len(cleaned.split()) <= 4 and len(cleaned) > 2 and not re.match(r'^\d+$', cleaned):
            entity_name = self._clean_entity(cleaned)
            if entity_name and len(entity_name) > 1:
                # Check if it looks like a dealer
                if any(word in normalized for word in ['electronics', 'traders', 'enterprises', 'group', 'mart']):
                    return IntentCandidate(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=entity_name,
                        confidence=0.80,
                        reason="Short query - dealer name",
                        strategy=IntentStrategy.PATTERN
                    )
        
        # Location detection
        warehouse_match = self.WAREHOUSE.search(cleaned)
        if warehouse_match:
            return IntentCandidate(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse_match.group(1).strip(),
                confidence=0.90,
                reason="Warehouse detected",
                strategy=IntentStrategy.PATTERN
            )
        
        city_match = self.CITY.search(cleaned)
        if city_match:
            return IntentCandidate(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city_match.group(1).strip(),
                confidence=0.90,
                reason="City detected",
                strategy=IntentStrategy.PATTERN
            )
        
        product_match = self.PRODUCT.search(cleaned)
        if product_match:
            return IntentCandidate(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_match.group(1).strip(),
                confidence=0.90,
                reason="Product detected",
                strategy=IntentStrategy.PATTERN
            )
        
        # Conversational
        if self.HELP.search(normalized):
            return IntentCandidate(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Help query",
                strategy=IntentStrategy.PATTERN
            )
        
        if self.GREETING.search(normalized):
            return IntentCandidate(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                strategy=IntentStrategy.PATTERN
            )
        
        if self.CONVERSATIONAL.search(normalized):
            return IntentCandidate(
                intent="conversational",
                service_key="groq",
                method="process_query",
                confidence=0.85,
                needs_groq=True,
                reason="Conversational",
                strategy=IntentStrategy.PATTERN
            )
        
        return None

class SpaCyStrategy(IntentStrategyBase):
    """spaCy-based entity extraction"""
    
    def __init__(self):
        super().__init__()
        self.nlp = None
        try:
            if SPACY_AVAILABLE:
                try:
                    self.nlp = spacy.load("en_core_web_sm")
                except OSError:
                    try:
                        self.nlp = spacy.load("en_core_web_lg")
                    except OSError:
                        pass
        except Exception as e:
            self.logger.warning(f"spaCy init failed: {e}")
    
    def get_strategy_type(self) -> IntentStrategy:
        return IntentStrategy.SPACY
    
    def detect(self, text: str) -> Optional[IntentCandidate]:
        if not self.nlp:
            return None
        
        try:
            doc = self.nlp(text)
            extracted = {}
            
            for ent in doc.ents:
                if ent.label_ == "ORG":
                    if "dealer" not in extracted:
                        extracted["dealer"] = []
                    extracted["dealer"].append(ent.text)
                elif ent.label_ == "GPE":
                    if "city" not in extracted:
                        extracted["city"] = []
                    extracted["city"].append(ent.text)
                elif ent.label_ == "PRODUCT":
                    if "product" not in extracted:
                        extracted["product"] = []
                    extracted["product"].append(ent.text)
            
            if extracted.get("dealer"):
                return IntentCandidate(
                    intent="dealer_dashboard",
                    service_key="dealer",
                    method="get_dealer_dashboard",
                    entity=extracted["dealer"][0],
                    confidence=0.70,
                    reason=f"spaCy ORG entity: {extracted['dealer'][0]}",
                    strategy=IntentStrategy.SPACY,
                    extracted_entities=extracted
                )
            
            if extracted.get("city") and "city" in text.lower():
                return IntentCandidate(
                    intent="city_dashboard",
                    service_key="city",
                    method="get_city_dashboard",
                    entity=extracted["city"][0],
                    confidence=0.70,
                    reason=f"spaCy GPE entity: {extracted['city'][0]}",
                    strategy=IntentStrategy.SPACY,
                    extracted_entities=extracted
                )
            
            if extracted.get("product"):
                return IntentCandidate(
                    intent="product_dashboard",
                    service_key="product",
                    method="get_product_dashboard",
                    entity=extracted["product"][0],
                    confidence=0.70,
                    reason=f"spaCy PRODUCT entity: {extracted['product'][0]}",
                    strategy=IntentStrategy.SPACY,
                    extracted_entities=extracted
                )
        except Exception as e:
            self.logger.warning(f"spaCy detection failed: {e}")
        
        return None

class SemanticStrategy(IntentStrategyBase):
    """SentenceTransformer-based semantic detection"""
    
    def __init__(self):
        super().__init__()
        self.model = None
        self.intent_embeddings = {}
        self._init_model()
    
    def _init_model(self):
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            return
        
        try:
            self.model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
            
            intent_examples = {
                'dn_lookup': ["check DN", "delivery note", "DN number"],
                'pending_dn': ["pending deliveries", "open delivery notes", "outstanding DNs"],
                'dealer_dashboard': ["dealer performance", "dealer revenue", "dealer dashboard"],
                'top_dealers': ["top dealers", "best performing", "dealer ranking"],
                'help': ["help me", "available commands", "what can you do"]
            }
            
            import numpy as np
            for intent, examples in intent_examples.items():
                embeddings = self.model.encode(examples, convert_to_numpy=True)
                self.intent_embeddings[intent] = embeddings.mean(axis=0)
            
            self.logger.info("✅ Semantic strategy initialized")
        except Exception as e:
            self.logger.warning(f"Semantic init failed: {e}")
    
    def get_strategy_type(self) -> IntentStrategy:
        return IntentStrategy.SEMANTIC
    
    def detect(self, text: str) -> Optional[IntentCandidate]:
        if not self.model or not self.intent_embeddings:
            return None
        
        try:
            import numpy as np
            text_embedding = self.model.encode([text], convert_to_numpy=True)[0]
            
            similarities = {}
            for intent, embedding in self.intent_embeddings.items():
                text_norm = text_embedding / np.linalg.norm(text_embedding)
                intent_norm = embedding / np.linalg.norm(embedding)
                similarities[intent] = float(np.dot(text_norm, intent_norm))
            
            best_intent = max(similarities, key=similarities.get)
            best_score = similarities[best_intent]
            
            if best_score < 0.3:
                return None
            
            route_map = {
                'dn_lookup': ('dn', 'get_dn_dashboard'),
                'pending_dn': ('dn', 'get_pending_dns'),
                'dealer_dashboard': ('dealer', 'get_dealer_dashboard'),
                'top_dealers': ('dealer', 'get_top_dealers'),
                'help': ('groq', 'process_query')
            }
            
            service_key, method = route_map.get(best_intent, ('groq', 'process_query'))
            
            return IntentCandidate(
                intent=best_intent,
                service_key=service_key,
                method=method,
                confidence=best_score,
                reason=f"Semantic match: {best_intent}",
                strategy=IntentStrategy.SEMANTIC,
                needs_groq=best_intent in ['help']
            )
        except Exception as e:
            self.logger.warning(f"Semantic detection failed: {e}")
        
        return None

class RapidFuzzStrategy(IntentStrategyBase):
    """RapidFuzz-based fuzzy matching"""
    
    def __init__(self, entity_resolver: EntityResolver):
        super().__init__()
        self.entity_resolver = entity_resolver
    
    def get_strategy_type(self) -> IntentStrategy:
        return IntentStrategy.RAPIDFUZZ
    
    def detect(self, text: str) -> Optional[IntentCandidate]:
        # Use entity resolver for dealer matching
        entity = self.entity_resolver.resolve_dealer(text)
        if entity and entity.confidence >= 0.75:
            return IntentCandidate(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=entity.name,
                confidence=entity.confidence,
                reason=f"RapidFuzz dealer match: {entity.name}",
                strategy=IntentStrategy.RAPIDFUZZ
            )
        return None

class FallbackStrategy(IntentStrategyBase):
    """Fallback strategy"""
    
    def get_strategy_type(self) -> IntentStrategy:
        return IntentStrategy.FALLBACK
    
    def detect(self, text: str) -> Optional[IntentCandidate]:
        # Check if it looks like a DN number
        cleaned = re.sub(r'\D', '', text.strip())
        if cleaned and 8 <= len(cleaned) <= 12:
            return IntentCandidate(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=cleaned,
                confidence=0.60,
                reason="DN fallback",
                strategy=IntentStrategy.FALLBACK
            )
        
        # Check if it looks like a dealer name
        if len(text.split()) <= 4 and len(text) > 2 and not re.match(r'^\d+$', text):
            return IntentCandidate(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=text,
                confidence=0.40,
                reason="Dealer fallback",
                strategy=IntentStrategy.FALLBACK
            )
        
        return IntentCandidate(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="General fallback",
            strategy=IntentStrategy.FALLBACK
        )

# ============================================================
# ENTERPRISE INTENT DETECTION ENGINE
# ============================================================

class IntentDetectionEngine:
    """
    ENTERPRISE INTENT DETECTION ENGINE
    
    Features:
    - Strategy pattern for detection
    - Entity resolver layer
    - Confidence engine
    - FlashRank re-ranking
    - Route validation
    - Service metadata
    - Comprehensive caching
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.start_time = time.time()
        
        # ============================================================
        # 1. INITIALIZE ENTITY RESOLVER
        # ============================================================
        self.entity_resolver = EntityResolver()
        self.logger.info("✅ EntityResolver initialized")
        
        # ============================================================
        # 2. INITIALIZE DETECTION STRATEGIES
        # ============================================================
        self.strategies = [
            PatternStrategy(),
            SpaCyStrategy(),
            SemanticStrategy(),
            RapidFuzzStrategy(self.entity_resolver),
            FallbackStrategy()
        ]
        self.logger.info(f"✅ {len(self.strategies)} detection strategies initialized")
        
        # ============================================================
        # 3. INITIALIZE FLASHRANK
        # ============================================================
        self.ranker = None
        if FLASHRANK_AVAILABLE:
            try:
                self.ranker = Ranker(model="ms-marco-MiniLM-L-12-v2")
                self.logger.info("✅ FlashRank initialized")
            except Exception as e:
                self.logger.warning(f"FlashRank init failed: {e}")
        
        # ============================================================
        # 4. INITIALIZE CONFIDENCE ENGINE
        # ============================================================
        self.confidence_engine = ConfidenceEngine()
        self.logger.info("✅ ConfidenceEngine initialized")
        
        # ============================================================
        # 5. SETUP CACHE
        # ============================================================
        self._cache = TTLCache(maxsize=1000, ttl=300) if CACHETOOLS_AVAILABLE else {}
        self._cache_hits = 0
        self._cache_misses = 0
        
        # ============================================================
        # 6. LOG SUMMARY
        # ============================================================
        init_time = (time.time() - self.start_time) * 1000
        self.logger.info("=" * 60)
        self.logger.info(f"✅ IntentDetectionEngine initialized in {init_time:.2f}ms")
        self.logger.info(f"   Strategies: {len(self.strategies)}")
        self.logger.info(f"   spaCy: {'✅' if SPACY_AVAILABLE else '❌'}")
        self.logger.info(f"   SentenceTransformer: {'✅' if SENTENCE_TRANSFORMERS_AVAILABLE else '❌'}")
        self.logger.info(f"   RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
        self.logger.info(f"   FlashRank: {'✅' if self.ranker else '❌'}")
        self.logger.info("=" * 60)
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent using all strategies and return RoutingDecision
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
        # STEP 1: RUN ALL STRATEGIES
        # ============================================================
        candidates = []
        for strategy in self.strategies:
            try:
                result = strategy.detect(cleaned)
                if result:
                    candidates.append(result)
                    self.logger.debug(f"  {strategy.get_strategy_type().value}: {result.intent} ({result.confidence:.2f})")
            except Exception as e:
                self.logger.warning(f"Strategy {strategy.get_strategy_type().value} failed: {e}")
        
        if not candidates:
            return self._create_fallback_decision(cleaned, "No intent detected")
        
        # ============================================================
        # STEP 2: FLASHRANK RE-RANKING
        # ============================================================
        if self.ranker and len(candidates) > 1:
            try:
                passages = [
                    {"id": i, "text": f"{c.intent}: {c.entity or ''}", "meta": c}
                    for i, c in enumerate(candidates)
                ]
                results = self.ranker.rerank(query=cleaned, passages=passages)
                for result in results:
                    idx = result["id"]
                    if idx < len(candidates):
                        candidates[idx].confidence = result["score"]
            except Exception as e:
                self.logger.warning(f"FlashRank failed: {e}")
        
        # ============================================================
        # STEP 3: ENTITY RESOLUTION
        # ============================================================
        primary_entity = None
        for candidate in candidates:
            if candidate.entity and candidate.intent in ['dealer_dashboard', 'dealer_suggestion']:
                entity = self.entity_resolver.resolve_dealer(candidate.entity)
                if entity and entity.confidence > 0.7:
                    primary_entity = entity
                    candidate.entity = entity.name
                    candidate.confidence = max(candidate.confidence, entity.confidence)
                    break
        
        # ============================================================
        # STEP 4: CONFIDENCE ENGINE
        # ============================================================
        combined_score = self.confidence_engine.calculate_combined_score(candidates, primary_entity)
        
        # ============================================================
        # STEP 5: SELECT BEST CANDIDATE
        # ============================================================
        best = max(candidates, key=lambda x: x.confidence)
        
        # Apply confidence threshold
        threshold = RoutingConfig.get_threshold(best.intent)
        if best.confidence < threshold:
            self.logger.info(f"⚠️ Low confidence: {best.confidence:.2f} < {threshold:.2f}")
            best = next((c for c in candidates if c.intent == 'general_ai'), None) or best
        
        # ============================================================
        # STEP 6: CREATE ROUTING DECISION
        # ============================================================
        routing_decision = self._create_routing_decision(
            candidate=best,
            combined_score=combined_score,
            primary_entity=primary_entity,
            candidates=candidates,
            original_message=cleaned
        )
        
        # ============================================================
        # STEP 7: VALIDATE ROUTE
        # ============================================================
        routing_decision.validated = RoutingConfig.validate_route(
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
        # STEP 8: CACHE AND RETURN
        # ============================================================
        self._cache[cache_key] = routing_decision
        self.logger.info(f"🎯 Final: {routing_decision.intent} ({routing_decision.confidence:.2f})")
        return routing_decision
    
    def _create_routing_decision(
        self,
        candidate: IntentCandidate,
        combined_score: float,
        primary_entity: Optional[Entity],
        candidates: List[IntentCandidate],
        original_message: str
    ) -> RoutingDecision:
        """Create RoutingDecision from candidate"""
        # Get service metadata
        metadata = RoutingConfig.get_route(candidate.intent)
        
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
            service_metadata=metadata,
            detection_method=candidate.strategy.value,
            combined_score=combined_score,
            candidates=candidates,
            primary_entity=primary_entity,
            extracted_entities=candidate.extracted_entities or {}
        )
        
        # Add suggestions if dealer not found
        if candidate.intent == 'dealer_dashboard' and not primary_entity:
            suggestions = self.entity_resolver.get_suggestions(candidate.entity or original_message, limit=5)
            decision.suggestions = [s.name for s in suggestions]
        
        return decision
    
    def _create_fallback_decision(self, message: str, reason: str) -> RoutingDecision:
        """Create fallback routing decision"""
        candidate = IntentCandidate(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason=reason,
            strategy=IntentStrategy.FALLBACK
        )
        return self._create_routing_decision(
            candidate=candidate,
            combined_score=0.30,
            primary_entity=None,
            candidates=[candidate],
            original_message=message
        )
    
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
            "strategies": len(self.strategies),
            "spacy_available": SPACY_AVAILABLE,
            "sentence_transformers_available": SENTENCE_TRANSFORMERS_AVAILABLE,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE,
            "flashrank_available": FLASHRANK_AVAILABLE and self.ranker is not None,
            "cache_stats": self.get_cache_stats(),
            "entity_resolver": {
                "loaded": self.entity_resolver._loaded,
                "dealer_count": len(self.entity_resolver._dealer_names)
            }
        }

# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'IntentDetectionEngine',
    'RoutingDecision',
    'EntityResolver',
    'ServiceMetadata',
    'SPACY_AVAILABLE',
    'SENTENCE_TRANSFORMERS_AVAILABLE',
    'RAPIDFUZZ_AVAILABLE',
    'FLASHRANK_AVAILABLE'
]

logger.info("=" * 70)
logger.info("Intent Detection Engine v2.0 - ENTERPRISE")
logger.info("=" * 70)
logger.info("✅ Strategy Pattern - 5 detection strategies")
logger.info("✅ Entity Resolver Layer")
logger.info("✅ Confidence Engine")
logger.info("✅ FlashRank Re-ranking")
logger.info("✅ Route Validation")
logger.info("✅ Service Metadata")
logger.info("✅ Comprehensive Caching")
logger.info("✅ NO business logic, NO SQL, NO formatting")
logger.info("=" * 70)
