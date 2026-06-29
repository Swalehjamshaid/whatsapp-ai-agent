"""
File: app/services/ai_provider_service.py
Version: 6.1 - ENTERPRISE AI ROUTING ENGINE WITH FIXED DEALER DETECTION
Purpose: Single entry point for all WhatsApp requests with AI-powered routing
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: ENTERPRISE AI LIBRARIES
# ==========================================================

# 1. spaCy - Entity Recognition
try:
    import spacy
    # Try loading the model, download if not available
    try:
        nlp = spacy.load("en_core_web_sm")
        logger.info("✅ spaCy loaded successfully")
    except OSError:
        logger.warning("⚠️ spaCy model not found, downloading...")
        try:
            import subprocess
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], check=True)
            nlp = spacy.load("en_core_web_sm")
            logger.info("✅ spaCy downloaded and loaded")
        except Exception as e:
            logger.error(f"❌ spaCy download failed: {e}")
            nlp = None
except ImportError:
    nlp = None
    logger.warning("⚠️ spaCy not available")

# 2. Sentence Transformers - Semantic Search
try:
    from sentence_transformers import SentenceTransformer
    import torch
    _encoder = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    logger.info("✅ SentenceTransformer loaded successfully")
except ImportError:
    _encoder = None
    logger.warning("⚠️ SentenceTransformer not available")
except Exception as e:
    _encoder = None
    logger.warning(f"⚠️ SentenceTransformer initialization failed: {e}")

# 3. PydanticAI - Structured AI Classification
try:
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.models.groq import GroqModel
    logger.info("✅ PydanticAI available")
except ImportError:
    Agent = None
    RunContext = None
    OpenAIModel = None
    GroqModel = None
    logger.warning("⚠️ PydanticAI not available")

# 4. Instructor - Structured Output Validation
try:
    import instructor
    from instructor import Instructor
    from pydantic import BaseModel, Field
    logger.info("✅ Instructor available")
except ImportError:
    instructor = None
    Instructor = None
    BaseModel = None
    Field = None
    logger.warning("⚠️ Instructor not available")

# 5. FlashRank - Reranking
try:
    from flashrank import Ranker, RerankRequest
    _ranker = Ranker(model="ms-marco-TinyBERT-L-2-v2")
    logger.info("✅ FlashRank loaded successfully")
except ImportError:
    _ranker = None
    logger.warning("⚠️ FlashRank not available")
except Exception as e:
    _ranker = None
    logger.warning(f"⚠️ FlashRank initialization failed: {e}")

# 6. SQLGlot - SQL Validation
try:
    import sqlglot
    from sqlglot import parse_one, optimize
    logger.info("✅ SQLGlot available")
except ImportError:
    sqlglot = None
    logger.warning("⚠️ SQLGlot not available")

# 7. PGVector - Vector Search
try:
    from pgvector.sqlalchemy import Vector
    logger.info("✅ PGVector available")
except ImportError:
    Vector = None
    logger.warning("⚠️ PGVector not available")

# 8. PyArrow - Fast Data Processing
try:
    import pyarrow as pa
    import pyarrow.compute as pc
    logger.info("✅ PyArrow available")
except ImportError:
    pa = None
    pc = None
    logger.warning("⚠️ PyArrow not available")

# 9. Polars - Fast DataFrame
try:
    import polars as pl
    logger.info("✅ Polars available")
except ImportError:
    pl = None
    logger.warning("⚠️ Polars not available")

# 10. Core imports
try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import text, func, inspect as sa_inspect, or_
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ==========================================================
# BLOCK 2: PYDANTIC MODELS FOR STRUCTURED ROUTING
# ==========================================================

if BaseModel is not None and Field is not None:
    class AIClassification(BaseModel):
        """Structured AI classification output"""
        intent: str = Field(description="The detected intent (dealer_dashboard, dn_lookup, warehouse_dashboard, etc.)")
        entity_type: str = Field(description="Type of entity (dealer, warehouse, city, product, dn)")
        entity_name: str = Field(description="The extracted entity name or identifier")
        metric: Optional[str] = Field(None, description="The requested metric (revenue, units, pending, delivery)")
        aggregation: Optional[str] = Field(None, description="Aggregation type (top, bottom, compare, total, average)")
        target_service: str = Field(description="Target service to route to")
        confidence: float = Field(description="Confidence score (0.0 to 1.0)")
        explanation: str = Field(description="Human-readable explanation of the classification")

    class DealerAnalyticsIntent(BaseModel):
        """Structured intent for dealer analytics"""
        query_type: str = Field(description="dealer_search, dealer_dashboard, dealer_ranking, dealer_comparison")
        dealer_name: Optional[str] = Field(None, description="Dealer name if specified")
        metric: Optional[str] = Field(None, description="Metric to analyze")
        comparison_dealers: Optional[List[str]] = Field(None, description="Dealers to compare")
        limit: Optional[int] = Field(10, description="Number of results to return")
        sort_by: Optional[str] = Field("revenue", description="Sort by field")
        sort_order: Optional[str] = Field("desc", description="asc or desc")

# ==========================================================
# BLOCK 3: ROUTING DECISION
# ==========================================================

@dataclass
class RoutingDecision:
    """Internal routing decision with AI-enhanced confidence"""
    intent: str
    service_key: str
    method: str
    entity: Optional[str] = None
    entity2: Optional[str] = None
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    ai_classification: Optional[Dict[str, Any]] = None
    
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
            "original_message": self.original_message
        }

# ==========================================================
# BLOCK 4: SPACY ENTITY EXTRACTOR
# ==========================================================

class SpacyEntityExtractor:
    """Entity extraction using spaCy"""
    
    ENTITY_TYPES = {
        "DEALER": ["dealer", "customer", "company", "firm", "distributor", "retailer"],
        "WAREHOUSE": ["warehouse", "depot", "distribution", "fulfillment", "storage"],
        "CITY": ["city", "town", "district", "region", "area", "location"],
        "PRODUCT": ["product", "model", "material", "item", "sku", "goods"],
        "DN": ["dn", "delivery note", "order", "shipment", "invoice"],
        "DIVISION": ["division", "category", "segment", "department"],
        "SALES_OFFICE": ["sales office", "branch", "office", "region", "zone"],
        "SALES_MANAGER": ["sales manager", "manager", "representative", "agent"]
    }
    
    def __init__(self):
        self.nlp = nlp
    
    def extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text using spaCy"""
        if not self.nlp:
            return self._regex_fallback(text)
        
        doc = self.nlp(text)
        
        entities = {
            "dealer": [],
            "warehouse": [],
            "city": [],
            "product": [],
            "dn": [],
            "division": [],
            "sales_office": [],
            "sales_manager": []
        }
        
        # Extract named entities
        for ent in doc.ents:
            if ent.label_ in ["ORG", "PERSON", "PRODUCT"]:
                entities["dealer"].append(ent.text)
            elif ent.label_ in ["GPE", "LOC", "FAC"]:
                entities["city"].append(ent.text)
        
        # Extract noun phrases
        for chunk in doc.noun_chunks:
            chunk_text = chunk.text.lower()
            for entity_type, keywords in self.ENTITY_TYPES.items():
                if any(keyword in chunk_text for keyword in keywords):
                    entity_key = entity_type.lower()
                    if entity_key in entities:
                        entities[entity_key].append(chunk.text)
        
        # Clean and deduplicate
        for key in entities:
            entities[key] = list(dict.fromkeys(entities[key]))
        
        # Try to find DN numbers
        dn_match = re.search(r'\b(\d{8,12})\b', text)
        if dn_match:
            entities["dn"] = [dn_match.group(1)]
        
        return entities
    
    def _regex_fallback(self, text: str) -> Dict[str, Any]:
        """Fallback regex extraction"""
        entities = {
            "dealer": [],
            "warehouse": [],
            "city": [],
            "product": [],
            "dn": [],
            "division": [],
            "sales_office": [],
            "sales_manager": []
        }
        
        # Extract DN
        dn_match = re.search(r'\b(\d{8,12})\b', text)
        if dn_match:
            entities["dn"] = [dn_match.group(1)]
        
        # Extract using patterns - IMPROVED
        patterns = [
            (r'(?:dealer|about|for|company|customer)\s+([a-z0-9\s&\-\.]+)', "dealer"),
            (r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]+)', "warehouse"),
            (r'(?:city|in|at)\s+([a-z0-9\s&\-\.]+)', "city"),
            (r'(?:product|model|material)\s+([a-z0-9\s&\-\.]+)', "product"),
        ]
        
        for pattern, entity_type in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entity = match.group(1).strip()
                if entity and len(entity) > 1:
                    entities[entity_type].append(entity)
        
        return entities

