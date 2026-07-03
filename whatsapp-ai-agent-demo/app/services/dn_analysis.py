"""
File: app/services/dn_analysis.py
Version: 32.0 - ENTERPRISE DN INTELLIGENCE ENGINE
================================================================================
ARCHITECTURE
================================================================================

Main Menu → Press "1" → ENTER DN SERVICE
    ↓
EVERYTHING stays inside dn_analysis.py
    ↓
ALL DN questions answered by THIS FILE
    ↓
NEVER goes back to AI Provider
    ↓
ONLY "99" returns to Main Menu

================================================================================
HYBRID INTENT ENGINE
================================================================================

1. spaCy → Named Entity Recognition (Warehouse, Dealer, City, Product)
2. sentence-transformers → Semantic Similarity
3. RapidFuzz → Fuzzy Matching
4. FlashRank → Intent Ranking
5. Semantic Router → Route Selection
6. Groq → AI Explanations ONLY

================================================================================
SUPPORTED INTENTS
================================================================================

Dashboard | Pending DN | Pending PGI | Pending POD | Delivered DN
Warehouse Dashboard | Warehouse Ranking | Warehouse Comparison
Warehouse Quantity | Warehouse Revenue | Warehouse Health
Dealer Dashboard | Dealer Ranking | Dealer Comparison
Product Dashboard | Material Dashboard | Division Dashboard
City Dashboard | Sales Office Dashboard | Sales Manager Dashboard
Delivery Timeline | Transit Analysis | SLA Compliance
Executive Summary | AI Insights | Forecast | Recommendations
Root Cause Analysis | Delivery Aging | Pending Aging

================================================================================
BUSINESS RULES
================================================================================

DN Count: COUNT(DISTINCT dn_no)
Quantity: SUM(dn_qty)
Revenue: SUM(dn_amount)
Pending DN: delivery_status != 'Delivered' OR pending_flag = TRUE
Pending PGI: good_issue_date IS NULL
Pending POD: pod_date IS NULL

================================================================================
STATUS: ENTERPRISE READY
================================================================================
"""

from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
from collections import defaultdict
import hashlib
import math
import json
from enum import Enum

logger = logging.getLogger(__name__)

# ============================================================
# AI LIBRARIES - Graceful Loading
# ============================================================

# Groq - AI Explanations ONLY
try:
    from groq import Groq
    GROQ_AVAILABLE = True
    logger.info("✅ Groq loaded")
except ImportError:
    GROQ_AVAILABLE = False
    logger.warning("⚠️ Groq not available")

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
            subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"], capture_output=True)
            nlp = spacy.load("en_core_web_sm")
            logger.info("✅ spaCy downloaded and loaded")
        except Exception:
            logger.warning("⚠️ spaCy model not available")
except ImportError:
    SPACY_AVAILABLE = False
    nlp = None
    logger.warning("⚠️ spaCy not available")

# Sentence Transformers - Semantic Similarity
try:
    from sentence_transformers import SentenceTransformer
    SEMANTIC_AVAILABLE = True
    semantic_model = SentenceTransformer('all-MiniLM-L6-v2')
    logger.info("✅ SentenceTransformer loaded")
except ImportError:
    SEMANTIC_AVAILABLE = False
    semantic_model = None
    logger.warning("⚠️ SentenceTransformer not available")

# RapidFuzz - Fuzzy Matching
try:
    from rapidfuzz import fuzz, process
    RAPIDFUZZ_AVAILABLE = True
    logger.info("✅ RapidFuzz loaded")
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("⚠️ RapidFuzz not available")

# Semantic Router - Intent Classification
try:
    from semantic_router import Route, Router
    from semantic_router.encoders import HuggingFaceEncoder
    SEMANTIC_ROUTER_AVAILABLE = True
    logger.info("✅ Semantic Router loaded")
except ImportError:
    SEMANTIC_ROUTER_AVAILABLE = False
    logger.warning("⚠️ Semantic Router not available")

# FlashRank - Intent Ranking
try:
    from flashrank import Ranker
    FLASHRANK_AVAILABLE = True
    ranker = Ranker()
    logger.info("✅ FlashRank loaded")
except ImportError:
    FLASHRANK_AVAILABLE = False
    ranker = None
    logger.warning("⚠️ FlashRank not available")

# Dateparser - Natural Language Dates
try:
    import dateparser
    DATEPARSER_AVAILABLE = True
    logger.info("✅ Dateparser loaded")
except ImportError:
    DATEPARSER_AVAILABLE = False
    logger.warning("⚠️ Dateparser not available")

# ============================================================
# DATABASE IMPORTS
# ============================================================

try:
    from sqlalchemy import func, or_, and_, desc, asc, case, extract
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
AI_PROVIDER = os.getenv("AI_PROVIDER", "groq")
AI_MODEL = os.getenv("AI_MODEL", "llama3-70b-8192")
USE_AI_ENHANCEMENT = os.getenv("USE_AI_ENHANCEMENT", "true").lower() == "true"
DN_DELAY_THRESHOLD_DAYS = int(os.getenv("DN_DELAY_THRESHOLD_DAYS", "7"))
SLA_TARGET_DAYS = int(os.getenv("DN_SLA_TARGET_DAYS", "3"))
CONFIDENCE_THRESHOLD = float(os.getenv("INTENT_CONFIDENCE_THRESHOLD", "0.70"))

# ============================================================
# ENUMS
# ============================================================

class IntentType(Enum):
    """DN intent types"""
    DASHBOARD = "dashboard"
    PENDING_DN = "pending_dn"
    PENDING_PGI = "pending_pgi"
    PENDING_POD = "pending_pod"
    DELIVERED_DN = "delivered_dn"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    WAREHOUSE_COMPARISON = "warehouse_comparison"
    WAREHOUSE_QUANTITY = "warehouse_quantity"
    WAREHOUSE_REVENUE = "warehouse_revenue"
    WAREHOUSE_HEALTH = "warehouse_health"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    DEALER_COMPARISON = "dealer_comparison"
    DEALER_QUANTITY = "dealer_quantity"
    PRODUCT_DASHBOARD = "product_dashboard"
    MATERIAL_DASHBOARD = "material_dashboard"
    DIVISION_DASHBOARD = "division_dashboard"
    CITY_DASHBOARD = "city_dashboard"
    SALES_OFFICE_DASHBOARD = "sales_office_dashboard"
    DELIVERY_TIMELINE = "delivery_timeline"
    TRANSIT_ANALYSIS = "transit_analysis"
    SLA_COMPLIANCE = "sla_compliance"
    EXECUTIVE_SUMMARY = "executive_summary"
    AI_INSIGHTS = "ai_insights"
    FORECAST = "forecast"
    RECOMMENDATIONS = "recommendations"
    ROOT_CAUSE = "root_cause"
    DELIVERY_AGING = "delivery_aging"
    PENDING_AGING = "pending_aging"
    SEARCH = "search"
    COMPARE = "compare"
    TREND = "trend"
    STATUS = "status"
    REVENUE = "revenue"
    UNITS = "units"
    CUSTOMER = "customer"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    MENU = "menu"
    UNKNOWN = "unknown"

# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DNContext:
    """DN session context with smart memory"""
    current_dn: Optional[str] = None
    in_dn_service: bool = False
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
    last_intent: Optional[str] = None
    last_response: Optional[str] = None
    search_results: Optional[List[Dict[str, Any]]] = None
    session_start: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    
    # Smart Memory
    current_warehouse: Optional[str] = None
    current_dealer: Optional[str] = None
    current_city: Optional[str] = None
    current_product: Optional[str] = None
    
    # Comparison memory
    comparison_items: List[str] = field(default_factory=list)

@dataclass
class IntentResult:
    """Intent detection result"""
    intent: IntentType
    confidence: float
    entities: Dict[str, Any]
    filters: Dict[str, Any]
    group_by: Optional[str] = None
    order_by: Optional[str] = None
    limit: int = 20
    requires_ai: bool = False
    explanation: str = ""

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

def _calculate_days(date1: Any, date2: Any) -> Optional[int]:
    if not date1 or not date2:
        return None
    if hasattr(date1, "date"):
        date1 = date1.date()
    if hasattr(date2, "date"):
        date2 = date2.date()
    if isinstance(date1, date) and isinstance(date2, date):
        return (date2 - date1).days
    return None

def _get_status_emoji(status: str) -> str:
    status_map = {
        "Delivered": "✅",
        "Completed": "✅",
        "In Transit": "🚚",
        "Pending PGI": "⏳",
        "Pending POD": "📋",
        "Pending DN": "📦",
        "Delayed": "⚠️",
        "Overdue": "🚨",
    }
    return status_map.get(status, "📊")

# ============================================================
# ENTERPRISE INTENT ENGINE
# ============================================================