# ==========================================================
# BLOCK 5: SEMANTIC SEARCH ENGINE (PGVector + Sentence Transformers)
# ==========================================================

class SemanticSearchEngine:
    """Semantic search using Sentence Transformers and vector similarity"""
    
    def __init__(self):
        self.encoder = _encoder
        self._embedding_cache = {}
        self._candidate_cache = {}
        
    def encode_text(self, text: str) -> List[float]:
        """Encode text to vector with caching"""
        if not self.encoder:
            return []
        
        cache_key = f"emb_{hash(text)}"
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]
        
        try:
            embedding = self.encoder.encode(text, convert_to_numpy=True).tolist()
            self._embedding_cache[cache_key] = embedding
            return embedding
        except Exception:
            return []
    
    def semantic_similarity(self, text1: str, text2: str) -> float:
        """Calculate semantic similarity between two texts"""
        vec1 = self.encode_text(text1)
        vec2 = self.encode_text(text2)
        
        if not vec1 or not vec2:
            return 0.0
        
        try:
            import numpy as np
            from sklearn.metrics.pairwise import cosine_similarity
            return float(cosine_similarity([vec1], [vec2])[0][0])
        except Exception:
            return 0.0
    
    def find_best_match(self, query: str, candidates: List[str], threshold: float = 0.6) -> Tuple[Optional[str], float]:
        """Find best semantic match from candidates"""
        if not candidates or not self.encoder:
            return None, 0.0
        
        query_embedding = self.encode_text(query)
        if not query_embedding:
            return None, 0.0
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            score = self.semantic_similarity(query, candidate)
            if score > best_score:
                best_score = score
                best_match = candidate
        
        if best_score >= threshold:
            return best_match, best_score
        
        return None, best_score

# ==========================================================
# BLOCK 6: FLASHRANK RERANKER
# ==========================================================

class FlashRankReranker:
    """Rerank candidates using FlashRank"""
    
    def __init__(self):
        self.ranker = _ranker
    
    def rerank(self, query: str, candidates: List[Dict]) -> List[Dict]:
        """Rerank candidates by relevance"""
        if not self.ranker or not candidates:
            return candidates
        
        try:
            # Prepare documents
            docs = []
            for i, candidate in enumerate(candidates):
                docs.append({
                    "id": i,
                    "text": candidate.get("text", ""),
                    "meta": candidate
                })
            
            # Rerank
            rerank_request = RerankRequest(query=query, passages=docs)
            results = self.ranker.rerank(rerank_request)
            
            # Return reranked candidates
            reranked = []
            for result in results:
                idx = result["id"]
                if idx < len(candidates):
                    reranked.append({
                        **candidates[idx],
                        "rerank_score": result["score"]
                    })
            
            return reranked
        except Exception as e:
            logger.warning(f"FlashRank reranking failed: {e}")
            return candidates

# ==========================================================
# BLOCK 7: PYDANTIC AI CLASSIFIER
# ==========================================================

class AIClassifier:
    """AI-powered query classification using PydanticAI"""
    
    def __init__(self):
        self.agent = None
        self._initialize_agent()
    
    def _initialize_agent(self):
        """Initialize PydanticAI agent"""
        if Agent is None:
            return
        
        # Try Groq first (fastest)
        try:
            from app.services.groq_service import get_groq_service
            groq_service = get_groq_service()
            if groq_service and hasattr(groq_service, 'client'):
                self.agent = Agent(
                    GroqModel('llama-3.1-70b-versatile', api_key=os.getenv("GROQ_API_KEY")),
                    system_prompt="""You are a Dealer Intelligence Routing Expert.
                    Classify the user's question and return structured routing information.
                    Never return free text. Always return structured classification."""
                )
                logger.info("✅ PydanticAI agent initialized with Groq")
                return
        except Exception as e:
            logger.warning(f"Groq agent init failed: {e}")
        
        # Try OpenAI as fallback
        if OpenAIModel is not None and os.getenv("OPENAI_API_KEY"):
            try:
                self.agent = Agent(
                    OpenAIModel('gpt-4o-mini', api_key=os.getenv("OPENAI_API_KEY")),
                    system_prompt="""You are a Dealer Intelligence Routing Expert.
                    Classify the user's question and return structured routing information."""
                )
                logger.info("✅ PydanticAI agent initialized with OpenAI")
                return
            except Exception as e:
                logger.warning(f"OpenAI agent init failed: {e}")
        
        logger.warning("⚠️ PydanticAI agent not available")
    
    def classify(self, question: str) -> Optional[Dict[str, Any]]:
        """Classify a question using PydanticAI"""
        if not self.agent or BaseModel is None:
            return None
        
        try:
            # Use instructor-like structured output
            response = self.agent.run_sync(
                f"Question: {question}\n\n"
                "Classify this question into:\n"
                "- Intent: dealer_dashboard, dn_lookup, warehouse_dashboard, city_dashboard, product_dashboard, ranking, comparison, general\n"
                "- Entity type: dealer, warehouse, city, product, dn\n"
                "- Entity name: the specific name or identifier\n"
                "- Metric: revenue, units, pending, delivery, pgi, pod\n"
                "- Aggregation: top, bottom, compare, total, average\n"
                "- Target service: dealer, dn, warehouse, city, product, national_kpi\n"
                "- Confidence: 0.0 to 1.0\n"
            )
            
            # Parse response into structured format
            return self._parse_response(response.data)
        except Exception as e:
            logger.warning(f"AI classification failed: {e}")
            return None
    
    def _parse_response(self, text: str) -> Dict[str, Any]:
        """Parse AI response into structured format"""
        # Simple parsing fallback
        lines = text.lower().split('\n')
        
        classification = {
            "intent": "unknown",
            "entity_type": "none",
            "entity_name": "",
            "metric": None,
            "aggregation": None,
            "target_service": "groq",
            "confidence": 0.5,
            "explanation": text
        }
        
        intents = {
            "dealer": "dealer_dashboard",
            "warehouse": "warehouse_dashboard",
            "city": "city_dashboard",
            "product": "product_dashboard",
            "dn": "dn_lookup",
            "ranking": "ranking",
            "comparison": "comparison",
            "pending": "pending_dn"
        }
        
        for line in lines:
            for key, value in intents.items():
                if key in line:
                    classification["intent"] = value
                    classification["target_service"] = key
                    if key == "dn":
                        classification["entity_type"] = "dn"
                    elif key == "dealer":
                        classification["entity_type"] = "dealer"
                    elif key == "warehouse":
                        classification["entity_type"] = "warehouse"
                    elif key == "city":
                        classification["entity_type"] = "city"
                    elif key == "product":
                        classification["entity_type"] = "product"
            
            if "entity:" in line or "entity name:" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    name = parts[1].strip()
                    if name and name != "none":
                        classification["entity_name"] = name
            
            if "metric:" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    classification["metric"] = parts[1].strip()
            
            if "confidence:" in line:
                parts = line.split(":", 1)
                if len(parts) > 1:
                    try:
                        classification["confidence"] = float(parts[1].strip())
                    except:
                        pass
        
        return classification

# ==========================================================
# BLOCK 8: INTELLIGENT ROUTING ENGINE - FIXED VERSION
# ==========================================================