class EnterpriseIntentEngine:
    """
    Hybrid Intent Detection Engine using:
    1. spaCy → Named Entity Recognition
    2. sentence-transformers → Semantic Similarity
    3. RapidFuzz → Fuzzy Matching
    4. FlashRank → Intent Ranking
    5. Semantic Router → Route Selection
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        self._initialized = True
        
        # Cache for entities
        self._entity_cache: Dict[str, List[str]] = {}
        self._cache_lock = threading.RLock()
        
        # Semantic router
        self._router = self._init_router()
        
        # Intent examples for semantic similarity
        self._intent_examples = self._init_intent_examples()
        
        logger.info("🧠 EnterpriseIntentEngine initialized")
    
    def _init_router(self):
        """Initialize semantic router"""
        if not SEMANTIC_ROUTER_AVAILABLE:
            return None
        
        try:
            routes = [
                Route(name="dashboard", utterances=[
                    "show dn", "dn dashboard", "dn details", "dn summary"
                ]),
                Route(name="pending_dn", utterances=[
                    "pending dns", "pending deliveries", "undelivered dns",
                    "outstanding deliveries", "open dns", "delivery pending"
                ]),
                Route(name="pending_pgi", utterances=[
                    "pending pgi", "pgi not done", "goods issue pending"
                ]),
                Route(name="pending_pod", utterances=[
                    "pending pod", "pod missing", "no pod", "pod pending"
                ]),
                Route(name="warehouse_dashboard", utterances=[
                    "warehouse dashboard", "warehouse performance", "show warehouse"
                ]),
                Route(name="warehouse_ranking", utterances=[
                    "top warehouses", "warehouse ranking", "best warehouse"
                ]),
                Route(name="warehouse_comparison", utterances=[
                    "compare warehouses", "warehouse vs", "warehouse comparison"
                ]),
                Route(name="warehouse_quantity", utterances=[
                    "warehouse quantity", "warehouse units", "quantity by warehouse"
                ]),
                Route(name="warehouse_revenue", utterances=[
                    "warehouse revenue", "revenue by warehouse", "sales by warehouse"
                ]),
                Route(name="dealer_dashboard", utterances=[
                    "dealer dashboard", "dealer performance", "show dealer"
                ]),
                Route(name="dealer_ranking", utterances=[
                    "top dealers", "dealer ranking", "best dealer"
                ]),
                Route(name="dealer_quantity", utterances=[
                    "dealer quantity", "dealer units", "quantity by dealer"
                ]),
                Route(name="city_dashboard", utterances=[
                    "city dashboard", "city performance", "show city"
                ]),
                Route(name="product_dashboard", utterances=[
                    "product dashboard", "product performance", "show product"
                ]),
                Route(name="executive_summary", utterances=[
                    "executive summary", "summary", "overview"
                ]),
                Route(name="ai_insights", utterances=[
                    "insights", "analysis", "key findings"
                ]),
                Route(name="recommendations", utterances=[
                    "recommendations", "suggestions", "improve", "what to do"
                ]),
                Route(name="root_cause", utterances=[
                    "root cause", "why", "reason", "cause"
                ]),
                Route(name="forecast", utterances=[
                    "forecast", "predict", "future", "expected"
                ]),
                Route(name="trend", utterances=[
                    "trend", "pattern", "over time", "weekly", "monthly"
                ]),
                Route(name="sla_compliance", utterances=[
                    "sla", "sla compliance", "service level", "delivery time"
                ]),
                Route(name="transit_analysis", utterances=[
                    "transit", "transit time", "travel time", "how long"
                ]),
                Route(name="delivery_timeline", utterances=[
                    "timeline", "history", "chronology", "sequence"
                ]),
                Route(name="search", utterances=[
                    "search dn", "find dn", "lookup dn", "search for"
                ]),
                Route(name="compare", utterances=[
                    "compare dns", "dn vs", "comparison"
                ]),
            ]
            encoder = HuggingFaceEncoder()
            router = Router(routes=routes, encoder=encoder)
            logger.info(f"✅ Semantic Router initialized with {len(routes)} routes")
            return router
        except Exception as e:
            logger.warning(f"Semantic Router init failed: {e}")
            return None
    
    def _init_intent_examples(self) -> Dict[str, List[str]]:
        """Initialize intent examples for semantic similarity"""
        return {
            "dashboard": ["show dn", "dn details", "dn info", "dn summary"],
            "pending_dn": ["pending dns", "pending deliveries", "undelivered dns", "outstanding deliveries"],
            "pending_pgi": ["pending pgi", "pgi not done", "goods issue pending"],
            "pending_pod": ["pending pod", "pod missing", "no pod", "pod pending"],
            "warehouse_dashboard": ["warehouse performance", "show warehouse", "warehouse details"],
            "warehouse_ranking": ["top warehouses", "warehouse ranking", "best warehouse"],
            "warehouse_comparison": ["compare warehouses", "warehouse vs warehouse"],
            "warehouse_quantity": ["warehouse units", "quantity by warehouse"],
            "warehouse_revenue": ["warehouse revenue", "revenue by warehouse"],
            "dealer_dashboard": ["dealer performance", "show dealer", "dealer details"],
            "dealer_ranking": ["top dealers", "dealer ranking", "best dealer"],
            "city_dashboard": ["city performance", "show city", "city details"],
            "product_dashboard": ["product performance", "show product", "product details"],
            "executive_summary": ["executive summary", "overview", "summary"],
            "ai_insights": ["insights", "analysis", "key findings"],
            "recommendations": ["recommendations", "suggestions", "improve"],
            "root_cause": ["root cause", "why", "reason"],
            "forecast": ["forecast", "predict", "future"],
            "trend": ["trend", "pattern", "over time"],
            "sla_compliance": ["sla compliance", "service level"],
            "transit_analysis": ["transit time", "travel time"],
            "delivery_timeline": ["timeline", "history", "chronology"],
            "search": ["search dn", "find dn", "lookup dn"],
            "compare": ["compare dns", "dn vs"],
        }
    
    def _extract_entities_spacy(self, query: str) -> Dict[str, Any]:
        """Extract entities using spaCy"""
        entities = {}
        if not SPACY_AVAILABLE or not nlp:
            return entities
        
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
                elif ent.label_ == "PRODUCT":
                    entities["product"] = ent.text
                elif ent.label_ == "MONEY":
                    entities["money"] = ent.text
                elif ent.label_ == "QUANTITY":
                    entities["quantity"] = ent.text
        except Exception:
            pass
        
        return entities
    
    def _fuzzy_match_entity(self, query: str, entities: List[str], threshold: int = 80) -> Optional[str]:
        """Fuzzy match entity using RapidFuzz"""
        if not RAPIDFUZZ_AVAILABLE or not entities:
            return None
        
        try:
            matches = process.extract(query, entities, scorer=fuzz.WRatio, limit=1)
            if matches and matches[0][1] >= threshold:
                return matches[0][0]
        except Exception:
            pass
        return None
    
    def _get_known_entities(self) -> Dict[str, List[str]]:
        """Get known entities from cache or database"""
        with self._cache_lock:
            if self._entity_cache:
                return self._entity_cache
        
        # Load entities from database
        entities = {
            "warehouses": ["Lahore", "Karachi", "Rawalpindi", "Multan", "Peshawar", 
                          "Hyderabad", "Quetta", "Faisalabad", "Sialkot", "Gujranwala"],
            "cities": ["Lahore", "Karachi", "Rawalpindi", "Islamabad", "Multan", 
                      "Peshawar", "Quetta", "Hyderabad", "Faisalabad", "Sialkot"],
            "dealer_names": [],
            "product_names": [],
            "divisions": ["Small Appliances", "Freezer", "Refrigerator", "Air Conditioner"],
        }
        
        # Try to load from database
        if DB_AVAILABLE:
            try:
                session = SessionLocal()
                
                # Get unique warehouses
                warehouses = session.query(func.distinct(DeliveryReport.warehouse)).filter(
                    DeliveryReport.warehouse.isnot(None)
                ).all()
                if warehouses:
                    entities["warehouses"] = [w[0] for w in warehouses if w[0]]
                
                # Get unique cities
                cities = session.query(func.distinct(DeliveryReport.ship_to_city)).filter(
                    DeliveryReport.ship_to_city.isnot(None)
                ).all()
                if cities:
                    entities["cities"] = [c[0] for c in cities if c[0]]
                
                # Get unique customers (dealers)
                dealers = session.query(func.distinct(DeliveryReport.customer_name)).filter(
                    DeliveryReport.customer_name.isnot(None)
                ).limit(100).all()
                if dealers:
                    entities["dealer_names"] = [d[0] for d in dealers if d[0]]
                
                # Get unique products
                products = session.query(func.distinct(DeliveryReport.customer_model)).filter(
                    DeliveryReport.customer_model.isnot(None)
                ).limit(100).all()
                if products:
                    entities["product_names"] = [p[0] for p in products if p[0]]
                
                session.close()
            except Exception:
                pass
        
        with self._cache_lock:
            self._entity_cache = entities
        
        return entities
    
    def _semantic_similarity(self, query: str, intent_examples: List[str]) -> float:
        """Calculate semantic similarity using sentence-transformers"""
        if not SEMANTIC_AVAILABLE or not semantic_model:
            return 0.0
        
        try:
            query_embedding = semantic_model.encode(query)
            example_embeddings = semantic_model.encode(intent_examples)
            
            from numpy import dot
            from numpy.linalg import norm
            
            best_score = 0.0
            for example_emb in example_embeddings:
                similarity = dot(query_embedding, example_emb) / (norm(query_embedding) * norm(example_emb))
                best_score = max(best_score, similarity)
            
            return best_score
        except Exception:
            return 0.0
    
    def detect_intent(self, query: str) -> IntentResult:
        """
        Detect intent using hybrid approach:
        1. Semantic Router (fast)
        2. Semantic Similarity (accurate)
        3. Keyword fallback
        4. Confidence threshold
        """
        query_clean = query.strip()
        query_lower = query_clean.lower()
        
        # Extract DN number
        dn = _extract_dn(query_clean)
        
        # Extract entities using spaCy
        spacy_entities = self._extract_entities_spacy(query_clean)
        
        # Get known entities for fuzzy matching
        known_entities = self._get_known_entities()
        
        # Fuzzy match entities
        fuzzy_entities = {}
        
        # Check for warehouse names
        for warehouse in known_entities.get("warehouses", []):
            if warehouse.lower() in query_lower:
                fuzzy_entities["warehouse"] = warehouse
                break
        
        # Check for city names
        if not fuzzy_entities.get("warehouse"):
            for city in known_entities.get("cities", []):
                if city.lower() in query_lower:
                    fuzzy_entities["city"] = city
                    break
        
        # Check for dealer names
        for dealer in known_entities.get("dealer_names", [])[:50]:
            if dealer.lower() in query_lower:
                fuzzy_entities["dealer"] = dealer
                break
        
        # Check for product names
        for product in known_entities.get("product_names", [])[:50]:
            if product.lower() in query_lower:
                fuzzy_entities["product"] = product
                break
        
        # Combine entities
        entities = {**spacy_entities, **fuzzy_entities}
        
        # If DN detected, add it
        if dn:
            entities["dn"] = dn
        
        # Detect intent using semantic router
        if self._router:
            try:
                result = self._router.route(query_clean)
                if result and hasattr(result, 'name'):
                    intent_name = result.name
                    for intent in IntentType:
                        if intent.value == intent_name:
                            return IntentResult(
                                intent=intent,
                                confidence=0.85,
                                entities=entities,
                                filters=entities.copy(),
                                explanation=f"Semantic routing: {intent_name}"
                            )
            except Exception:
                pass
        
        # Use semantic similarity
        best_intent = IntentType.UNKNOWN
        best_score = 0.0
        
        for intent_name, examples in self._intent_examples.items():
            score = self._semantic_similarity(query_clean, examples)
            if score > best_score:
                best_score = score
                try:
                    best_intent = IntentType(intent_name)
                except ValueError:
                    pass
        
        # Keyword fallback for specific patterns
        if best_score < 0.5:
            if "pending" in query_lower or "undelivered" in query_lower:
                if "pgi" in query_lower:
                    best_intent = IntentType.PENDING_PGI
                    best_score = 0.7
                elif "pod" in query_lower:
                    best_intent = IntentType.PENDING_POD
                    best_score = 0.7
                elif "quantity" in query_lower or "qty" in query_lower:
                    best_intent = IntentType.PENDING_DN
                    best_score = 0.6
                else:
                    best_intent = IntentType.PENDING_DN
                    best_score = 0.6
            elif "warehouse" in query_lower:
                if "ranking" in query_lower or "top" in query_lower:
                    best_intent = IntentType.WAREHOUSE_RANKING
                    best_score = 0.7
                elif "revenue" in query_lower:
                    best_intent = IntentType.WAREHOUSE_REVENUE
                    best_score = 0.7
                elif "quantity" in query_lower or "qty" in query_lower:
                    best_intent = IntentType.WAREHOUSE_QUANTITY
                    best_score = 0.7
                elif "health" in query_lower or "score" in query_lower:
                    best_intent = IntentType.WAREHOUSE_HEALTH
                    best_score = 0.7
                elif "compare" in query_lower or "vs" in query_lower:
                    best_intent = IntentType.WAREHOUSE_COMPARISON
                    best_score = 0.7
                elif dn:
                    best_intent = IntentType.DASHBOARD
                    best_score = 0.8
                else:
                    best_intent = IntentType.WAREHOUSE_DASHBOARD
                    best_score = 0.6
            elif "dealer" in query_lower:
                if "ranking" in query_lower or "top" in query_lower:
                    best_intent = IntentType.DEALER_RANKING
                    best_score = 0.7
                elif "quantity" in query_lower:
                    best_intent = IntentType.DEALER_QUANTITY
                    best_score = 0.7
                elif dn:
                    best_intent = IntentType.DASHBOARD
                    best_score = 0.8
                else:
                    best_intent = IntentType.DEALER_DASHBOARD
                    best_score = 0.6
            elif "city" in query_lower:
                if dn:
                    best_intent = IntentType.DASHBOARD
                    best_score = 0.8
                else:
                    best_intent = IntentType.CITY_DASHBOARD
                    best_score = 0.6
            elif "product" in query_lower or "material" in query_lower:
                best_intent = IntentType.PRODUCT_DASHBOARD
                best_score = 0.6
            elif "division" in query_lower:
                best_intent = IntentType.DIVISION_DASHBOARD
                best_score = 0.6
            elif "sla" in query_lower or "compliance" in query_lower:
                best_intent = IntentType.SLA_COMPLIANCE
                best_score = 0.7
            elif "transit" in query_lower or "travel" in query_lower:
                best_intent = IntentType.TRANSIT_ANALYSIS
                best_score = 0.7
            elif "timeline" in query_lower or "history" in query_lower:
                best_intent = IntentType.DELIVERY_TIMELINE
                best_score = 0.7
            elif "trend" in query_lower or "pattern" in query_lower:
                best_intent = IntentType.TREND
                best_score = 0.6
            elif "forecast" in query_lower or "predict" in query_lower:
                best_intent = IntentType.FORECAST
                best_score = 0.6
            elif "insight" in query_lower or "analysis" in query_lower:
                best_intent = IntentType.AI_INSIGHTS
                best_score = 0.6
            elif "recommend" in query_lower or "suggest" in query_lower:
                best_intent = IntentType.RECOMMENDATIONS
                best_score = 0.6
            elif "root cause" in query_lower or "why" in query_lower:
                best_intent = IntentType.ROOT_CAUSE
                best_score = 0.6
            elif "search" in query_lower or "find" in query_lower:
                best_intent = IntentType.SEARCH
                best_score = 0.7
            elif "compare" in query_lower or "vs" in query_lower:
                best_intent = IntentType.COMPARE
                best_score = 0.7
            elif "status" in query_lower:
                best_intent = IntentType.STATUS
                best_score = 0.7
            elif "revenue" in query_lower or "amount" in query_lower:
                best_intent = IntentType.REVENUE
                best_score = 0.7
            elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower:
                best_intent = IntentType.UNITS
                best_score = 0.7
            elif "customer" in query_lower:
                best_intent = IntentType.CUSTOMER
                best_score = 0.7
            elif "dealer" in query_lower:
                best_intent = IntentType.DEALER
                best_score = 0.7
            elif "warehouse" in query_lower:
                best_intent = IntentType.WAREHOUSE
                best_score = 0.7
            elif "city" in query_lower:
                best_intent = IntentType.CITY
                best_score = 0.7
            elif "menu" in query_lower:
                best_intent = IntentType.MENU
                best_score = 1.0
            elif dn:
                best_intent = IntentType.DASHBOARD
                best_score = 0.9
        
        # AI explanation requests
        if best_intent in [IntentType.AI_INSIGHTS, IntentType.RECOMMENDATIONS, 
                          IntentType.ROOT_CAUSE, IntentType.EXECUTIVE_SUMMARY]:
            requires_ai = True
        else:
            requires_ai = False
        
        return IntentResult(
            intent=best_intent,
            confidence=min(1.0, best_score),
            entities=entities,
            filters=entities.copy(),
            requires_ai=requires_ai,
            explanation=f"Detected: {best_intent.value} (confidence: {best_score:.2f})"
        )

# ============================================================
# DN MENU RENDERER
# ============================================================

class DNMenuRenderer:
    """DN Menu Renderer - WhatsApp Format"""
    
    @staticmethod
    def render_main_menu() -> str:
        return "\n".join([
            "📦 *DN INTELLIGENCE ENGINE*",
            "",
            "0. Main Menu",
            "1. DN Dashboard",
            "2. Pending DN",
            "3. Search DN",
            "4. Compare DN",
            "5. AI Insights",
            "6. Trends",
            "7. Forecast",
            "99. Back to Main",
            "",
            "📌 *Smart Commands (Uses current DN):*",
            "",
            "📊 *Info:* status, revenue, units, customer, dealer",
            "📍 *Location:* warehouse, city",
            "📋 *Status:* pending, pgi, pod, sla, delay",
            "🤖 *AI:* insights, recommendations, root-cause",
            "🔍 *Search:* search [keyword]",
            "🔄 *Compare:* compare DN1 DN2",
            "",
            "💡 *Follow-up Commands:*",
            "• After viewing a DN, just type 'status', 'revenue', etc.",
            "• No need to type the DN again!",
            "",
            "Reply with a number or command:"
        ])
    
    @staticmethod
    def render_pending_dns(items: List[Dict[str, Any]], title: str = "📋 Pending DNs") -> str:
        if not items:
            return f"{title}\n\n✅ No pending DNs found.\n\n0. Main Menu\n99. Back"
        
        lines = [title, ""]
        lines.append(f"Total: {len(items)}")
        lines.append("")
        
        for i, item in enumerate(items[:20], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            status = item.get('delivery_status', 'Pending')
            days = item.get('pending_days', 0)
            status_emoji = _get_status_emoji(status)
            lines.append(f"{i}. {status_emoji} *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   Status: {status}")
            if days > 0:
                lines.append(f"   Pending: {days} Days")
            lines.append("")
        
        if len(items) > 20:
            lines.append(f"... and {len(items) - 20} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_search_results(query: str, items: List[Dict[str, Any]]) -> str:
        if not items:
            return f"🔍 No results found for '{query}'\n\n0. Main Menu\n99. Back"
        
        lines = [f"🔍 *Search Results for '{query}'*", ""]
        lines.append(f"Found: {len(items)} DNs")
        lines.append("")
        
        for i, item in enumerate(items[:20], 1):
            dn_no = item.get('dn_no', 'N/A')
            customer = item.get('customer_name', item.get('customer_code', 'N/A'))
            city = item.get('ship_to_city', 'N/A')
            status = item.get('delivery_status', 'Pending')
            status_emoji = _get_status_emoji(status)
            lines.append(f"{i}. {status_emoji} *DN {dn_no}*")
            lines.append(f"   Customer: {customer}")
            lines.append(f"   City: {city} | Status: {status}")
            lines.append("")
        
        if len(items) > 20:
            lines.append(f"... and {len(items) - 20} more")
        
        lines.extend(["", "0. Main Menu", "99. Back"])
        return "\n".join(lines)
    
    @staticmethod
    def render_warehouse_dashboard(data: Dict[str, Any]) -> str:
        warehouse = data.get('warehouse', 'Unknown')
        return "\n".join([
            f"🏭 *Warehouse Dashboard - {warehouse}*",
            "",
            "📊 *Key Metrics*",
            f"Total DN: {data.get('total_dn', 0):,}",
            f"Pending DN: {data.get('pending_dn', 0):,}",
            f"Pending PGI: {data.get('pending_pgi', 0):,}",
            f"Pending POD: {data.get('pending_pod', 0):,}",
            f"Delivered: {data.get('delivered_dn', 0):,}",
            "",
            "💰 *Financials*",
            f"Revenue: PKR {data.get('total_revenue', 0):,.2f}",
            f"Quantity: {data.get('total_quantity', 0):,}",
            "",
            "📈 *Performance*",
            f"Delivery Success: {data.get('delivery_success_pct', 0):.1f}%",
            f"Pending Rate: {data.get('pending_rate', 0):.1f}%",
            f"SLA: {data.get('sla_compliance_pct', 0):.1f}%",
            "",
            "🏆 *Health*",
            f"Health Score: {data.get('health_score', 0):.1f}/100",
            f"Status: {data.get('overall_status', 'Unknown')}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    @staticmethod
    def render_comparison_result(item1: str, item2: str, metrics: Dict[str, Any]) -> str:
        lines = [
            f"🔄 *Comparison: {item1} vs {item2}*",
            "",
            "━━━━━━━━━━━━━━━━━━",
            "",
        ]
        
        for key, values in metrics.items():
            v1 = values.get('value1', 'N/A')
            v2 = values.get('value2', 'N/A')
            lines.append(f"{key}: {v1} vs {v2}")
        
        lines.extend([
            "",
            "0. Main Menu",
            "99. Back"
        ])
        return "\n".join(lines)

# ============================================================
# DN DASHBOARD BUILDER
# ============================================================

class DNDashboardBuilder:
    """Build DN dashboards from PostgreSQL"""
    
    def __init__(self, session: Session):
        self.session = session
    
    def get_pending_dns(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Get pending DNs"""
        try:
            today = date.today()
            
            results = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
                DeliveryReport.ship_to_city,
                DeliveryReport.warehouse,
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None),
                    DeliveryReport.delivery_status != 'Delivered'
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            items = []
            for row in results:
                days = (today - row.dn_create_date).days if row.dn_create_date else 0
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'pending_days': days,
                    'ship_to_city': _text(row.ship_to_city),
                    'warehouse': _text(row.warehouse),
                })
            
            return items
        except Exception as e:
            logger.error(f"Pending DNs error: {e}")
            return []
    
    def get_pending_pgi(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Get pending PGI DNs"""
        try:
            results = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
            ).filter(
                DeliveryReport.good_issue_date.is_(None)
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': 'Pending PGI',
                })
            
            return items
        except Exception as e:
            logger.error(f"Pending PGI error: {e}")
            return []
    
    def get_pending_pod(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Get pending POD DNs"""
        try:
            results = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.delivery_status,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None)
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(limit).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'delivery_status': 'Pending POD',
                })
            
            return items
        except Exception as e:
            logger.error(f"Pending POD error: {e}")
            return []
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dashboard"""
        try:
            # Main metrics
            metrics = self.session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.count(distinct(case(
                    (or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_dn'),
                func.count(distinct(case(
                    (DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no)
                ))).label('pending_pgi'),
                func.count(distinct(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)),
                     DeliveryReport.dn_no)
                ))).label('pending_pod'),
                func.count(distinct(case(
                    (DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)
                ))).label('delivered_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_delivery_days'),
                func.avg(case(
                    (and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)),
                     DeliveryReport.pod_date - DeliveryReport.good_issue_date)
                )).label('avg_pod_days'),
                func.avg(case(
                    (DeliveryReport.good_issue_date.isnot(None),
                     DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)
                )).label('avg_pgi_days'),
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse_name.lower()
            ).first()
            
            if not metrics:
                return {}
            
            total_dn = _safe_int(metrics.total_dn)
            pending_dn = _safe_int(metrics.pending_dn)
            delivered_dn = _safe_int(metrics.delivered_dn)
            
            delivery_success = (delivered_dn / total_dn * 100) if total_dn > 0 else 0
            pending_rate = (pending_dn / total_dn * 100) if total_dn > 0 else 0
            
            # Health score
            health_score = (
                delivery_success * 0.30 +
                (100 - pending_rate) * 0.25 +
                min(100, (_safe_float(metrics.total_revenue) / 1000000) * 10) * 0.15 +
                50
            )
            health_score = min(100, max(0, health_score))
            
            # SLA compliance
            sla_compliance = 100 if delivery_success > 95 else 80 if delivery_success > 85 else 60
            
            return {
                'warehouse': warehouse_name,
                'total_dn': total_dn,
                'pending_dn': pending_dn,
                'pending_pgi': _safe_int(metrics.pending_pgi),
                'pending_pod': _safe_int(metrics.pending_pod),
                'delivered_dn': delivered_dn,
                'total_quantity': _safe_int(metrics.total_quantity),
                'total_revenue': _safe_float(metrics.total_revenue),
                'avg_delivery_days': _safe_float(metrics.avg_delivery_days),
                'avg_pod_days': _safe_float(metrics.avg_pod_days),
                'avg_pgi_days': _safe_float(metrics.avg_pgi_days),
                'delivery_success_pct': delivery_success,
                'pending_rate': pending_rate,
                'sla_compliance_pct': sla_compliance,
                'health_score': health_score,
                'overall_status': 'Excellent' if health_score >= 85 else 'Good' if health_score >= 70 else 'Watch' if health_score >= 50 else 'Critical',
            }
        except Exception as e:
            logger.error(f"Warehouse dashboard error: {e}")
            return {}
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get DN dashboard"""
        try:
            result = self.session.query(
                DeliveryReport.dn_no,
                DeliveryReport.dn_work,
                DeliveryReport.order_type,
                DeliveryReport.division,
                DeliveryReport.customer_code,
                DeliveryReport.dealer_code,
                DeliveryReport.customer_name,
                DeliveryReport.dealer,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager,
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_location,
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                DeliveryReport.dn_qty,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            if not result:
                return {}
            
            today = date.today()
            dn_age = (today - result.dn_create_date).days if result.dn_create_date else 0
            
            return {
                'dn_no': _text(result.dn_no),
                'dn_work': _text(result.dn_work),
                'order_type': _text(result.order_type),
                'division': _text(result.division),
                'customer_code': _text(result.customer_code),
                'dealer_code': _text(result.dealer_code),
                'customer_name': _text(result.customer_name),
                'dealer_name': _text(result.dealer),
                'sales_office': _text(result.sales_office),
                'sales_manager': _text(result.sales_manager),
                'warehouse': _text(result.warehouse),
                'warehouse_code': _text(result.warehouse_code),
                'ship_to_city': _text(result.ship_to_city),
                'delivery_location': _text(result.delivery_location),
                'material_no': _text(result.material_no),
                'customer_model': _text(result.customer_model),
                'dn_qty': _safe_int(result.dn_qty),
                'dn_amount': _safe_float(result.dn_amount),
                'dn_create_date': result.dn_create_date,
                'good_issue_date': result.good_issue_date,
                'pod_date': result.pod_date,
                'delivery_status': _text(result.delivery_status, 'Pending'),
                'pgi_status': _text(result.pgi_status, 'Pending'),
                'pod_status': _text(result.pod_status, 'Pending'),
                'pending_flag': bool(result.pending_flag) if result.pending_flag is not None else True,
                'dn_age': dn_age,
            }
        except Exception as e:
            logger.error(f"DN dashboard error: {e}")
            return {}

# ============================================================
# MAIN DN INTELLIGENCE SERVICE
# ============================================================

class DNAnalysisService:
    """
    DN Intelligence Engine with Enterprise NLU
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
        self._service_name = "dn_analysis"
        self._version = "32.0"
        
        # Initialize components
        self._intent_engine = EnterpriseIntentEngine()
        self._menu_renderer = DNMenuRenderer()
        self._builder = None  # Will be created per session
        
        # Context memory
        self._contexts: Dict[str, DNContext] = {}
        self._context_lock = threading.RLock()
        
        logger.info("=" * 70)
        logger.info("🚀 DN INTELLIGENCE ENGINE v32.0 initialized")
        logger.info("   📦 Enterprise NLU with Hybrid Intent Detection")
        logger.info(f"   🧠 spaCy: {'✅' if SPACY_AVAILABLE else '❌'}")
        logger.info(f"   🧠 SentenceTransformer: {'✅' if SEMANTIC_AVAILABLE else '❌'}")
        logger.info(f"   🧠 RapidFuzz: {'✅' if RAPIDFUZZ_AVAILABLE else '❌'}")
        logger.info(f"   🧠 FlashRank: {'✅' if FLASHRANK_AVAILABLE else '❌'}")
        logger.info(f"   🧠 Semantic Router: {'✅' if SEMANTIC_ROUTER_AVAILABLE else '❌'}")
        logger.info("   🔑 ONLY '99' exits to main menu")
        logger.info("=" * 70)
    
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
                self._contexts[session_id].in_dn_service = True
            context = self._contexts[session_id]
            context.last_activity = datetime.now()
            return context
    
    def get_main_menu(self) -> str:
        return self._menu_renderer.render_main_menu()
    
    def process_whatsapp_query(self, message: str, sender: str = "default") -> str:
        """Main entry point"""
        if not message or not message.strip():
            return self.get_main_menu()
        
        message_clean = message.strip()
        logger.info(f"📦 DN Engine: '{message_clean}' from {sender}")
        
        context = self._get_context(sender)
        
        # ============================================================
        # STEP 1: Check for "99" - Exit
        # ============================================================
        if message_clean == "99":
            context.in_dn_service = False
            context.current_dn = None
            return "99"
        
        # ============================================================
        # STEP 2: Check for menu commands
        # ============================================================
        if message_clean.lower() in ["menu", "help", "options", "0"]:
            return self.get_main_menu()
        
        # ============================================================
        # STEP 3: Check for menu options (1-7)
        # ============================================================
        if message_clean in ["1", "2", "3", "4", "5", "6", "7"]:
            return self._handle_menu_option(sender, message_clean, context)
        
        # ============================================================
        # STEP 4: Check for DN number
        # ============================================================
        dn = _extract_dn(message_clean)
        if dn and _is_valid_dn(dn):
            context.current_dn = dn
            return self._get_dn_dashboard(sender, dn)
        
        # ============================================================
        # STEP 5: Check for pending commands
        # ============================================================
        if "pending" in message_clean.lower():
            if "pgi" in message_clean.lower():
                return self._get_pending_pgi(sender)
            elif "pod" in message_clean.lower():
                return self._get_pending_pod(sender)
            else:
                return self._get_pending_dns(sender)
        
        # ============================================================
        # STEP 6: Check for search
        # ============================================================
        if "search" in message_clean.lower():
            query = message_clean.replace("search", "").replace("find", "").replace("lookup", "").strip()
            if query:
                return self._search_dns(sender, query)
            return "🔍 Please specify what to search."
        
        # ============================================================
        # STEP 7: Check for compare
        # ============================================================
        if "compare" in message_clean.lower() or "vs" in message_clean.lower():
            dns = re.findall(r'\b(\d{8,12})\b', message_clean)
            if len(dns) >= 2:
                return self._compare_dns(sender, dns[0], dns[1])
            return "🔄 Please provide two DN numbers to compare."
        
        # ============================================================
        # STEP 8: Follow-up queries using current DN
        # ============================================================
        if context.current_dn:
            query_lower = message_clean.lower()
            if "status" in query_lower:
                return self._get_dn_status(sender, context.current_dn)
            elif "revenue" in query_lower or "amount" in query_lower:
                return self._get_dn_revenue(sender, context.current_dn)
            elif "units" in query_lower or "quantity" in query_lower or "qty" in query_lower:
                return self._get_dn_units(sender, context.current_dn)
            elif "customer" in query_lower:
                return self._get_dn_customer(sender, context.current_dn)
            elif "dealer" in query_lower:
                return self._get_dn_dealer(sender, context.current_dn)
            elif "warehouse" in query_lower:
                return self._get_dn_warehouse(sender, context.current_dn)
            elif "city" in query_lower:
                return self._get_dn_city(sender, context.current_dn)
        
        # ============================================================
        # STEP 9: Intent Detection - Route to handler
        # ============================================================
        intent_result = self._intent_engine.detect_intent(message_clean)
        logger.info(f"🎯 Intent: {intent_result.intent.value} (confidence: {intent_result.confidence:.2f})")
        
        # Check confidence threshold
        if intent_result.confidence < CONFIDENCE_THRESHOLD:
            return "\n".join([
                "❌ I'm not sure what you're asking.",
                "",
                "💡 *Please try one of these:*",
                "• Type a DN number (8-12 digits)",
                "• 'pending' - Show pending DNs",
                "• 'pending pgi' - Show pending PGI",
                "• 'pending pod' - Show pending POD",
                "• 'search [keyword]' - Search DNs",
                "• 'status' - Status of current DN",
                "• 'revenue' - Revenue of current DN",
                "• 'units' - Units of current DN",
                "",
                "0. Main Menu",
                "99. Back"
            ])
        
        # Route based on intent
        intent = intent_result.intent
        entities = intent_result.entities
        
        # Warehouse intents
        if intent == IntentType.WAREHOUSE_DASHBOARD:
            warehouse = entities.get("warehouse") or entities.get("location")
            if warehouse:
                return self._get_warehouse_dashboard(sender, warehouse)
            return "🏭 Please specify a warehouse name."
        
        if intent == IntentType.WAREHOUSE_QUANTITY:
            warehouse = entities.get("warehouse") or entities.get("location")
            if warehouse:
                return self._get_warehouse_quantity(sender, warehouse)
            return "📦 Please specify a warehouse name."
        
        if intent == IntentType.WAREHOUSE_REVENUE:
            warehouse = entities.get("warehouse") or entities.get("location")
            if warehouse:
                return self._get_warehouse_revenue(sender, warehouse)
            return "💰 Please specify a warehouse name."
        
        if intent == IntentType.WAREHOUSE_RANKING:
            return self._get_warehouse_ranking(sender)
        
        if intent == IntentType.WAREHOUSE_COMPARISON:
            warehouses = re.findall(r'([A-Za-z]+)', message_clean)
            if len(warehouses) >= 2:
                return self._compare_warehouses(sender, warehouses[0], warehouses[1])
            return "🔄 Please provide two warehouse names to compare."
        
        # Dealer intents
        if intent == IntentType.DEALER_DASHBOARD:
            dealer = entities.get("dealer") or entities.get("organization")
            if dealer:
                return self._get_dealer_dashboard(sender, dealer)
            return "🏪 Please specify a dealer name."
        
        if intent == IntentType.DEALER_RANKING:
            return self._get_dealer_ranking(sender)
        
        # City intents
        if intent == IntentType.CITY_DASHBOARD:
            city = entities.get("city") or entities.get("location")
            if city:
                return self._get_city_dashboard(sender, city)
            return "🏙️ Please specify a city name."
        
        # Product intents
        if intent == IntentType.PRODUCT_DASHBOARD:
            product = entities.get("product") or entities.get("organization")
            if product:
                return self._get_product_dashboard(sender, product)
            return "📦 Please specify a product name."
        
        # Pending intents
        if intent == IntentType.PENDING_DN:
            return self._get_pending_dns(sender)
        
        if intent == IntentType.PENDING_PGI:
            return self._get_pending_pgi(sender)
        
        if intent == IntentType.PENDING_POD:
            return self._get_pending_pod(sender)
        
        # AI intents
        if intent == IntentType.AI_INSIGHTS:
            if context.current_dn:
                return self._get_ai_insights(sender, context.current_dn)
            return "🤖 Please enter a DN number first.\n\n0. Main Menu\n99. Back"
        
        if intent == IntentType.RECOMMENDATIONS:
            return self._get_recommendations(sender)
        
        if intent == IntentType.ROOT_CAUSE:
            if context.current_dn:
                return self._get_root_cause(sender, context.current_dn)
            return "🔍 Please enter a DN number first.\n\n0. Main Menu\n99. Back"
        
        if intent == IntentType.EXECUTIVE_SUMMARY:
            return self._get_executive_summary(sender)
        
        # Forecast and trends
        if intent == IntentType.FORECAST:
            return self._get_forecast(sender)
        
        if intent == IntentType.TREND:
            return self._get_trends(sender)
        
        # SLA and transit
        if intent == IntentType.SLA_COMPLIANCE:
            if context.current_dn:
                return self._get_dn_sla(sender, context.current_dn)
            return "⚡ Please enter a DN number first.\n\n0. Main Menu\n99. Back"
        
        if intent == IntentType.TRANSIT_ANALYSIS:
            if context.current_dn:
                return self._get_transit_analysis(sender, context.current_dn)
            return "🚚 Please enter a DN number first.\n\n0. Main Menu\n99. Back"
        
        if intent == IntentType.DELIVERY_TIMELINE:
            if context.current_dn:
                return self._get_dn_timeline(sender, context.current_dn)
            return "📅 Please enter a DN number first.\n\n0. Main Menu\n99. Back"
        
        # Search and compare
        if intent == IntentType.SEARCH:
            return "🔍 Please specify what to search. Example: 'search Lahore'"
        
        if intent == IntentType.COMPARE:
            return "🔄 Please provide two DN numbers to compare."
        
        # ============================================================
        # STEP 10: Unknown - Show help
        # ============================================================
        return self._show_help()
    
    def _show_help(self) -> str:
        return "\n".join([
            "❌ I didn't understand that.",
            "",
            "💡 *DN Commands:*",
            "",
            "📊 *DN Queries:*",
            "• Type a DN number (8-12 digits) for dashboard",
            "• status - Status of current DN",
            "• revenue - Revenue of current DN",
            "• units - Units of current DN",
            "• customer - Customer of current DN",
            "• dealer - Dealer of current DN",
            "",
            "📋 *Pending:*",
            "• pending - Show pending DNs",
            "• pending pgi - Show pending PGI",
            "• pending pod - Show pending POD",
            "",
            "🔍 *Search:*",
            "• search [keyword] - Search DNs",
            "",
            "🔄 *Compare:*",
            "• compare DN1 DN2 - Compare DNs",
            "",
            "🤖 *AI:*",
            "• insights - AI insights",
            "• recommendations - Recommendations",
            "• root-cause - Root cause analysis",
            "",
            "📌 *Current DN:*",
            f"• {self._get_context('default').current_dn or 'None'}",
            "",
            "0. Main Menu",
            "99. Back"
        ])
    
    def _handle_menu_option(self, sender: str, option: str, context: DNContext) -> str:
        """Handle menu options"""
        if option == "1":
            if context.current_dn:
                return self._get_dn_dashboard(sender, context.current_dn)
            return "🔍 *Enter DN number:*\n\nType an 8-12 digit DN number.\n\n0. Main Menu\n99. Back"
        elif option == "2":
            return self._get_pending_dns(sender)
        elif option == "3":
            return "🔍 *Search DNs:*\n\nType 'search [keyword]' to find DNs.\n\n0. Main Menu\n99. Back"
        elif option == "4":
            return "🔄 *Compare DNs:*\n\nType 'compare DN1 DN2'\n\n0. Main Menu\n99. Back"
        elif option == "5":
            if context.current_dn:
                return self._get_ai_insights(sender, context.current_dn)
            return "🤖 *AI Insights*\n\nPlease enter a DN number first.\n\n0. Main Menu\n99. Back"
        elif option == "6":
            return self._get_trends(sender)
        elif option == "7":
            return self._get_forecast(sender)
        return self.get_main_menu()
    
    # ============================================================
    # DN OPERATIONS
    # ============================================================
    
    def _get_dn_dashboard(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data = builder.get_dn_dashboard(dn_no)
            session.close()
            
            if not data:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            # Render dashboard
            status = data.get('delivery_status', 'Pending')
            status_emoji = _get_status_emoji(status)
            dn_age = data.get('dn_age', 0)
            
            return "\n".join([
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                f"📦 *DN {dn_no}* {status_emoji}",
                "━━━━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "📊 *BASIC INFORMATION*",
                f"DN Work: {data.get('dn_work', 'N/A')}",
                f"Order Type: {data.get('order_type', 'N/A')}",
                f"Division: {data.get('division', 'N/A')}",
                "",
                "👤 *CUSTOMER*",
                f"Code: {data.get('customer_code', 'N/A')}",
                f"Name: {data.get('customer_name', 'N/A')}",
                "",
                "🏪 *DEALER*",
                f"Code: {data.get('dealer_code', 'N/A')}",
                f"Name: {data.get('dealer_name', 'N/A')}",
                "",
                "🏢 *SALES*",
                f"Office: {data.get('sales_office', 'N/A')}",
                f"Manager: {data.get('sales_manager', 'N/A')}",
                "",
                "🏭 *WAREHOUSE*",
                f"Name: {data.get('warehouse', 'N/A')}",
                f"Code: {data.get('warehouse_code', 'N/A')}",
                "",
                "📍 *DELIVERY*",
                f"City: {data.get('ship_to_city', 'N/A')}",
                f"Location: {data.get('delivery_location', 'N/A')}",
                "",
                "📦 *PRODUCT*",
                f"Material: {data.get('material_no', 'N/A')}",
                f"Model: {data.get('customer_model', 'N/A')}",
                "",
                "📊 *QUANTITIES*",
                f"Units: {data.get('dn_qty', 0):,}",
                f"Revenue: PKR {data.get('dn_amount', 0):,.2f}",
                "",
                "📅 *DATES*",
                f"Created: {_format_date(data.get('dn_create_date'))}",
                f"PGI: {_format_date(data.get('good_issue_date'))}",
                f"POD: {_format_date(data.get('pod_date'))}",
                "",
                "📈 *STATUS*",
                f"Delivery: {data.get('delivery_status', 'Pending')}",
                f"PGI: {data.get('pgi_status', 'Pending')}",
                f"POD: {data.get('pod_status', 'Pending')}",
                f"Pending: {'✅ Yes' if data.get('pending_flag') else '❌ No'}",
                f"DN Age: {dn_age} Days",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_status(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.delivery_status,
                DeliveryReport.pgi_status,
                DeliveryReport.pod_status,
                DeliveryReport.pending_flag,
                DeliveryReport.dn_create_date,
                DeliveryReport.customer_name,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📊 *DN {dn_no} - Status*",
                "",
                f"Status: {_text(result.delivery_status, 'Pending')}",
                f"PGI: {_text(result.pgi_status, 'Pending')}",
                f"POD: {_text(result.pod_status, 'Pending')}",
                f"Pending: {'✅ Yes' if result.pending_flag else '❌ No'}",
                "",
                f"Created: {_format_date(result.dn_create_date)}",
                f"Customer: {_text(result.customer_name)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Status error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching status for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_revenue(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_amount,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            amount = result.dn_amount or 0
            return f"💰 *DN {dn_no} Revenue*\n\nPKR {amount:,.2f}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Revenue error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching revenue for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_units(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_qty,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            qty = result.dn_qty or 0
            return f"📦 *DN {dn_no} Units*\n\n{qty:,}\n\n0. Main Menu\n99. Back"
            
        except Exception as e:
            logger.error(f"Units error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching units for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_customer(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"👤 *Customer - DN {dn_no}*",
                "",
                f"Name: {_text(result.customer_name)}",
                f"Code: {_text(result.customer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Customer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching customer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_dealer(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dealer,
                DeliveryReport.dealer_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏪 *Dealer - DN {dn_no}*",
                "",
                f"Name: {_text(result.dealer)}",
                f"Code: {_text(result.dealer_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_warehouse(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏭 *Warehouse - DN {dn_no}*",
                "",
                f"Name: {_text(result.warehouse)}",
                f"Code: {_text(result.warehouse_code)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Warehouse error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching warehouse for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_city(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.ship_to_city,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📍 *City - DN {dn_no}*",
                "",
                f"City: {_text(result.ship_to_city)}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"City error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching city for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_sla(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_create_date,
                DeliveryReport.pod_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            delivery_days = _calculate_days(result.dn_create_date, result.pod_date)
            sla_compliant = delivery_days is not None and delivery_days <= SLA_TARGET_DAYS
            
            return "\n".join([
                f"⚡ *SLA - DN {dn_no}*",
                "",
                f"Target: {SLA_TARGET_DAYS} Days",
                f"Actual: {delivery_days if delivery_days is not None else 'N/A'} Days",
                f"Compliant: {'✅ Yes' if sla_compliant else '❌ No'}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"SLA error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching SLA for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_transit_analysis(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
                DeliveryReport.warehouse,
                DeliveryReport.ship_to_city,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            pgi_days = _calculate_days(result.dn_create_date, result.good_issue_date)
            transit_days = _calculate_days(result.good_issue_date, result.pod_date)
            total_days = _calculate_days(result.dn_create_date, result.pod_date)
            
            return "\n".join([
                f"🚚 *Transit Analysis - DN {dn_no}*",
                "",
                f"Warehouse: {_text(result.warehouse)}",
                f"Destination: {_text(result.ship_to_city)}",
                "",
                "⏱️ *Timing*",
                f"Created: {_format_date(result.dn_create_date)}",
                f"PGI: {_format_date(result.good_issue_date)}",
                f"POD: {_format_date(result.pod_date)}",
                "",
                "📊 *Metrics*",
                f"PGI Days: {pgi_days if pgi_days is not None else 'N/A'}",
                f"Transit Days: {transit_days if transit_days is not None else 'N/A'}",
                f"Total Days: {total_days if total_days is not None else 'N/A'}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Transit error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching transit for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_dn_timeline(self, sender: str, dn_no: str) -> str:
        session = self._get_session()
        if not session:
            return f"⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date,
                DeliveryReport.pod_date,
            ).filter(
                DeliveryReport.dn_no == dn_no
            ).first()
            
            session.close()
            
            if not result:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            events = []
            if result.dn_create_date:
                events.append({
                    'timestamp': _format_date(result.dn_create_date),
                    'status': 'created',
                    'description': 'DN created'
                })
            if result.good_issue_date:
                events.append({
                    'timestamp': _format_date(result.good_issue_date),
                    'status': 'pgi',
                    'description': 'Goods issued from warehouse'
                })
            if result.pod_date:
                events.append({
                    'timestamp': _format_date(result.pod_date),
                    'status': 'delivered',
                    'description': 'Delivery completed - POD received'
                })
            
            if not events:
                return f"📅 *Timeline - DN {dn_no}*\n\nNo events found.\n\n0. Main Menu\n99. Back"
            
            lines = [f"📅 *Timeline - DN {dn_no}*", ""]
            for event in events:
                emoji = "📝" if event['status'] == 'created' else "🚚" if event['status'] == 'pgi' else "✅"
                lines.append(f"{emoji} *{event['timestamp']}*")
                lines.append(f"   {event['description']}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Timeline error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching timeline for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # PENDING OPERATIONS
    # ============================================================
    
    def _get_pending_dns(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            items = builder.get_pending_dns(30)
            session.close()
            return self._menu_renderer.render_pending_dns(items)
            
        except Exception as e:
            logger.error(f"Pending DNs error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending DNs.\n\n0. Main Menu\n99. Back"
    
    def _get_pending_pgi(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            items = builder.get_pending_pgi(30)
            session.close()
            return self._menu_renderer.render_pending_dns(items, "⏳ Pending PGI")
            
        except Exception as e:
            logger.error(f"Pending PGI error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending PGI.\n\n0. Main Menu\n99. Back"
    
    def _get_pending_pod(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            items = builder.get_pending_pod(30)
            session.close()
            return self._menu_renderer.render_pending_dns(items, "📋 Pending POD")
            
        except Exception as e:
            logger.error(f"Pending POD error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching pending POD.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # SEARCH AND COMPARE
    # ============================================================
    
    def _search_dns(self, sender: str, query: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            search_pattern = f"%{query}%"
            results = session.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                DeliveryReport.ship_to_city,
                DeliveryReport.delivery_status,
                DeliveryReport.warehouse,
                DeliveryReport.division,
            ).filter(
                or_(
                    DeliveryReport.dn_no.ilike(search_pattern),
                    DeliveryReport.customer_name.ilike(search_pattern),
                    DeliveryReport.customer_code.ilike(search_pattern),
                    DeliveryReport.ship_to_city.ilike(search_pattern),
                    DeliveryReport.warehouse.ilike(search_pattern),
                    DeliveryReport.division.ilike(search_pattern),
                    DeliveryReport.dealer.ilike(search_pattern),
                    DeliveryReport.sales_office.ilike(search_pattern),
                    DeliveryReport.sales_manager.ilike(search_pattern),
                    DeliveryReport.material_no.ilike(search_pattern),
                    DeliveryReport.customer_model.ilike(search_pattern),
                )
            ).order_by(
                desc(DeliveryReport.dn_create_date)
            ).limit(30).all()
            
            items = []
            for row in results:
                items.append({
                    'dn_no': _text(row.dn_no),
                    'customer_name': _text(row.customer_name, row.customer_code),
                    'customer_code': _text(row.customer_code),
                    'ship_to_city': _text(row.ship_to_city),
                    'delivery_status': _text(row.delivery_status, 'Pending'),
                    'warehouse': _text(row.warehouse),
                    'division': _text(row.division),
                })
            
            session.close()
            return self._menu_renderer.render_search_results(query, items)
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            if session:
                session.close()
            return f"⚠️ Error searching for '{query}'\n\n0. Main Menu\n99. Back"
    
    def _compare_dns(self, sender: str, dn1: str, dn2: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data1 = builder.get_dn_dashboard(dn1)
            data2 = builder.get_dn_dashboard(dn2)
            session.close()
            
            if not data1 or not data2:
                return "⚠️ One or both DNs not found.\n\n0. Main Menu\n99. Back"
            
            metrics = {
                "Revenue": {
                    "value1": f"PKR {data1.get('dn_amount', 0):,.2f}",
                    "value2": f"PKR {data2.get('dn_amount', 0):,.2f}"
                },
                "Units": {
                    "value1": f"{data1.get('dn_qty', 0):,}",
                    "value2": f"{data2.get('dn_qty', 0):,}"
                },
                "Status": {
                    "value1": data1.get('delivery_status', 'Pending'),
                    "value2": data2.get('delivery_status', 'Pending')
                },
                "Warehouse": {
                    "value1": data1.get('warehouse', 'N/A'),
                    "value2": data2.get('warehouse', 'N/A')
                },
                "City": {
                    "value1": data1.get('ship_to_city', 'N/A'),
                    "value2": data2.get('ship_to_city', 'N/A')
                },
                "Customer": {
                    "value1": data1.get('customer_name', 'N/A'),
                    "value2": data2.get('customer_name', 'N/A')
                },
                "Dealer": {
                    "value1": data1.get('dealer_name', 'N/A'),
                    "value2": data2.get('dealer_name', 'N/A')
                }
            }
            
            return self._menu_renderer.render_comparison_result(dn1, dn2, metrics)
            
        except Exception as e:
            logger.error(f"Comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing DNs.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # WAREHOUSE OPERATIONS
    # ============================================================
    
    def _get_warehouse_dashboard(self, sender: str, warehouse: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data = builder.get_warehouse_dashboard(warehouse)
            session.close()
            
            if not data:
                return f"⚠️ Warehouse '{warehouse}' not found.\n\n0. Main Menu\n99. Back"
            
            return self._menu_renderer.render_warehouse_dashboard(data)
            
        except Exception as e:
            logger.error(f"Warehouse dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching warehouse dashboard for {warehouse}\n\n0. Main Menu\n99. Back"
    
    def _get_warehouse_quantity(self, sender: str, warehouse: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                func.sum(DeliveryReport.dn_qty).label('total_qty'),
                func.sum(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_qty), else_=0)).label('pending_qty'),
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse.lower()
            ).first()
            
            session.close()
            
            total = _safe_int(result.total_qty)
            pending = _safe_int(result.pending_qty)
            
            return "\n".join([
                f"📦 *Warehouse Quantity - {warehouse}*",
                "",
                f"Total Quantity: {total:,}",
                f"Pending Quantity: {pending:,}",
                f"Delivered Quantity: {total - pending:,}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Warehouse quantity error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching quantity for {warehouse}\n\n0. Main Menu\n99. Back"
    
    def _get_warehouse_revenue(self, sender: str, warehouse: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_amount), else_=0)).label('pending_revenue'),
            ).filter(
                func.lower(DeliveryReport.warehouse) == warehouse.lower()
            ).first()
            
            session.close()
            
            total = _safe_float(result.total_revenue)
            pending = _safe_float(result.pending_revenue)
            
            return "\n".join([
                f"💰 *Warehouse Revenue - {warehouse}*",
                "",
                f"Total Revenue: PKR {total:,.2f}",
                f"Pending Revenue: PKR {pending:,.2f}",
                f"Delivered Revenue: PKR {total - pending:,.2f}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Warehouse revenue error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching revenue for {warehouse}\n\n0. Main Menu\n99. Back"
    
    def _get_warehouse_ranking(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.warehouse,
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_dn'),
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(10).all()
            
            items = []
            for row in results:
                items.append({
                    'warehouse': _text(row.warehouse),
                    'total_dn': _safe_int(row.total_dn),
                    'total_revenue': _safe_float(row.total_revenue),
                    'total_quantity': _safe_int(row.total_quantity),
                    'pending_dn': _safe_int(row.pending_dn),
                })
            
            session.close()
            
            lines = ["🏆 *Warehouse Ranking*", ""]
            for i, item in enumerate(items, 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                lines.append(f"{medal} *{item['warehouse']}*")
                lines.append(f"   Revenue: PKR {item['total_revenue']:,.2f}")
                lines.append(f"   DNs: {item['total_dn']:,} | Pending: {item['pending_dn']:,}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Warehouse ranking error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching warehouse ranking.\n\n0. Main Menu\n99. Back"
    
    def _compare_warehouses(self, sender: str, wh1: str, wh2: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data1 = builder.get_warehouse_dashboard(wh1)
            data2 = builder.get_warehouse_dashboard(wh2)
            session.close()
            
            if not data1 or not data2:
                return "⚠️ One or both warehouses not found.\n\n0. Main Menu\n99. Back"
            
            metrics = {
                "Revenue": {
                    "value1": f"PKR {data1.get('total_revenue', 0):,.2f}",
                    "value2": f"PKR {data2.get('total_revenue', 0):,.2f}"
                },
                "DNs": {
                    "value1": f"{data1.get('total_dn', 0):,}",
                    "value2": f"{data2.get('total_dn', 0):,}"
                },
                "Pending": {
                    "value1": f"{data1.get('pending_dn', 0):,}",
                    "value2": f"{data2.get('pending_dn', 0):,}"
                },
                "Success Rate": {
                    "value1": f"{data1.get('delivery_success_pct', 0):.1f}%",
                    "value2": f"{data2.get('delivery_success_pct', 0):.1f}%"
                },
                "Health Score": {
                    "value1": f"{data1.get('health_score', 0):.1f}/100",
                    "value2": f"{data2.get('health_score', 0):.1f}/100"
                }
            }
            
            return self._menu_renderer.render_comparison_result(wh1, wh2, metrics)
            
        except Exception as e:
            logger.error(f"Warehouse comparison error: {e}")
            if session:
                session.close()
            return f"⚠️ Error comparing warehouses.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # DEALER OPERATIONS
    # ============================================================
    
    def _get_dealer_dashboard(self, sender: str, dealer: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_dn'),
            ).filter(
                func.lower(DeliveryReport.customer_name) == dealer.lower()
            ).first()
            
            session.close()
            
            if not result or not result.total_dn:
                return f"⚠️ Dealer '{dealer}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏪 *Dealer Dashboard - {dealer}*",
                "",
                f"Total DNs: {_safe_int(result.total_dn):,}",
                f"Pending DNs: {_safe_int(result.pending_dn):,}",
                f"Total Quantity: {_safe_int(result.total_quantity):,}",
                f"Total Revenue: PKR {_safe_float(result.total_revenue):,.2f}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching dealer dashboard for {dealer}\n\n0. Main Menu\n99. Back"
    
    def _get_dealer_ranking(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            results = session.query(
                DeliveryReport.customer_name,
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(10).all()
            
            items = []
            for row in results:
                items.append({
                    'dealer': _text(row.customer_name),
                    'total_dn': _safe_int(row.total_dn),
                    'total_revenue': _safe_float(row.total_revenue),
                })
            
            session.close()
            
            lines = ["🏆 *Dealer Ranking*", ""]
            for i, item in enumerate(items, 1):
                medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                lines.append(f"{medal} *{item['dealer']}*")
                lines.append(f"   Revenue: PKR {item['total_revenue']:,.2f}")
                lines.append(f"   DNs: {item['total_dn']:,}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Dealer ranking error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching dealer ranking.\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # CITY OPERATIONS
    # ============================================================
    
    def _get_city_dashboard(self, sender: str, city: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_dn'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouses'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealers'),
            ).filter(
                func.lower(DeliveryReport.ship_to_city) == city.lower()
            ).first()
            
            session.close()
            
            if not result or not result.total_dn:
                return f"⚠️ City '{city}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"🏙️ *City Dashboard - {city}*",
                "",
                f"Total DNs: {_safe_int(result.total_dn):,}",
                f"Pending DNs: {_safe_int(result.pending_dn):,}",
                f"Total Quantity: {_safe_int(result.total_quantity):,}",
                f"Total Revenue: PKR {_safe_float(result.total_revenue):,.2f}",
                f"Warehouses: {_safe_int(result.warehouses):,}",
                f"Dealers: {_safe_int(result.dealers):,}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"City dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching city dashboard for {city}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # PRODUCT OPERATIONS
    # ============================================================
    
    def _get_product_dashboard(self, sender: str, product: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            result = session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_dn'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealers'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('cities'),
            ).filter(
                or_(
                    func.lower(DeliveryReport.customer_model).ilike(f"%{product.lower()}%"),
                    func.lower(DeliveryReport.material_no).ilike(f"%{product.lower()}%"),
                )
            ).first()
            
            session.close()
            
            if not result or not result.total_dn:
                return f"⚠️ Product '{product}' not found.\n\n0. Main Menu\n99. Back"
            
            return "\n".join([
                f"📦 *Product Dashboard - {product}*",
                "",
                f"Total DNs: {_safe_int(result.total_dn):,}",
                f"Pending DNs: {_safe_int(result.pending_dn):,}",
                f"Total Quantity: {_safe_int(result.total_quantity):,}",
                f"Total Revenue: PKR {_safe_float(result.total_revenue):,.2f}",
                f"Dealers: {_safe_int(result.dealers):,}",
                f"Cities: {_safe_int(result.cities):,}",
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
        except Exception as e:
            logger.error(f"Product dashboard error: {e}")
            if session:
                session.close()
            return f"⚠️ Error fetching product dashboard for {product}\n\n0. Main Menu\n99. Back"
    
    # ============================================================
    # TRENDS AND FORECAST
    # ============================================================
    
    def _get_trends(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from sqlalchemy import extract
            
            weekly = session.query(
                extract('week', DeliveryReport.dn_create_date).label('week'),
                func.count(distinct(DeliveryReport.dn_no)).label('count'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
            ).filter(
                DeliveryReport.dn_create_date.isnot(None)
            ).group_by(
                extract('week', DeliveryReport.dn_create_date)
            ).order_by(
                desc(extract('week', DeliveryReport.dn_create_date))
            ).limit(4).all()
            
            session.close()
            
            lines = ["📈 *DN Trends*", ""]
            
            for row in weekly:
                week = int(row.week)
                count = _safe_int(row.count)
                revenue = _safe_float(row.revenue)
                lines.append(f"Week {week}:")
                lines.append(f"   DNs: {count:,}")
                lines.append(f"   Revenue: PKR {revenue:,.2f}")
                lines.append("")
            
            lines.extend(["0. Main Menu", "99. Back"])
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Trends error: {e}")
            if session:
                session.close()
            return "⚠️ Error fetching trends.\n\n0. Main Menu\n99. Back"
    
    def _get_forecast(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            from datetime import timedelta
            
            results = session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total'),
                func.count(func.distinct(func.date(DeliveryReport.dn_create_date))).label('days'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
            ).filter(
                DeliveryReport.dn_create_date >= datetime.now().date() - timedelta(days=30)
            ).first()
            
            session.close()
            
            if not results or not results.days:
                return "🔮 Insufficient data for forecast.\n\n0. Main Menu\n99. Back"
            
            total = _safe_int(results.total)
            days = _safe_int(results.days)
            revenue = _safe_float(results.revenue)
            units = _safe_int(results.units)
            
            avg_daily = total / days if days > 0 else 0
            avg_daily_revenue = revenue / days if days > 0 else 0
            avg_daily_units = units / days if days > 0 else 0
            
            return "\n".join([
                "🔮 *DN Forecast*",
                "",
                f"Expected DNs: {int(avg_daily * 7):,}",
                f"Expected Revenue: PKR {avg_daily_revenue * 7:,.2f}",
                f"Expected Units: {int(avg_daily_units * 7):,}",
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
    
    # ============================================================
    # AI OPERATIONS
    # ============================================================
    
    def _get_ai_insights(self, sender: str, dn_no: str) -> str:
        if not GROQ_AVAILABLE or not USE_AI_ENHANCEMENT:
            return "🤖 AI insights are currently unavailable.\n\n0. Main Menu\n99. Back"
        
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data = builder.get_dn_dashboard(dn_no)
            session.close()
            
            if not data:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            # Build context for AI
            context_str = f"""
DN Number: {dn_no}
Customer: {data.get('customer_name', 'N/A')}
Dealer: {data.get('dealer_name', 'N/A')}
Warehouse: {data.get('warehouse', 'N/A')}
City: {data.get('ship_to_city', 'N/A')}
Status: {data.get('delivery_status', 'Pending')}
Revenue: PKR {data.get('dn_amount', 0):,.2f}
Units: {data.get('dn_qty', 0):,}
Created: {_format_date(data.get('dn_create_date'))}
PGI: {_format_date(data.get('good_issue_date'))}
POD: {_format_date(data.get('pod_date'))}
DN Age: {data.get('dn_age', 0)} Days
"""
            
            prompt = f"""You are a logistics DN expert. Provide insights and analysis on this DN.

DN Data:
{context_str}

Provide:
1. Key findings
2. What it tells us
3. Business implications
4. Any concerns or recommendations

Keep it concise for WhatsApp. Use emojis. Max 250 words."""
            
            try:
                client = Groq()
                response = client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": "You are a logistics DN expert. Provide concise, business-focused analysis for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=400
                )
                ai_response = response.choices[0].message.content
                
                return "\n".join([
                    f"🤖 *AI Insights - DN {dn_no}*",
                    "",
                    ai_response,
                    "",
                    "0. Main Menu",
                    "99. Back"
                ])
            except Exception as e:
                logger.error(f"AI generation error: {e}")
                return f"🤖 AI insights temporarily unavailable.\n\n0. Main Menu\n99. Back"
                
        except Exception as e:
            logger.error(f"AI insights error: {e}")
            if session:
                session.close()
            return f"⚠️ Error generating AI insights for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_recommendations(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            pending_count = session.query(
                func.count(distinct(DeliveryReport.dn_no))
            ).filter(
                or_(
                    DeliveryReport.pending_flag.is_(True),
                    DeliveryReport.pod_date.is_(None)
                )
            ).scalar() or 0
            
            session.close()
            
            recommendations = []
            
            if pending_count > 50:
                recommendations.append(f"🚨 {pending_count} pending DNs need immediate attention")
            elif pending_count > 20:
                recommendations.append(f"📋 Review {pending_count} pending DNs for timely closure")
            else:
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
    
    def _get_root_cause(self, sender: str, dn_no: str) -> str:
        if not GROQ_AVAILABLE or not USE_AI_ENHANCEMENT:
            return "🔍 Root cause analysis is currently unavailable.\n\n0. Main Menu\n99. Back"
        
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            builder = DNDashboardBuilder(session)
            data = builder.get_dn_dashboard(dn_no)
            session.close()
            
            if not data:
                return f"⚠️ DN '{dn_no}' not found.\n\n0. Main Menu\n99. Back"
            
            status = data.get('delivery_status', 'Pending')
            dn_age = data.get('dn_age', 0)
            
            if status == "Delivered" or status == "Completed":
                return f"✅ *DN {dn_no}*\n\nThis DN is already delivered. No root cause analysis needed.\n\n0. Main Menu\n99. Back"
            
            # Build context
            context_str = f"""
DN Number: {dn_no}
Status: {status}
DN Age: {dn_age} Days
Customer: {data.get('customer_name', 'N/A')}
Warehouse: {data.get('warehouse', 'N/A')}
City: {data.get('ship_to_city', 'N/A')}
Created: {_format_date(data.get('dn_create_date'))}
PGI: {_format_date(data.get('good_issue_date'))}
POD: {_format_date(data.get('pod_date'))}
"""
            
            prompt = f"""You are a logistics DN expert. Analyze why this DN is delayed or problematic.

DN Data:
{context_str}

Provide:
1. Root cause analysis
2. Contributing factors
3. Impact on business
4. What went wrong
5. Recommendations to fix

Keep it concise for WhatsApp. Use emojis. Max 200 words."""
            
            try:
                client = Groq()
                response = client.chat.completions.create(
                    model="llama3-70b-8192",
                    messages=[
                        {"role": "system", "content": "You are a logistics DN expert. Provide concise analysis for WhatsApp."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=350
                )
                ai_response = response.choices[0].message.content
                
                return "\n".join([
                    f"🔍 *Root Cause Analysis - DN {dn_no}*",
                    "",
                    ai_response,
                    "",
                    "0. Main Menu",
                    "99. Back"
                ])
            except Exception as e:
                logger.error(f"AI root cause error: {e}")
                return f"🔍 Root cause analysis temporarily unavailable.\n\n0. Main Menu\n99. Back"
                
        except Exception as e:
            logger.error(f"Root cause error: {e}")
            if session:
                session.close()
            return f"⚠️ Error analyzing root cause for DN {dn_no}\n\n0. Main Menu\n99. Back"
    
    def _get_executive_summary(self, sender: str) -> str:
        session = self._get_session()
        if not session:
            return "⚠️ Database unavailable.\n\n0. Main Menu\n99. Back"
        
        try:
            # Get summary statistics
            stats = session.query(
                func.count(distinct(DeliveryReport.dn_no)).label('total_dn'),
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_revenue'),
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_dn'),
                func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label('delivered_dn'),
                func.count(distinct(DeliveryReport.warehouse)).label('warehouses'),
                func.count(distinct(DeliveryReport.customer_name)).label('dealers'),
                func.count(distinct(DeliveryReport.ship_to_city)).label('cities'),
            ).first()
            
            # Get top warehouse by pending
            top_warehouse = session.query(
                DeliveryReport.warehouse,
                func.count(distinct(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_no)))).label('pending_count'),
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).order_by(
                desc('pending_count')
            ).first()
            
            session.close()
            
            total_dn = _safe_int(stats.total_dn)
            delivered = _safe_int(stats.delivered_dn)
            pending = _safe_int(stats.pending_dn)
            revenue = _safe_float(stats.total_revenue)
            warehouses = _safe_int(stats.warehouses)
            dealers = _safe_int(stats.dealers)
            cities = _safe_int(stats.cities)
            
            delivery_rate = (delivered / total_dn * 100) if total_dn > 0 else 0
            
            lines = [
                "📋 *Executive Summary*",
                "",
                "📊 *National Overview*",
                f"Total DNs: {total_dn:,}",
                f"Delivered: {delivered:,} ({delivery_rate:.1f}%)",
                f"Pending: {pending:,}",
                f"Revenue: PKR {revenue:,.2f}",
                "",
                "🏭 *Network*",
                f"Warehouses: {warehouses:,}",
                f"Dealers: {dealers:,}",
                f"Cities: {cities:,}",
            ]
            
            if top_warehouse and top_warehouse.warehouse:
                lines.extend([
                    "",
                    "⚠️ *Top Pending Warehouse*",
                    f"{top_warehouse.warehouse}: {_safe_int(top_warehouse.pending_count)} pending DNs",
                ])
            
            if pending > 0:
                lines.extend([
                    "",
                    "🎯 *Recommendation*",
                    f"Focus on clearing {pending} pending DNs.",
                ])
            else:
                lines.extend([
                    "",
                    "✅ *Status*",
                    "No pending DNs. Excellent performance!",
                ])
            
            lines.extend([
                "",
                "0. Main Menu",
                "99. Back"
            ])
            
            return "\n".join(lines)
            
        except Exception as e:
            logger.error(f"Executive summary error: {e}")
            if session:
                session.close()
            return "⚠️ Error generating executive summary.\n\n0. Main Menu\n99. Back"
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": self._service_name,
            "version": self._version,
            "status": "healthy",
            "database": "connected" if DB_AVAILABLE else "disconnected",
            "libraries": {
                "spacy": SPACY_AVAILABLE,
                "sentence_transformers": SEMANTIC_AVAILABLE,
                "rapidfuzz": RAPIDFUZZ_AVAILABLE,
                "semantic_router": SEMANTIC_ROUTER_AVAILABLE,
                "flashrank": FLASHRANK_AVAILABLE,
                "groq": GROQ_AVAILABLE,
            },
            "timestamp": datetime.now().isoformat()
        }

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

__all__ = [
    "DNAnalysisService",
    "get_dn_analysis_service",
    "process_dn_menu",
    "get_dn_main_menu",
]