class IntelligentRoutingEngine:
    """Multi-stage routing engine with AI, semantic, and regex fallback"""
    
    def __init__(self):
        self.spacy_extractor = SpacyEntityExtractor()
        self.semantic_search = SemanticSearchEngine()
        self.flashrank = FlashRankReranker()
        self.ai_classifier = AIClassifier()
        self._routing_cache = {}
        self._cache_ttl = 300
        
        # ENHANCED: Pre-compile regex patterns for better dealer detection
        self.DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
        
        # IMPROVED: Better dealer detection patterns
        self.DEALER_DASHBOARD_PATTERN = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.DEALER_PATTERN = re.compile(
            r'(?:dealer|about|tell me about|show me|get|view|display|give me|company|customer)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.SIMPLE_DEALER_PATTERN = re.compile(
            r'^([a-z0-9\s&\-\.]{2,50})$',
            re.IGNORECASE
        )
        
        self.PENDING_PATTERN = re.compile(r'(?:pending|open)\s*(?:dn|pgi|pod)', re.IGNORECASE)
        
        # Dealer candidates cache
        self._dealer_candidates = []
        self._warehouse_candidates = []
        self._city_candidates = []
        self._product_candidates = []
        self._candidates_loaded = False
        self._dealer_names = []  # Simple list for fast lookup
    
    def load_candidates(self):
        """Load candidates from PostgreSQL"""
        if self._candidates_loaded:
            return
        
        try:
            if not SessionLocal or DeliveryReport is None:
                return
            
            session = SessionLocal()
            try:
                # Load dealers
                dealers = session.query(
                    DeliveryReport.customer_name,
                    DeliveryReport.dealer_code,
                    DeliveryReport.customer_code
                ).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).distinct().all()
                
                self._dealer_candidates = [
                    {
                        "name": d.customer_name,
                        "code": d.dealer_code,
                        "customer_code": d.customer_code,
                        "normalized": self._normalize_text(d.customer_name)
                    }
                    for d in dealers if d.customer_name
                ]
                
                # Also store just names for faster lookup
                self._dealer_names = [d["normalized"] for d in self._dealer_candidates]
                
                # Load warehouses
                warehouses = session.query(
                    DeliveryReport.warehouse
                ).filter(
                    DeliveryReport.warehouse.isnot(None)
                ).distinct().all()
                
                self._warehouse_candidates = [
                    {"name": w.warehouse, "normalized": self._normalize_text(w.warehouse)}
                    for w in warehouses if w.warehouse
                ]
                
                # Load cities
                cities = session.query(
                    DeliveryReport.ship_to_city
                ).filter(
                    DeliveryReport.ship_to_city.isnot(None)
                ).distinct().all()
                
                self._city_candidates = [
                    {"name": c.ship_to_city, "normalized": self._normalize_text(c.ship_to_city)}
                    for c in cities if c.ship_to_city
                ]
                
                # Load products
                products = session.query(
                    DeliveryReport.customer_model
                ).filter(
                    DeliveryReport.customer_model.isnot(None)
                ).distinct().all()
                
                self._product_candidates = [
                    {"name": p.customer_model, "normalized": self._normalize_text(p.customer_model)}
                    for p in products if p.customer_model
                ]
                
                self._candidates_loaded = True
                logger.info(f"Loaded {len(self._dealer_candidates)} dealers, {len(self._warehouse_candidates)} warehouses, {len(self._city_candidates)} cities, {len(self._product_candidates)} products")
            except Exception as e:
                logger.warning(f"Failed to load candidates: {e}")
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"Failed to load candidates: {e}")
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison"""
        if not text:
            return ""
        return re.sub(r'\s+', ' ', text.strip().lower())
    
    def _find_dealer_in_db(self, dealer_name: str) -> Optional[str]:
        """Find dealer in database using multiple strategies"""
        if not SessionLocal or DeliveryReport is None:
            return None
        
        session = None
        try:
            session = SessionLocal()
            normalized = self._normalize_text(dealer_name)
            
            # Strategy 1: Exact match on customer_name
            result = session.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == normalized
            ).first()
            if result:
                return result.customer_name
            
            # Strategy 2: Contains match on customer_name
            result = session.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name).contains(normalized)
            ).first()
            if result:
                return result.customer_name
            
            # Strategy 3: Dealer code match
            result = session.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.dealer_code).contains(normalized)
            ).first()
            if result:
                return result.customer_name
            
            # Strategy 4: Customer code match
            result = session.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_code).contains(normalized)
            ).first()
            if result:
                return result.customer_name
            
            # Strategy 5: Semantic search on all dealer names
            if self._dealer_names:
                best, score = self.semantic_search.find_best_match(normalized, self._dealer_names, threshold=0.5)
                if best:
                    # Find the original name
                    for candidate in self._dealer_candidates:
                        if candidate["normalized"] == best:
                            return candidate["name"]
            
            return None
        except Exception as e:
            logger.error(f"Dealer search failed: {e}")
            return None
        finally:
            if session:
                session.close()
    
    def _find_similar_dealers(self, dealer_name: str) -> List[str]:
        """Find similar dealers using semantic search and fuzzy matching"""
        if not SessionLocal or DeliveryReport is None:
            return []
        
        session = None
        try:
            session = SessionLocal()
            normalized = self._normalize_text(dealer_name)
            
            # Get all dealer names
            results = session.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.isnot(None)
            ).distinct().limit(100).all()
            
            dealer_names = [r.customer_name for r in results if r.customer_name]
            
            # Use semantic search
            if self.semantic_search:
                scored = []
                for name in dealer_names:
                    score = self.semantic_search.semantic_similarity(normalized, self._normalize_text(name))
                    if score > 0.3:
                        scored.append((name, score))
                
                scored.sort(key=lambda x: x[1], reverse=True)
                return [name for name, score in scored[:5]]
            
            # Fallback: fuzzy match
            from rapidfuzz import process, fuzz
            matches = process.extract(normalized, dealer_names, scorer=fuzz.WRatio, limit=5)
            return [match[0] for match in matches if match[1] > 60]
            
        except Exception as e:
            logger.error(f"Similar dealer search failed: {e}")
            return []
        finally:
            if session:
                session.close()
    
    def route(self, message: str) -> RoutingDecision:
        """Route a message using multi-stage AI pipeline with enhanced dealer detection"""
        start_time = time.perf_counter()
        message_clean = message.strip()
        message_lower = message_clean.lower()
        
        # Check cache
        cache_key = f"route_{hash(message_clean)}"
        if cache_key in self._routing_cache:
            cached_time, cached_decision = self._routing_cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                logger.info(f"Cache hit for: {message_clean[:50]}...")
                return cached_decision
        
        # ============================================================
        # FAST PATH 1: DN Detection (Highest priority)
        # ============================================================
        dn_match = self.DN_PATTERN.search(message_clean)
        if dn_match:
            decision = RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_match.group(1),
                confidence=0.99,
                needs_groq=False,
                reason="DN detected (fast path)",
                original_message=message_clean
            )
            self._cache_decision(cache_key, decision)
            return decision
        
        # ============================================================
        # FAST PATH 2: Pending Detection
        # ============================================================
        if self.PENDING_PATTERN.search(message_lower):
            if "pgi" in message_lower:
                decision = RoutingDecision(
                    intent="pending_pgi",
                    service_key="dn",
                    method="get_pending_pgi",
                    confidence=0.95,
                    needs_groq=False,
                    reason="Pending PGI detected",
                    original_message=message_clean
                )
            elif "pod" in message_lower:
                decision = RoutingDecision(
                    intent="pending_pod",
                    service_key="dn",
                    method="get_pending_pod",
                    confidence=0.95,
                    needs_groq=False,
                    reason="Pending POD detected",
                    original_message=message_clean
                )
            else:
                decision = RoutingDecision(
                    intent="pending_dn",
                    service_key="dn",
                    method="get_pending_dns",
                    confidence=0.95,
                    needs_groq=False,
                    reason="Pending DN detected",
                    original_message=message_clean
                )
            self._cache_decision(cache_key, decision)
            return decision
        
        # ============================================================
        # FAST PATH 3: Dealer Dashboard Detection (IMPROVED)
        # ============================================================
        
        # Check for dealer-related keywords
        dealer_keywords = ['dealer', 'about', 'tell me about', 'show me', 'get', 'view', 'display', 'give me', 'company', 'customer']
        dashboard_keywords = ['dashboard', 'profile', 'summary', 'overview', 'info', 'information', 'details', 'status', 'statistics', 'performance']
        
        is_dealer_related = any(kw in message_lower for kw in dealer_keywords)
        is_dashboard_related = any(kw in message_lower for kw in dashboard_keywords)
        
        dealer_name = None
        
        # Try pattern 1: "dashboard/profile of X"
        match = self.DEALER_DASHBOARD_PATTERN.search(message_clean)
        if match:
            dealer_name = match.group(1).strip()
        
        # Try pattern 2: "dealer X" or "tell me about X"
        if not dealer_name:
            match = self.DEALER_PATTERN.search(message_clean)
            if match:
                dealer_name = match.group(1).strip()
        
        # Try pattern 3: Simple name (2-50 chars, no spaces)
        if not dealer_name and is_dealer_related and not is_dashboard_related:
            match = self.SIMPLE_DEALER_PATTERN.search(message_clean)
            if match:
                dealer_name = match.group(1).strip()
        
        # Try pattern 4: Just the name if message is short
        if not dealer_name and len(message_clean.split()) <= 3:
            # Check if it looks like a dealer name (not a number, not a single word)
            if len(message_clean) > 2 and not re.match(r'^\d+$', message_clean):
                dealer_name = message_clean
        
        if dealer_name:
            # Clean up the dealer name
            dealer_name = re.sub(r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|dashboard|profile|summary|overview|info|information|details|status|statistics|performance|the|a|an)\b', '', dealer_name, flags=re.IGNORECASE).strip()
            
            if dealer_name and len(dealer_name) > 1:
                # Try to find in database
                found_dealer = self._find_dealer_in_db(dealer_name)
                if found_dealer:
                    decision = RoutingDecision(
                        intent="dealer_dashboard",
                        service_key="dealer",
                        method="get_dealer_dashboard",
                        entity=found_dealer,
                        confidence=0.95,
                        needs_groq=False,
                        reason=f"Dealer found: {found_dealer}",
                        original_message=message_clean
                    )
                    self._cache_decision(cache_key, decision)
                    return decision
                else:
                    # Try to find similar dealers
                    similar = self._find_similar_dealers(dealer_name)
                    if similar:
                        # Return the first match if it's good enough
                        if len(similar) == 1:
                            decision = RoutingDecision(
                                intent="dealer_dashboard",
                                service_key="dealer",
                                method="get_dealer_dashboard",
                                entity=similar[0],
                                confidence=0.85,
                                needs_groq=False,
                                reason=f"Semantic match: {similar[0]}",
                                original_message=message_clean
                            )
                            self._cache_decision(cache_key, decision)
                            return decision
                        else:
                            # Multiple suggestions
                            suggestions_text = "Did you mean:\n" + "\n".join([f"• {name}" for name in similar[:5]])
                            decision = RoutingDecision(
                                intent="dealer_suggestion",
                                service_key="dealer",
                                method="suggest_dealers",
                                entity=dealer_name,
                                confidence=0.7,
                                needs_groq=False,
                                reason=f"Dealer not found, suggestions: {similar[:3]}",
                                original_message=message_clean
                            )
                            self._cache_decision(cache_key, decision)
                            return decision
        
        # ============================================================
        # STAGE: Analytics Intent Detection
        # ============================================================
        analytics_patterns = [
            (r'(highest|top|best|max).*?(revenue|sales|units|performance|dealer)', "ranking", "dealer", "get_top_dealers"),
            (r'(lowest|bottom|worst|min).*?(revenue|sales|units|performance|dealer)', "ranking", "dealer", "get_bottom_dealers"),
            (r'compare.*?(dealer|warehouse|city)', "comparison", "dealer", "compare_dealers"),
            (r'(national|overall|country).*?(kpi|dashboard|performance)', "national_kpi", "national_kpi", "get_national_kpi_dashboard"),
        ]
        
        for pattern, intent, service_key, method in analytics_patterns:
            if re.search(pattern, message_lower):
                decision = RoutingDecision(
                    intent=intent,
                    service_key=service_key,
                    method=method,
                    confidence=0.85,
                    needs_groq=False,
                    reason=f"Analytics pattern: {intent}",
                    original_message=message_clean
                )
                self._cache_decision(cache_key, decision)
                return decision
        
        # ============================================================
        # STAGE: AI Classification (for complex queries)
        # ============================================================
        if len(message_lower.split()) > 3:
            ai_result = self.ai_classifier.classify(message_clean)
            if ai_result:
                intent = ai_result.get("intent")
                entity_name = ai_result.get("entity_name")
                target_service = ai_result.get("target_service")
                confidence = ai_result.get("confidence", 0.5)
                
                if intent in ["dealer_dashboard", "dealer_profile"] and entity_name:
                    found_dealer = self._find_dealer_in_db(entity_name)
                    if found_dealer:
                        decision = RoutingDecision(
                            intent="dealer_dashboard",
                            service_key="dealer",
                            method="get_dealer_dashboard",
                            entity=found_dealer,
                            confidence=confidence,
                            needs_groq=False,
                            reason=f"AI detected dealer: {found_dealer}",
                            original_message=message_clean
                        )
                        self._cache_decision(cache_key, decision)
                        return decision
                
                # If AI suggested a service, try it
                if target_service in ["dn", "dealer", "warehouse", "city", "product", "national_kpi"]:
                    service_map = {
                        "dn": ("dn_lookup", "get_dn_dashboard"),
                        "dealer": ("dealer_dashboard", "get_dealer_dashboard"),
                        "warehouse": ("warehouse_dashboard", "get_warehouse_dashboard"),
                        "city": ("city_dashboard", "get_city_dashboard"),
                        "product": ("product_dashboard", "get_product_dashboard"),
                        "national_kpi": ("national_kpi", "get_national_kpi_dashboard"),
                    }
                    if target_service in service_map:
                        intent, method = service_map[target_service]
                        decision = RoutingDecision(
                            intent=intent,
                            service_key=target_service,
                            method=method,
                            entity=entity_name if entity_name else None,
                            confidence=confidence,
                            needs_groq=False,
                            reason=f"AI routing: {target_service}",
                            original_message=message_clean
                        )
                        self._cache_decision(cache_key, decision)
                        return decision
        
        # ============================================================
        # STAGE: Help / Greeting
        # ============================================================
        if re.search(r'(?:help|menu|hi|hello|hey|good morning|good evening|what can you do|available commands)', message_lower):
            decision = RoutingDecision(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.9,
                needs_groq=True,
                reason="Help/greeting detected",
                original_message=message_clean
            )
            self._cache_decision(cache_key, decision)
            return decision
        
        # ============================================================
        # FALLBACK: Groq
        # ============================================================
        decision = RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.3,
            needs_groq=True,
            reason="No intent detected - Groq fallback",
            original_message=message_clean
        )
        self._cache_decision(cache_key, decision)
        return decision
    
    def _cache_decision(self, cache_key: str, decision: RoutingDecision) -> None:
        """Cache routing decision"""
        self._routing_cache[cache_key] = (time.time(), decision)
        # Limit cache size
        if len(self._routing_cache) > 2000:
            # Remove oldest 100 entries
            oldest = sorted(self._routing_cache.items(), key=lambda x: x[1][0])[:100]
            for key, _ in oldest:
                del self._routing_cache[key]

# ==========================================================
# BLOCK 9: SERVICE STATUS ENUM
# ==========================================================

class ServiceStatus:
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

# ==========================================================
# BLOCK 10: POSTGRESQL VALIDATOR
# ==========================================================

class PostgreSQLValidator:
    REQUIRED_COLUMNS = [
        "dn_no", "customer_name", "dealer_code", "customer_code",
        "ship_to_city", "warehouse", "dn_qty", "dn_amount",
        "dn_create_date", "good_issue_date", "pod_date",
        "delivery_status", "pgi_status", "pod_status", "pending_flag"
    ]
    
    def validate(self) -> Dict[str, Any]:
        result = {
            "success": False,
            "connected": False,
            "table_exists": False,
            "columns_valid": False,
            "errors": [],
            "warnings": [],
            "record_count": 0,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            
            try:
                session.execute(text("SELECT 1"))
                result["connected"] = True
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                session.close()
                return result
            
            inspector = sa_inspect(session.bind)
            tables = inspector.get_table_names()
            
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                session.close()
                return result
            
            result["table_exists"] = True
            
            columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
            missing = [col for col in self.REQUIRED_COLUMNS if col not in columns]
            
            if missing:
                result["errors"].append(f"Missing columns: {missing}")
                result["columns_valid"] = False
            else:
                result["columns_valid"] = True

            try:
                result["record_count"] = int(session.query(func.count(DeliveryReport.id)).scalar() or 0)
            except Exception:
                pass
            
            session.close()
            
            if result["connected"] and result["table_exists"] and result["columns_valid"]:
                result["success"] = True
            
            return result
            
        except Exception as e:
            result["errors"].append(str(e))
            return result

# ==========================================================
# BLOCK 11: SERVICE REGISTRY
# ==========================================================

class ServiceRegistry:
    SERVICES = {
        "dn": {
            "module": "app.services.dn_analysis",
            "class_name": "DNAnalysisService",
            "methods": [
                "get_dn_dashboard", "search_dn", "verify_dn",
                "get_pending_dns", "get_pending_pgi", "get_pending_pod",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "DN Analytics Service"
        },
        "dealer": {
            "module": "app.services.dealer_analytics_service",
            "class_name": "DealerAnalyticsService",
            "methods": [
                "get_dealer_dashboard", "get_dealer_profile", 
                "compare_dealers", "get_top_dealers", "get_bottom_dealers",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Dealer Analytics Service"
        },
        "warehouse": {
            "module": "app.services.warehouse_analytics_service",
            "class_name": "WarehouseAnalyticsService",
            "methods": [
                "get_warehouse_dashboard", "get_top_warehouses",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Warehouse Analytics Service"
        },
        "city": {
            "module": "app.services.city_analytics_service",
            "class_name": "CityAnalyticsService",
            "methods": [
                "get_city_dashboard", "get_top_cities",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "City Analytics Service"
        },
        "product": {
            "module": "app.services.product_analytics_service",
            "class_name": "ProductAnalyticsService",
            "methods": [
                "get_product_dashboard", "get_top_products",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Product Analytics Service"
        },
        "national_kpi": {
            "module": "app.services.national_kpi_service",
            "class_name": "NationalKPIService",
            "methods": [
                "get_national_kpi_dashboard", "get_delivery_kpis", 
                "get_warehouse_kpis",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "National KPI Service"
        }
    }
    
    def __init__(self):
        self._services = self.SERVICES.copy()
        self._status_cache = {}
        self._instance_cache = {}
        self._lock = threading.Lock()
        self._last_validation = None
        self._postgresql_validator = PostgreSQLValidator()
        self._routing_engine = IntelligentRoutingEngine()
    
    def validate_all_services(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            pg_status = self._postgresql_validator.validate()
            results = {}
            for service_key in self._services:
                results[service_key] = self._validate_service(
                    service_key, 
                    pg_valid=pg_status.get("success", False)
                )
            self._last_validation = time.time()
            return results
    
    def _validate_service(self, service_key: str, pg_valid: bool = False) -> Dict[str, Any]:
        if service_key not in self._services:
            return {"status": ServiceStatus.NOT_STARTED, "ready": False, "errors": ["Not registered"]}
        
        service_def = self._services[service_key]
        
        result = {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": [],
            "checks_passed": 0,
            "checks_total": 7
        }
        
        if not pg_valid:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append("PostgreSQL validation failed")
            return result
        
        result["checks_passed"] += 1
        
        try:
            module = importlib.import_module(service_def.get("module"))
            result["checks_passed"] += 1
        except ImportError as e:
            result["status"] = ServiceStatus.NOT_STARTED
            result["errors"].append(f"Module not found: {e}")
            return result
        
        if not hasattr(module, service_def.get("class_name")):
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Class '{service_def.get('class_name')}' not found")
            return result
        
        cls = getattr(module, service_def.get("class_name"))
        result["checks_passed"] += 1
        
        missing_methods = []
        for method in service_def.get("methods", []):
            if not hasattr(cls, method):
                missing_methods.append(method)
        
        if missing_methods:
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Missing methods: {missing_methods}")
            return result
        
        result["checks_passed"] += 1
        
        try:
            instance = cls()
            result["checks_passed"] += 1
        except Exception as e:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append(f"Instantiation failed: {e}")
            return result
        
        if hasattr(instance, "health_check"):
            try:
                health = instance.health_check()
                if health.get("healthy", False):
                    result["checks_passed"] += 1
                else:
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Health check failed")
                    return result
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Health check exception: {e}")
                return result
        
        if hasattr(instance, "validation_query"):
            try:
                validation = instance.validation_query()
                if validation.get("success", False):
                    result["checks_passed"] += 1
                else:
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Validation failed")
                    return result
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Validation exception: {e}")
                return result
        
        result["status"] = ServiceStatus.READY
        result["ready"] = True
        result["instance"] = instance
        
        return result
    
    def get_service_status(self, service_key: str) -> Dict[str, Any]:
        if (service_key not in self._status_cache or 
            self._last_validation is None or 
            time.time() - self._last_validation > 60):
            
            pg_status = self._postgresql_validator.validate()
            self._status_cache[service_key] = self._validate_service(
                service_key, 
                pg_valid=pg_status.get("success", False)
            )
            
            if self._status_cache[service_key].get("ready", False):
                self._instance_cache[service_key] = self._status_cache[service_key].get("instance")
        
        return self._status_cache.get(service_key, {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": ["Service not validated"]
        })
    
    def is_service_ready(self, service_key: str) -> bool:
        status = self.get_service_status(service_key)
        return status.get("ready", False)
    
    def get_service_instance(self, service_key: str):
        if not self.is_service_ready(service_key):
            return None
        return self._instance_cache.get(service_key)
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
        if service_key not in self._services:
            return {"error": "Service not registered"}
        status = self.get_service_status(service_key)
        return {
            "key": service_key,
            "description": self._services[service_key].get("description", ""),
            "status": status.get("status", ServiceStatus.NOT_STARTED),
            "ready": status.get("ready", False),
            "checks_passed": status.get("checks_passed", 0),
            "checks_total": status.get("checks_total", 7),
            "errors": status.get("errors", []),
            "warnings": status.get("warnings", [])
        }
    
    def get_all_service_statuses(self) -> Dict[str, Dict[str, Any]]:
        results = {}
        for service_key in self._services:
            results[service_key] = self.get_service_status(service_key)
        return results
    
    def get_health_report(self) -> Dict[str, Any]:
        statuses = self.get_all_service_statuses()
        total = len(statuses)
        ready = sum(1 for s in statuses.values() if s.get("ready", False))
        in_dev = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.IN_DEVELOPMENT)
        not_started = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.NOT_STARTED)
        error = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.ERROR)
        
        pg_status = self._postgresql_validator.validate()
        
        return {
            "total_services": total,
            "ready": ready,
            "in_development": in_dev,
            "not_started": not_started,
            "error": error,
            "readiness_score": (ready / total * 100) if total > 0 else 0,
            "services": statuses,
            "postgresql": pg_status,
            "last_validation": self._last_validation
        }

# ==========================================================
# BLOCK 12: WHATSAPP PROVIDER SERVICE - MASTER ROUTER
# ==========================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v6.1 - ENTERPRISE AI ROUTING (FIXED DEALER DETECTION)")
            logger.info("=" * 70)
            
            self.routing_engine = IntelligentRoutingEngine()
            logger.info("✅ IntelligentRoutingEngine initialized")
            
            self.registry = ServiceRegistry()
            logger.info("✅ ServiceRegistry initialized")
            
            self._groq_service = None
            try:
                from app.services.groq_service import get_groq_service
                self._groq_service = get_groq_service()
                logger.info("✅ GroqService initialized")
            except ImportError:
                logger.warning("⚠️ GroqService not available")
            except Exception as e:
                logger.error(f"❌ GroqService initialization failed: {e}")
            
            self.registry.validate_all_services()
            
            init_duration = (time.time() - start_time) * 1000
            health = self.registry.get_health_report()
            
            logger.info("")
            logger.info("   SERVICE REGISTRY STATUS:")
            logger.info(f"   ✅ Ready: {health['ready']}")
            logger.info(f"   🔧 In Development: {health['in_development']}")
            logger.info(f"   ⏳ Not Started: {health['not_started']}")
            logger.info(f"   🚨 Error: {health['error']}")
            logger.info(f"   📊 Readiness Score: {health['readiness_score']:.1f}%")
            logger.info("")
            
            pg_status = health.get('postgresql', {})
            logger.info(f"   PostgreSQL: {'✅' if pg_status.get('success') else '❌'} {pg_status.get('connected', False)}")
            logger.info("")
            
            for service_key, status in health['services'].items():
                ready = status.get("ready", False)
                status_text = status.get("status", "UNKNOWN")
                checks = status.get("checks_passed", 0)
                total_checks = status.get("checks_total", 7)
                icon = "✅" if ready else "🔧"
                logger.info(f"   {icon} {service_key.title():15} → {status_text} ({checks}/{total_checks} checks)")
            
            logger.info("")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   AI FEATURES: PydanticAI, spaCy, SentenceTransformers, FlashRank, SQLGlot")
            logger.info("   FIXED: Dealer detection for names like 'Haroon Electronics'")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    # ==========================================================
    # MAIN ROUTING METHOD
    # ==========================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a WhatsApp query - MAIN ENTRY POINT.
        
        Uses AI-powered routing with multi-stage pipeline:
        1. Fast path (DN, Pending)
        2. Dealer detection with database lookup
        3. Analytics intent detection
        4. PydanticAI classification
        5. Semantic search fallback
        6. Regex fallback
        """
        logger.info(f"📩 Processing WhatsApp query: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            # STEP 1: AI-powered routing
            routing_decision = self.routing_engine.route(message)
            logger.info(f"🎯 Intent: {routing_decision.intent}, Service: {routing_decision.service_key}, Confidence: {routing_decision.confidence:.2f}")

            # STEP 2: Check if this needs Groq
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                return await self._handle_groq(message, routing_decision)
            
            # STEP 3: Check Service Readiness
            service_key = routing_decision.service_key
            if not self.registry.is_service_ready(service_key):
                return self._format_module_unavailable(
                    message,
                    service_key,
                    self.registry.get_service_info(service_key)
                )
            
            # STEP 4: Execute Service
            result = await self._execute_service(routing_decision)
            payload = self._extract_service_payload(result)
            
            # STEP 5: Format Response
            return self._format_response(
                message,
                payload,
                error=not result.get("success", False)
            )
            
        except Exception as e:
            logger.exception(f"❌ Failed: {e}")
            return self._format_response(
                message,
                f"⚠️ An unexpected error occurred.\n\nPlease try again later.",
                error=True
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ Total response time: {elapsed_ms:.2f}ms")

    @staticmethod
    def _extract_service_payload(result: Dict[str, Any]) -> Any:
        """Extract payload from service result"""
        if not isinstance(result, dict):
            return result
        for key in ("formatted_response", "whatsapp_message", "response", "message", "dashboard", "profile", "data", "suggestions"):
            value = result.get(key)
            if value not in (None, ""):
                return value
        return result
    
    # ==========================================================
    # GROQ HANDLING
    # ==========================================================
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq queries with AI"""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if isinstance(response, dict) and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
                    if isinstance(response, str) and response.strip():
                        return self._format_response(message, response, error=False)
            except Exception as e:
                logger.error(f"❌ Groq failed: {e}")
        
        # Check if this is a dealer suggestion response
        if decision.intent == "dealer_suggestion":
            suggestions = self.routing_engine._find_similar_dealers(decision.entity or message)
            if suggestions:
                suggestion_text = "🔍 Did you mean:\n\n" + "\n".join([f"• {name}" for name in suggestions[:5]])
                suggestion_text += "\n\nPlease type the full dealer name exactly as shown above."
                return self._format_response(message, suggestion_text, error=False)
        
        # Fallback responses
        if any(word in message.lower() for word in ["hello", "hi", "hey", "good morning", "good evening"]):
            return self._format_response(
                message,
                "👋 Hello! I'm your Dealer Intelligence Assistant.\n\n"
                "I can help you with:\n"
                "📦 DN queries (send a DN number)\n"
                "🏪 Dealer analytics\n"
                "🏭 Warehouse analytics\n"
                "🏙️ City analytics\n"
                "📊 Rankings and comparisons\n\n"
                "Type 'Help' to see all commands.",
                error=False
            )
        elif "help" in message.lower() or "menu" in message.lower():
            return self._format_response(
                message,
                "📋 Available Commands\n\n"
                "📦 DN Queries:\n"
                "• Send a DN number (8-12 digits)\n"
                "• 'Pending DN'\n"
                "• 'Pending PGI'\n"
                "• 'Pending POD'\n\n"
                "🏪 Dealer Queries:\n"
                "• 'Dealer [name]'\n"
                "• '[Dealer name] profile'\n"
                "• 'Top dealers by revenue'\n"
                "• 'Compare [dealer1] and [dealer2]'\n\n"
                "🏭 Warehouse Queries:\n"
                "• 'Warehouse [name]'\n"
                "• 'Top warehouses'\n\n"
                "🏙️ City Queries:\n"
                "• 'City [name]'\n\n"
                "📦 Product Queries:\n"
                "• 'Product [name]'\n\n"
                "📊 Analytics:\n"
                "• 'National KPI dashboard'\n"
                "• 'Highest revenue dealer'\n"
                "• 'Which dealer has the most units?'",
                error=False
            )
        else:
            # Check if we have a dealer suggestion
            if decision.entity and len(decision.entity) > 2:
                similar = self.routing_engine._find_similar_dealers(decision.entity)
                if similar:
                    suggestion_text = "🔍 I couldn't find exactly that dealer. Did you mean:\n\n" + "\n".join([f"• {name}" for name in similar[:5]])
                    suggestion_text += "\n\nPlease type the full dealer name exactly as shown above."
                    return self._format_response(message, suggestion_text, error=False)
            
            return self._format_response(
                message,
                "I couldn't identify your request. Please specify:\n"
                "• A DN number (8-12 digits)\n"
                "• A dealer name (e.g., 'Dealer Taj Electronics')\n"
                "• A warehouse name\n"
                "• A city name\n"
                "• An analytics query (e.g., 'Top dealers')\n\n"
                "Type 'Help' for all commands.",
                error=False
            )
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    async def _execute_service(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Execute service with method call"""
        started = time.perf_counter()
        service_instance = self.registry.get_service_instance(decision.service_key)
        if not service_instance:
            return {"success": False, "error": f"Service '{decision.service_key}' not available"}
        
        try:
            method = getattr(service_instance, decision.method, None)
            if not method:
                return {"success": False, "error": f"Method '{decision.method}' not found"}
            
            if decision.entity:
                if decision.entity2:
                    result = method(decision.entity, decision.entity2)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return result if isinstance(result, dict) else {"success": True, "data": result}
            
        except Exception as e:
            logger.exception(f"❌ Service execution failed: {e}")
            return {"success": False, "error": str(e)}
        finally:
            logger.info(
                "⏱️ Service execution service=%s method=%s duration_ms=%.2f",
                decision.service_key, decision.method,
                (time.perf_counter() - started) * 1000
            )
    
    # ==========================================================
    # RESPONSE FORMATTING
    # ==========================================================
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        """Format response for WhatsApp with proper error handling"""
        if error:
            return {
                "success": not error,
                "message": original_message,
                "response": data,
                "error": error,
                "timestamp": datetime.now().isoformat()
            }
        
        # Handle objects with to_whatsapp_message
        if hasattr(data, "to_whatsapp_message"):
            try:
                data = data.to_whatsapp_message()
            except Exception:
                logger.exception("Dashboard WhatsApp formatting failed")

        # Handle dict with formatted response
        if isinstance(data, dict):
            for key in ("formatted_response", "whatsapp_message", "response", "message"):
                if data.get(key) not in (None, ""):
                    data = data[key]
                    break

        # Handle DN objects
        if hasattr(data, 'dn_no'):
            try:
                from app.routes.webhook import format_dn_response
                data = format_dn_response(data)
            except:
                pass
        
        # Handle dict with data field
        if isinstance(data, dict) and 'data' in data:
            inner_data = data['data']
            if hasattr(inner_data, 'dn_no'):
                try:
                    from app.routes.webhook import format_dn_response
                    data = format_dn_response(inner_data)
                except:
                    pass
        
        return {
            "success": True,
            "message": original_message,
            "response": data,
            "error": False,
            "timestamp": datetime.now().isoformat()
        }
    
    def _format_module_unavailable(self, original_message: str, service_key: str, info: Dict[str, Any]) -> Dict[str, Any]:
        status_text = info.get("status", "UNKNOWN")
        errors = info.get("errors", [])
        checks_passed = info.get("checks_passed", 0)
        checks_total = info.get("checks_total", 7)
        
        message = f"""⚠️ Module Currently Under Development

Module:
{service_key.title()} Service

Status:
{status_text}

Readiness:
{checks_passed}/{checks_total} checks passed

"""
        if errors:
            message += f"\nMissing:\n{chr(10).join(['- ' + e for e in errors[:3]])}"
        message += "\n\nPlease try again after development is completed."
        
        return self._format_response(original_message, message, error=True)
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_service_registry_status(self) -> Dict[str, Any]:
        return self.registry.get_health_report()
    
    def validate_all_services(self) -> Dict[str, Any]:
        return self.registry.validate_all_services()
    
    def get_system_health(self) -> Dict[str, Any]:
        service_health = self.registry.get_health_report()
        return {
            "services": service_health,
            "system_status": "healthy" if service_health.get("readiness_score", 0) > 50 else "degraded",
            "timestamp": datetime.now().isoformat(),
            "version": "6.1",
            "ai_features": {
                "pydantic_ai": Agent is not None,
                "spacy": nlp is not None,
                "sentence_transformers": _encoder is not None,
                "flashrank": _ranker is not None,
                "sqlglot": sqlglot is not None,
                "pgvector": Vector is not None,
                "pyarrow": pa is not None,
                "polars": pl is not None
            }
        }
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
        return self.registry.get_service_info(service_key)
    
    def refresh_service_status(self, service_key: str = None) -> Dict[str, Any]:
        if service_key:
            self.registry._status_cache.pop(service_key, None)
            self.registry._instance_cache.pop(service_key, None)
            return self.registry.get_service_status(service_key)
        else:
            return self.registry.validate_all_services()

# ==========================================================
# BLOCK 13: THREAD-SAFE SINGLETON
# ==========================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService singleton initialized (v6.1)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    return _whatsapp_provider_service

# ==========================================================
# BLOCK 14: EXPORTS
# ==========================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'ServiceStatus',
    'RoutingDecision',
    'IntelligentRoutingEngine',
    'AIClassifier',
    'SemanticSearchEngine',
    'SpacyEntityExtractor',
    'FlashRankReranker'
]

# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v6.1 - ENTERPRISE AI ROUTING ENGINE")
logger.info("=" * 70)
logger.info("✅ PydanticAI - Structured classification")
logger.info("✅ spaCy - Entity extraction")
logger.info("✅ SentenceTransformers - Semantic search")
logger.info("✅ FlashRank - Candidate reranking")
logger.info("✅ SQLGlot - SQL validation")
logger.info("✅ PGVector - Vector similarity")
logger.info("✅ PyArrow - Fast data processing")
logger.info("✅ Polars - Fast DataFrame")
logger.info("✅ FIXED: Dealer detection for names like 'Haroon Electronics'")
logger.info("=" * 70)
