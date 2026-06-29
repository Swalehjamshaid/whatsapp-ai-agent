"""
File: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
Enterprise Dealer Intelligence Engine - AI-Powered with Pydantic AI, Instructor, SQLGlot, PGVector, PyArrow
Ultra-fast responses (< 1 second)
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional, Dict, List, Tuple, Union
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from cachetools import TTLCache, LRUCache
from rapidfuzz import fuzz, process
from sqlalchemy import and_, case, distinct, func, or_, text, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

try:
    from app.services.ai_bootstrap_service import get_ai_provider_manager
except ImportError:
    get_ai_provider_manager = None  # type: ignore[assignment]

# ============================================================
# ENTERPRISE AI LIBRARIES - LATEST VERSIONS
# ============================================================

# 1. Pydantic AI - Structured AI Responses
try:
    from pydantic_ai import Agent, RunContext
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models.groq import GroqModel
except ImportError:
    Agent = None
    RunContext = None
    OpenAIModel = None
    OpenAIProvider = None
    GroqModel = None

# 2. Instructor - Structured Output
try:
    import instructor
    from instructor import Instructor
    from instructor.patch import patch
except ImportError:
    instructor = None
    Instructor = None

# 3. SQLGlot - Advanced SQL Parsing & Optimization
try:
    import sqlglot
    from sqlglot import parse_one, optimize
    from sqlglot.optimizer import optimize_queries
except ImportError:
    sqlglot = None

# 4. PGVector - PostgreSQL Vector Search
try:
    from pgvector.sqlalchemy import Vector
    from pgvector.utils import vector_norm, vector_dim
except ImportError:
    Vector = None

# 5. PyArrow - Ultra-fast Data Processing
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    import pyarrow.compute as pc
except ImportError:
    pa = None
    pq = None
    pc = None

# ============================================================
# AI PROVIDER LIBRARIES
# ============================================================

try:
    import openai
    from openai import OpenAI
except ImportError:
    openai = None
    OpenAI = None

try:
    import groq
    from groq import Groq
except ImportError:
    groq = None
    Groq = None

try:
    import anthropic
    from anthropic import Anthropic
except ImportError:
    anthropic = None
    Anthropic = None

try:
    from sentence_transformers import SentenceTransformer
    import torch
except ImportError:
    SentenceTransformer = None
    torch = None

try:
    import numpy as np
    import pandas as pd
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    np = None
    pd = None
    TfidfVectorizer = None
    cosine_similarity = None

logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY") or os.getenv("ORS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

CACHE_TTL = max(300, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "21600")))
USE_AI = os.getenv("USE_AI_ENHANCEMENTS", "true").lower() == "true"
AI_PROVIDER = os.getenv("AI_PROVIDER", "openai")  # openai, groq, anthropic, or local

# Pre-compile regex patterns for speed
_STOP_PHRASES_PATTERN = re.compile(
    r'\b(?:tell me about|dealer dashboard|dealer profile|dealer performance|'
    r'dealer statistics|dealer revenue|dealer distance|dealer pending|'
    r'dealer status|dealer pod|dealer pgi|show|display|dealer|'
    r'profile|statistics|performance|status|revenue|distance|'
    r'pending|dashboard|about|of|the|company|private|'
    r'limited|pvt|ltd)\b'
)
_WHITESPACE_PATTERN = re.compile(r'\s+')
_SPECIAL_CHARS_PATTERN = re.compile(r'[^a-z0-9\s]')

# Thread pool for parallel operations
_executor = ThreadPoolExecutor(max_workers=10)


def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return _text(value, "N/A")


def _status_complete(column: Any) -> Any:
    return func.lower(func.coalesce(column, "")).in_(("completed", "complete", "delivered", "done", "yes"))


# ============================================================
# SQLGlot - SQL Optimization Engine (Ultra-Fast)
# ============================================================

class SQLOptimizer:
    """Advanced SQL Optimization using SQLGlot with caching"""
    
    _optimization_cache = LRUCache(maxsize=1000)
    
    @classmethod
    def optimize_query(cls, sql_query: str) -> str:
        """Optimize SQL query for better performance with caching"""
        cache_key = f"sql_opt_{hash(sql_query)}"
        if cache_key in cls._optimization_cache:
            return cls._optimization_cache[cache_key]
        
        if not sqlglot:
            return sql_query
        
        try:
            parsed = parse_one(sql_query)
            optimized = optimize(parsed, dialect="postgres")
            result = optimized.sql(dialect="postgres")
            cls._optimization_cache[cache_key] = result
            return result
        except Exception:
            return sql_query
    
    @classmethod
    def analyze_query(cls, sql_query: str) -> Dict[str, Any]:
        """Analyze SQL query structure"""
        if not sqlglot:
            return {}
        
        try:
            parsed = parse_one(sql_query)
            return {
                "tables": [t.name for t in parsed.find_all(sqlglot.expressions.Table)],
                "columns": [c.name for c in parsed.find_all(sqlglot.expressions.Column)],
                "has_joins": bool(parsed.find_all(sqlglot.expressions.Join)),
                "has_where": bool(parsed.find_all(sqlglot.expressions.Where)),
                "has_group_by": bool(parsed.find_all(sqlglot.expressions.Group)),
            }
        except Exception:
            return {}


# ============================================================
# PGVector - Semantic Search Engine (Ultra-Fast)
# ============================================================

class SemanticSearchEngine:
    """Semantic search using PGVector with caching"""
    
    _embedding_cache = LRUCache(maxsize=10000)
    
    def __init__(self):
        self.encoder = None
        try:
            manager = get_ai_provider_manager() if get_ai_provider_manager else None
            self.encoder = manager.get_embeddings() if manager else None
        except Exception as e:
            logger.warning("Shared SentenceTransformer unavailable: %s", e)
    
    def encode_text(self, text: str) -> List[float]:
        """Encode text to vector embedding with caching"""
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


# ============================================================
# PyArrow - Ultra-Fast Data Processing
# ============================================================

class PyArrowProcessor:
    """Ultra-fast data processing with PyArrow"""
    
    @staticmethod
    def to_arrow(data: List[Dict]) -> Any:
        """Convert data to PyArrow Table for fast processing"""
        if not pa:
            return data
        
        try:
            return pa.Table.from_pylist(data)
        except Exception:
            return data
    
    @staticmethod
    def filter_fast(table: Any, column: str, value: Any) -> Any:
        """Fast filter using PyArrow compute"""
        if not pa or not pc:
            return table
        
        try:
            return table.filter(pc.field(column) == value)
        except Exception:
            return table


# ============================================================
# PYDANTIC AI AGENT FOR HUMAN-LIKE RESPONSES
# ============================================================

@dataclass
class AIResponse:
    """Structured AI response"""
    dealer_name: str
    answer: str
    insights: List[str]
    recommendations: List[str]
    sentiment: str
    confidence: float
    metadata: Dict[str, Any]


class AIDealerAgent:
    """AI Agent using Pydantic AI, Instructor, and Groq/OpenAI for human-like responses"""
    
    def __init__(self):
        self.agent = None
        self.instructor_client = None
        self.semantic_search = SemanticSearchEngine()
        self._initialize_ai_agent()
        self._response_cache = TTLCache(maxsize=1000, ttl=300)  # 5 min cache
    
    def _initialize_ai_agent(self):
        """Initialize AI agent with Pydantic AI and Instructor"""
        if not USE_AI:
            return
        
        # Initialize with Groq (fastest) or OpenAI
        if GROQ_API_KEY and GroqModel is not None:
            try:
                self.agent = Agent(
                    GroqModel('llama-3.1-70b-versatile', api_key=GROQ_API_KEY),
                    system_prompt="You are a Dealer Intelligence Expert. Provide concise, actionable insights."
                )
                logger.info("AI Agent initialized with Groq")
                return
            except Exception as e:
                logger.warning(f"Groq initialization failed: {e}")
        
        if OPENAI_API_KEY and Agent is not None:
            try:
                self.agent = Agent(
                    OpenAIModel('gpt-4o-mini', api_key=OPENAI_API_KEY),
                    system_prompt="You are a Dealer Intelligence Expert. Provide concise, actionable insights."
                )
                logger.info("AI Agent initialized with OpenAI")
                return
            except Exception as e:
                logger.warning(f"OpenAI initialization failed: {e}")
        
        # Instructor fallback
        if instructor and OpenAI:
            try:
                self.instructor_client = instructor.from_openai(OpenAI(api_key=OPENAI_API_KEY))
                logger.info("Instructor client initialized")
            except Exception as e:
                logger.warning(f"Instructor initialization failed: {e}")
        
        logger.warning("AI Agent not initialized - using fallback responses")
    
    def generate_response(self, dealer_name: str, data: Dict[str, Any], question: str) -> AIResponse:
        """Generate AI-powered response"""
        cache_key = f"{dealer_name}_{hash(question)}_{hash(str(data))}"
        
        # Check cache for < 1s response
        if cache_key in self._response_cache:
            return self._response_cache[cache_key]
        
        try:
            if self.agent:
                return self._generate_with_pydantic_ai(dealer_name, data, question)
            elif self.instructor_client:
                return self._generate_with_instructor(dealer_name, data, question)
            else:
                return self._generate_fallback(dealer_name, data, question)
        except Exception as e:
            logger.error(f"AI response generation failed: {e}")
            return self._generate_fallback(dealer_name, data, question)
    
    def _generate_with_pydantic_ai(self, dealer_name: str, data: Dict[str, Any], question: str) -> AIResponse:
        """Generate response using Pydantic AI"""
        try:
            # Prepare context
            context = self._prepare_context(data)
            
            # Run the agent
            response = self.agent.run_sync(
                f"Dealer: {dealer_name}\nQuestion: {question}\nData: {context}"
            )
            
            result = AIResponse(
                dealer_name=dealer_name,
                answer=response.data,
                insights=self._extract_insights(data),
                recommendations=self._generate_recommendations(data),
                sentiment=self._analyze_sentiment(data),
                confidence=0.95,
                metadata={"provider": "pydantic-ai", "model": "groq/llama-3.1-70b"}
            )
            
            # Cache the result
            self._response_cache[f"{dealer_name}_{hash(question)}_{hash(str(data))}"] = result
            return result
            
        except Exception as e:
            logger.warning(f"Pydantic AI failed: {e}")
            return self._generate_fallback(dealer_name, data, question)
    
    def _generate_with_instructor(self, dealer_name: str, data: Dict[str, Any], question: str) -> AIResponse:
        """Generate response using Instructor"""
        try:
            # Structured extraction
            response = self.instructor_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a Dealer Intelligence Expert. Extract insights from dealer data."},
                    {"role": "user", "content": f"Dealer: {dealer_name}\nQuestion: {question}\nData: {self._prepare_context(data)}"}
                ],
                response_model=AIResponse,
            )
            
            # Cache the result
            self._response_cache[f"{dealer_name}_{hash(question)}_{hash(str(data))}"] = response
            return response
            
        except Exception as e:
            logger.warning(f"Instructor failed: {e}")
            return self._generate_fallback(dealer_name, data, question)
    
    def _generate_fallback(self, dealer_name: str, data: Dict[str, Any], question: str) -> AIResponse:
        """Generate fallback response without AI"""
        insights = self._extract_insights(data)
        recommendations = self._generate_recommendations(data)
        
        return AIResponse(
            dealer_name=dealer_name,
            answer=self._format_fallback_answer(data, question),
            insights=insights,
            recommendations=recommendations,
            sentiment=self._analyze_sentiment(data),
            confidence=0.70,
            metadata={"provider": "fallback", "model": "rule-based"}
        )
    
    def _prepare_context(self, data: Dict[str, Any]) -> str:
        """Prepare context for AI"""
        return "\n".join([f"{k}: {v}" for k, v in data.items() if v is not None])
    
    def _extract_insights(self, data: Dict[str, Any]) -> List[str]:
        """Extract key insights from data"""
        insights = []
        
        # Revenue insights
        revenue = data.get('total_revenue', 0)
        if revenue > 10000000:
            insights.append(f"High revenue: PKR {revenue:,.2f}")
        elif revenue < 1000000:
            insights.append(f"Low revenue: PKR {revenue:,.2f} - needs attention")
        
        # Delivery insights
        delivery_pct = data.get('delivery_success_pct', 0)
        if delivery_pct >= 95:
            insights.append(f"Excellent delivery rate: {delivery_pct:.1f}%")
        elif delivery_pct < 80:
            insights.append(f"Delivery rate needs improvement: {delivery_pct:.1f}%")
        
        # Pending insights
        pending = data.get('pending_dn', 0)
        if pending > 10:
            insights.append(f"High pending DNs: {pending} - requires immediate attention")
        
        # PGI/POD insights
        pod_pct = data.get('pod_success_pct', 0)
        if pod_pct < 80:
            insights.append(f"POD completion is low: {pod_pct:.1f}%")
        
        return insights[:5]  # Limit to top 5 insights
    
    def _generate_recommendations(self, data: Dict[str, Any]) -> List[str]:
        """Generate actionable recommendations"""
        recommendations = []
        
        pending = data.get('pending_dn', 0)
        if pending > 10:
            recommendations.append(f"Escalate {pending} pending DNs immediately")
        
        pod_pct = data.get('pod_success_pct', 0)
        if pod_pct < 80:
            recommendations.append("Prioritize POD collection and closure")
        
        delivery_pct = data.get('delivery_success_pct', 0)
        if delivery_pct < 85:
            recommendations.append("Review delivery process for improvement")
        
        if not recommendations:
            recommendations.append("Maintain current delivery and POD control process")
            recommendations.append("Continue monitoring key performance indicators")
        
        return recommendations[:3]
    
    def _analyze_sentiment(self, data: Dict[str, Any]) -> str:
        """Analyze dealer sentiment based on metrics"""
        score = 0
        
        # Revenue sentiment
        revenue = data.get('total_revenue', 0)
        if revenue > 5000000:
            score += 30
        elif revenue > 1000000:
            score += 15
        
        # Delivery sentiment
        delivery_pct = data.get('delivery_success_pct', 0)
        if delivery_pct >= 90:
            score += 30
        elif delivery_pct >= 70:
            score += 15
        
        # Pending sentiment
        pending = data.get('pending_dn', 0)
        if pending <= 5:
            score += 20
        elif pending <= 15:
            score += 10
        
        # POD sentiment
        pod_pct = data.get('pod_success_pct', 0)
        if pod_pct >= 90:
            score += 20
        elif pod_pct >= 70:
            score += 10
        
        if score >= 80:
            return "Excellent"
        elif score >= 60:
            return "Good"
        elif score >= 40:
            return "Fair"
        else:
            return "Needs Improvement"
    
    def _format_fallback_answer(self, data: Dict[str, Any], question: str) -> str:
        """Format fallback answer"""
        dealer = data.get('dealer_name', 'Unknown')
        revenue = data.get('total_revenue', 0)
        dns = data.get('total_dn', 0)
        pending = data.get('pending_dn', 0)
        delivery = data.get('delivery_success_pct', 0)
        
        return (
            f"ðŸ“Š Dealer Summary\n\n"
            f"Dealer: {dealer}\n"
            f"Revenue: PKR {revenue:,.2f}\n"
            f"Total DNs: {dns:,}\n"
            f"Pending DNs: {pending:,}\n"
            f"Delivery Success: {delivery:.1f}%\n\n"
            f"ðŸ’¡ Key Insight\n"
            f"{self._extract_insights(data)[0] if self._extract_insights(data) else 'Performance is stable'}"
        )


# ============================================================
# INTENT AND METRIC ENUMS
# ============================================================

class DealerIntent(Enum):
    DEALER_INFO = "dealer_info"
    REVENUE = "revenue"
    DN = "dn"
    UNITS = "units"
    DELIVERY = "delivery"
    PGI = "pgi"
    POD = "pod"
    PENDING = "pending"
    PRODUCT = "product"
    WAREHOUSE = "warehouse"
    RANKING = "ranking"
    HEALTH = "health"
    DASHBOARD = "dashboard"
    PROFILE = "profile"
    COMPARISON = "comparison"
    UNKNOWN = "unknown"


# ============================================================
# INTENT RESOLVER
# ============================================================

class IntentResolver:
    """Resolves user questions to dealer intents"""
    
    INTENT_PATTERNS = {
        r'\b(?:tell me about|about|who is|what is)\s+(\w+)': DealerIntent.DEALER_INFO,
        r'\bdealer (?:info|information|details|summary|overview)\b': DealerIntent.DEALER_INFO,
        r'\b(?:sales manager|manager)\b': DealerIntent.DEALER_INFO,
        r'\b(?:sales office|office|region)\b': DealerIntent.DEALER_INFO,
        r'\brevenue\b': DealerIntent.REVENUE,
        r'\bsales\b': DealerIntent.REVENUE,
        r'\bincome\b': DealerIntent.REVENUE,
        r'\bturnover\b': DealerIntent.REVENUE,
        r'\brevenue (?:growth|trend|change)\b': DealerIntent.REVENUE,
        r'\bdn\b': DealerIntent.DN,
        r'\bdelivery note\b': DealerIntent.DN,
        r'\bunits?\b': DealerIntent.UNITS,
        r'\bquantity\b': DealerIntent.UNITS,
        r'\bdelivery\b': DealerIntent.DELIVERY,
        r'\bdelivery (?:success|performance)\b': DealerIntent.DELIVERY,
        r'\bpgi\b': DealerIntent.PGI,
        r'\bgood issue\b': DealerIntent.PGI,
        r'\bpod\b': DealerIntent.POD,
        r'\bproof of delivery\b': DealerIntent.POD,
        r'\bpending\b': DealerIntent.PENDING,
        r'\boutstanding\b': DealerIntent.PENDING,
        r'\bproduct\b': DealerIntent.PRODUCT,
        r'\bmodel\b': DealerIntent.PRODUCT,
        r'\bwarehouse\b': DealerIntent.WAREHOUSE,
        r'\bdepot\b': DealerIntent.WAREHOUSE,
        r'\brank\b': DealerIntent.RANKING,
        r'\branking\b': DealerIntent.RANKING,
        r'\bbusiness (?:score|health)\b': DealerIntent.HEALTH,
        r'\boverall status\b': DealerIntent.HEALTH,
        r'\bdashboard\b': DealerIntent.DASHBOARD,
        r'\bprofile\b': DealerIntent.PROFILE,
        r'\bcompare\b': DealerIntent.COMPARISON,
        r'\bvs\b': DealerIntent.COMPARISON,
    }
    
    @classmethod
    def resolve_intent(cls, question: str) -> DealerIntent:
        """Resolve intent from question"""
        question_lower = question.lower()
        
        # Check dashboard first
        if 'dashboard' in question_lower:
            return DealerIntent.DASHBOARD
        if 'profile' in question_lower or 'summary' in question_lower:
            return DealerIntent.PROFILE
        
        # Check comparison
        if 'compare' in question_lower or 'vs' in question_lower:
            return DealerIntent.COMPARISON
        
        # Check patterns
        for pattern, intent in cls.INTENT_PATTERNS.items():
            if re.search(pattern, question_lower):
                return intent
        
        return DealerIntent.UNKNOWN


# ============================================================
# DATACLASSES
# ============================================================

@dataclass
class DistanceAnalytics:
    warehouse: str
    dealer_city: str
    distance_km: Optional[float] = None
    estimated_driving_minutes: Optional[int] = None
    estimated_driving_time: str = "Unknown"
    estimated_delivery_time: str = "Unknown"
    source: str = "unavailable"


@dataclass
class DealerDashboard:
    dealer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    sales_office: str
    sales_manager: str
    division: str
    total_dn: int
    completed_dn: int
    pending_dn: int
    total_units: int
    total_revenue: float
    average_revenue_per_dn: float
    average_units_per_dn: float
    first_delivery_date: str
    latest_delivery_date: str
    average_delivery_days: float
    average_pod_days: float
    average_total_cycle_time: float
    delivery_success_pct: float
    pgi_success_pct: float
    pod_success_pct: float
    pending_pct: float
    distance: DistanceAnalytics
    delivery_location: str = "Unknown"
    revenue_rank: Optional[int] = None
    delivery_rank: Optional[int] = None
    busiest_month: str = "Unknown"
    strongest_product_category: str = "Unknown"
    weakest_product_category: str = "Unknown"
    revenue_growth_pct: Optional[float] = None
    insights: list[str] = field(default_factory=list)
    delivered_units: int = 0
    pending_units: int = 0
    delivered_revenue: float = 0.0
    pending_revenue: float = 0.0
    pgi_pending_dn: int = 0
    pod_pending_dn: int = 0
    delivery_pending_dn: int = 0
    oldest_pending_dn: str = "N/A"
    oldest_pending_days: int = 0
    newest_dn: str = "N/A"
    highest_revenue_dn: str = "N/A"
    lowest_revenue_dn: str = "N/A"
    highest_unit_dn: str = "N/A"
    lowest_unit_dn: str = "N/A"
    average_revenue_per_unit: float = 0.0
    warehouse_utilization: float = 0.0
    delivery_coverage: float = 0.0
    top_product: str = "Unknown"
    top_model: str = "Unknown"
    top_material: str = "Unknown"
    current_month_revenue: float = 0.0
    previous_month_revenue: float = 0.0
    monthly_growth: float = 0.0
    current_month_dn: int = 0
    previous_month_dn: int = 0
    current_month_units: int = 0
    previous_month_units: int = 0
    best_month: str = "Unknown"
    worst_month: str = "Unknown"
    pending_average_days: float = 0.0
    critical_pending: int = 0
    overdue_pending: int = 0
    national_rank: Optional[int] = None
    unit_rank: Optional[int] = None
    dn_rank: Optional[int] = None
    pod_rank: Optional[int] = None
    pending_rank: Optional[int] = None
    regional_rank: Optional[int] = None
    fastest_delivery_days: float = 0.0
    slowest_delivery_days: float = 0.0
    latest_pgi_date: str = "N/A"
    latest_pod_date: str = "N/A"
    same_day_deliveries: int = 0
    next_day_deliveries: int = 0
    top_division: str = "Unknown"
    recommendations: list[str] = field(default_factory=list)
    business_score: float = 0.0
    overall_status: str = "Needs Attention"
    executive_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_whatsapp_message(self) -> str:
        distance = "Unknown" if self.distance.distance_km is None else f"{self.distance.distance_km:,.1f} KM"
        insights = "\n".join(f"\u2022 {item}" for item in self.insights[:10]) or "\u2022 No significant exception detected."
        recommendations = "\n".join(f"\u2022 {item}" for item in self.recommendations[:5]) or "\u2022 Continue monitoring delivery performance."
        return "\n".join(
            (
                "\U0001f3e2 Dealer Dashboard",
                "\u2501" * 18,
                "\U0001f464 Dealer Information",
                f"Dealer Name: {self.dealer_name}",
                f"Dealer Code: {self.dealer_code}",
                f"Customer Code: {self.customer_code}",
                f"City: {self.city}",
                f"Delivery Location: {self.delivery_location}",
                f"Sales Office: {self.sales_office}",
                f"Sales Manager: {self.sales_manager}",
                f"Division: {self.division}",
                "",
                "\U0001f3ed Warehouse",
                f"Warehouse: {self.warehouse} ({self.warehouse_code})",
                f"Distance: {distance}",
                f"Driving Time: {self.distance.estimated_driving_time}",
                f"Estimated Delivery: {self.distance.estimated_delivery_time}",
                "",
                "\U0001f4ca Business Summary",
                f"Revenue: PKR {self.total_revenue:,.2f}",
                f"Average Revenue/DN: PKR {self.average_revenue_per_dn:,.2f}",
                f"Units: {self.total_units:,}",
                f"Average Units/DN: {self.average_units_per_dn:,.2f}",
                f"Total DNs: {self.total_dn:,}",
                f"Completed DNs: {self.completed_dn:,}",
                f"Pending DNs: {self.pending_dn:,}",
                "",
                "\U0001f69a Delivery Performance",
                f"Delivery Success: {self.delivery_success_pct:.2f}%",
                f"PGI Success: {self.pgi_success_pct:.2f}%",
                f"POD Success: {self.pod_success_pct:.2f}%",
                f"Pending: {self.pending_pct:.2f}%",
                f"Average Delivery: {self.average_delivery_days:.2f} Days",
                f"Average POD: {self.average_pod_days:.2f} Days",
                f"Average Total Cycle: {self.average_total_cycle_time:.2f} Days",
                f"Fastest / Slowest: {self.fastest_delivery_days:.0f} / {self.slowest_delivery_days:.0f} Days",
                "",
                "\U0001f4c5 Date Summary",
                f"First DN: {self.first_delivery_date}",
                f"Latest DN: {self.latest_delivery_date}",
                f"Latest PGI: {self.latest_pgi_date}",
                f"Latest POD: {self.latest_pod_date}",
                "",
                "\U0001f4e6 Product Performance",
                f"Top Product: {self.top_product}",
                f"Top Model: {self.top_model}",
                f"Top Material: {self.top_material}",
                f"Strongest Category: {self.strongest_product_category}",
                f"Weakest Category: {self.weakest_product_category}",
                "",
                "\U0001f3c6 Dealer Ranking",
                f"Revenue Rank: {self.revenue_rank or 'N/A'}",
                f"Delivery Rank: {self.delivery_rank or 'N/A'}",
                f"Regional Rank: {self.regional_rank or 'N/A'}",
                f"National Rank: {self.national_rank or 'N/A'}",
                "",
                "\u26a0 Pending Dashboard",
                f"Pending Revenue: PKR {self.pending_revenue:,.2f}",
                f"Pending Units: {self.pending_units:,}",
                f"Pending DNs: {self.pending_dn:,}",
                f"Average Pending: {self.pending_average_days:.1f} Days",
                f"Oldest Pending: {self.oldest_pending_dn} ({self.oldest_pending_days} Days)",
                "",
                "\U0001f7e2 Business Health",
                f"Overall Score: {self.business_score:.1f}/100",
                f"Business Status: {self.overall_status}",
                "",
                "\U0001f4a1 Key Insights",
                insights,
                "",
                "\U0001f4cc Recommendations",
                recommendations,
                "",
                "\U0001f4dd Executive Summary",
                self.executive_summary or "Performance is stable; continue monitoring pending deliveries and POD closure.",
            )
        )

    def __str__(self) -> str:
        return self.to_whatsapp_message()


@dataclass
class DealerComparison:
    dealers: list[DealerDashboard]
    revenue_leader: str
    units_leader: str
    dn_leader: str
    delivery_leader: str
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerRanking:
    sort_by: str
    order: str
    dealers: list[DealerDashboard]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerSearchResult:
    original_message: str
    extracted_dealer: str
    normalized_dealer: str
    dealer_found: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    alias_used: Optional[str] = None
    rapidfuzz_score: Optional[float] = None
    semantic_score: Optional[float] = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    cache_used: bool = False
    exception: Optional[str] = None


# ============================================================
# CITY COORDINATE SERVICE
# ============================================================

class CityCoordinateService:
    """Cached coordinates with ultra-fast lookup"""
    
    COORDINATES: Dict[str, tuple[float, float]] = {
        "abbottabad": (34.1688, 73.2215), "attock": (33.7667, 72.3667),
        "bahawalpur": (29.3956, 71.6836), "bannu": (32.9861, 70.6042),
        "dera ghazi khan": (30.0489, 70.6455), "dera ismail khan": (31.8315, 70.9017),
        "faisalabad": (31.4504, 73.1350), "gilgit": (35.9208, 74.3144),
        "gujranwala": (32.1877, 74.1945), "gujrat": (32.5731, 74.1005),
        "haripur": (33.9946, 72.9106), "hyderabad": (25.3960, 68.3578),
        "islamabad": (33.6844, 73.0479), "jacobabad": (28.2819, 68.4382),
        "jhelum": (32.9405, 73.7276), "karachi": (24.8607, 67.0011),
        "kasur": (31.1187, 74.4508), "kohat": (33.5834, 71.4332),
        "lahore": (31.5204, 74.3587), "larkana": (27.5570, 68.2028),
        "mardan": (34.1989, 72.0231), "mansehra": (34.3302, 73.1968),
        "mirpur": (33.1484, 73.7517), "multan": (30.1575, 71.5249),
        "muzaffarabad": (34.3700, 73.4711), "nawabshah": (26.2442, 68.4100),
        "okara": (30.8138, 73.4534), "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750), "rahim yar khan": (28.4212, 70.2989),
        "rawalpindi": (33.5651, 73.0169), "sahiwal": (30.6682, 73.1114),
        "sargodha": (32.0836, 72.6711), "sheikhupura": (31.7167, 73.9850),
        "sialkot": (32.4945, 74.5229), "skardu": (35.2971, 75.6333),
        "sukkur": (27.7244, 68.8228), "swat": (35.2227, 72.4258),
        "wah cantt": (33.7715, 72.7511), "taxila": (33.7463, 72.8397),
    }
    
    _normalize_cache: Dict[str, str] = {}
    _city_cache: Dict[str, Optional[tuple[float, float]]] = {}

    def __init__(self) -> None:
        self._names = tuple(self.COORDINATES.keys())

    @staticmethod
    def normalize(city: Any) -> str:
        if not city:
            return ""
        
        value = str(city).lower()
        
        cached = CityCoordinateService._normalize_cache.get(value)
        if cached is not None:
            return cached
        
        value = value.replace("city", "").strip(" ,.-")
        
        aliases = {
            "rwp": "rawalpindi", "isb": "islamabad", "lhr": "lahore", 
            "khi": "karachi", "fsd": "faisalabad", "hyd": "hyderabad", 
            "ryk": "rahim yar khan", "dik": "dera ismail khan"
        }
        result = aliases.get(value, value)
        CityCoordinateService._normalize_cache[value] = result
        return result

    def get(self, city: Any) -> Optional[tuple[float, float]]:
        if not city:
            return None
            
        normalized = self.normalize(city)
        if normalized in self._city_cache:
            return self._city_cache[normalized]
        
        if normalized in self.COORDINATES:
            self._city_cache[normalized] = self.COORDINATES[normalized]
            return self.COORDINATES[normalized]
        
        match = process.extractOne(normalized, self._names, scorer=fuzz.WRatio, score_cutoff=82)
        if match:
            result = self.COORDINATES[match[0]]
            self._city_cache[normalized] = result
            return result
        
        self._city_cache[normalized] = None
        return None


# ============================================================
# DISTANCE SERVICE
# ============================================================

class DistanceService:
    """Ultra-fast distance calculation with caching"""
    
    def __init__(self, coordinates: CityCoordinateService) -> None:
        self.coordinates = coordinates
        # Route results are effectively static for a warehouse/city pair.
        self.cache: LRUCache[str, DistanceAnalytics] = LRUCache(maxsize=16384)
        self._lock = threading.RLock()
        self._ors = None
        if ORS_API_KEY:
            try:
                import openrouteservice
                self._ors = openrouteservice.Client(key=ORS_API_KEY, timeout=1)
            except Exception:
                pass

    @staticmethod
    def delivery_estimate(km: Optional[float]) -> str:
        if km is None:
            return "Unknown"
        if km <= 80:
            return "Same Day"
        if km <= 200:
            return "Next Day"
        if km <= 400:
            return "1-2 Days"
        if km <= 700:
            return "2-3 Days"
        return "3-5 Days"

    @staticmethod
    def driving_time(minutes: Optional[int]) -> str:
        if minutes is None:
            return "Unknown"
        hours, mins = divmod(max(0, minutes), 60)
        return f"{hours} hr {mins} min" if hours and mins else (f"{hours} hr" if hours else f"{mins} min")

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(value))

    def calculate(self, warehouse: Any, dealer_city: Any) -> DistanceAnalytics:
        warehouse_name, city_name = _text(warehouse), _text(dealer_city)
        key = f"{self.coordinates.normalize(warehouse_name)}|{self.coordinates.normalize(city_name)}"
        
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            return cached
        
        origin, destination = self.coordinates.get(warehouse_name), self.coordinates.get(city_name)
        if not origin or not destination:
            result = DistanceAnalytics(warehouse_name, city_name)
        else:
            km: Optional[float] = None
            minutes: Optional[int] = None
            source = "great-circle"
            
            if self._ors:
                try:
                    route = self._ors.directions(
                        [(origin[1], origin[0]), (destination[1], destination[0])], 
                        profile="driving-car"
                    )
                    summary = route["routes"][0]["summary"]
                    km = float(summary["distance"]) / 1000
                    minutes = int(round(float(summary["duration"]) / 60))
                    source = "openrouteservice"
                except Exception:
                    pass
            
            if km is None:
                try:
                    from geopy.distance import great_circle
                    km = great_circle(origin, destination).kilometers
                except:
                    km = self._haversine(origin, destination)
                km *= 1.20
                minutes = int(round(km / 55 * 60))
            
            result = DistanceAnalytics(
                warehouse_name, city_name, 
                round(km, 1), minutes, 
                self.driving_time(minutes), 
                self.delivery_estimate(km), 
                source
            )
        
        with self._lock:
            self.cache[key] = result
        return result


# ============================================================
# MAIN DEALER ANALYTICS SERVICE
# ============================================================

class DealerAnalyticsService:
    """Enterprise Dealer Intelligence Engine with AI - Ultra-fast (< 1 second)"""
    
    SORT_ALIASES = {
        "revenue": "total_revenue", "units": "total_units", "dn": "total_dn",
        "average_delivery": "average_delivery_days", "highest_pod": "pod_success_pct",
        "lowest_pending": "pending_pct", "best_revenue_growth": "revenue_growth_pct",
    }
    
    STOP_PHRASES = frozenset({
        "tell me about", "dealer dashboard", "dealer profile", "dealer performance",
        "dealer statistics", "dealer revenue", "dealer distance", "dealer pending",
        "dealer status", "dealer pod", "dealer pgi", "show", "display", "dealer",
        "profile", "statistics", "performance", "status", "revenue", "distance",
        "pending", "dashboard", "about", "of", "the", "company", "private",
        "limited", "pvt", "ltd",
    })
    
    DEALER_ALIASES = {
        "mian": "Mian Group Chakwal", "mgc": "Mian Group Chakwal",
        "taj": "Taj Electronics", "taj haripur": "Taj Electronics Haripur",
    }
    
    _normalize_regex = re.compile(r'[^a-z0-9\s]')

    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "6.1.0-performance"
        self._startup_time = datetime.utcnow().isoformat()
        self._initialization_errors: list[str] = []
        
        # Initialize services
        try:
            self._coordinates = CityCoordinateService()
        except Exception as error:
            self._initialization_errors.append(str(error))
            self._coordinates = CityCoordinateService.__new__(CityCoordinateService)
            self._coordinates._names = tuple()
            self._coordinates.COORDINATES = {}
        
        try:
            self._distance = DistanceService(self._coordinates)
        except Exception as error:
            self._initialization_errors.append(str(error))
            self._distance = None
        
        # Reuse process-wide AI resources; never reload models per service.
        self._ai_manager = get_ai_provider_manager() if get_ai_provider_manager else None
        self._ai_agent = None
        try:
            self._ai_agent = (
                self._ai_manager.get_agent(AIDealerAgent, key="dealer_agent")
                if self._ai_manager else None
            )
        except Exception as error:
            self._initialization_errors.append(str(error))
            logger.warning("Shared AI agent unavailable: %s", error)
        
        # The wrapper is cheap and reuses the shared embedding encoder.
        self._semantic_search = SemanticSearchEngine()
        
        # Caches for speed
        self._dealer_cache: TTLCache[str, DealerSearchResult] = TTLCache(maxsize=8192, ttl=CACHE_TTL)
        self._candidate_cache: TTLCache[str, list[dict[str, str]]] = TTLCache(maxsize=1, ttl=3600)
        self._extended_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=8192, ttl=3600)
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=8192, ttl=600)
        self._ranking_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=256, ttl=600)
        self._national_ranking_cache: TTLCache[str, dict[str, dict[str, Any]]] = TTLCache(maxsize=1, ttl=600)
        self._search_lock = threading.RLock()
        self._aggregate_cache: TTLCache[str, list[Any]] = TTLCache(maxsize=2048, ttl=300)
        
        # Pre-compute stop phrases
        self._normalized_stop_phrases = {self._normalize_dealer_text(p) for p in self.STOP_PHRASES}
        self._last_diagnostic: dict[str, Any] = {}

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    @staticmethod
    def _dealer_filter(identifier: str) -> Any:
        token = identifier.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.customer_name)) == token.lower(),
            DeliveryReport.dealer_code == token,
            DeliveryReport.customer_code == token,
        )

    @classmethod
    def _normalize_dealer_text(cls, value: Any) -> str:
        if not value:
            return ""
        
        text_value = unicodedata.normalize("NFKD", _text(value, "").lower())
        text_value = cls._normalize_regex.sub(" ", text_value)
        text_value = _WHITESPACE_PATTERN.sub(" ", text_value).strip()
        
        for phrase in cls.STOP_PHRASES:
            if phrase in text_value:
                text_value = text_value.replace(phrase, " ")
        
        return _WHITESPACE_PATTERN.sub(" ", text_value).strip()

    def _dealer_candidates(self, session: Session) -> tuple[list[dict[str, str]], bool]:
        with self._search_lock:
            cached = self._candidate_cache.get("all")
        if cached is not None:
            return cached, True
        
        started = time.perf_counter()
        
        # Optimized query with SQLGlot if available
        query_text = """
            SELECT DISTINCT 
                customer_name, 
                dealer_code, 
                customer_code 
            FROM delivery_reports 
            WHERE customer_name IS NOT NULL 
              AND customer_name != ''
            ORDER BY customer_name
        """
        
        if sqlglot:
            query_text = SQLOptimizer.optimize_query(query_text)
        
        rows = session.execute(text(query_text)).fetchall()
        
        # Use PyArrow for fast processing if available
        if pa:
            try:
                data = pa.Table.from_pylist([
                    {"name": _text(r.customer_name), 
                     "dealer_code": _text(r.dealer_code, ""),
                     "customer_code": _text(r.customer_code, ""),
                     "normalized": self._normalize_dealer_text(r.customer_name)}
                    for r in rows if _text(r.customer_name, "")
                ])
                candidates = data.to_pylist()
            except:
                candidates = [
                    {"name": _text(r.customer_name),
                     "dealer_code": _text(r.dealer_code, ""),
                     "customer_code": _text(r.customer_code, ""),
                     "normalized": self._normalize_dealer_text(r.customer_name)}
                    for r in rows if _text(r.customer_name, "")
                ]
        else:
            candidates = [
                {"name": _text(r.customer_name),
                 "dealer_code": _text(r.dealer_code, ""),
                 "customer_code": _text(r.customer_code, ""),
                 "normalized": self._normalize_dealer_text(r.customer_name)}
                for r in rows if _text(r.customer_name, "")
            ]
        
        with self._search_lock:
            self._candidate_cache["all"] = candidates
        
        logger.info(f"Candidates loaded: {len(candidates)} in {(time.perf_counter() - started)*1000:.2f}ms")
        return candidates, False

    def _resolve_dealer(self, session: Session, message: str) -> DealerSearchResult:
        started = time.perf_counter()
        original = _text(message, "")
        normalized = self._normalize_dealer_text(original)
        alias = self.DEALER_ALIASES.get(normalized)
        search_text = alias or normalized
        cache_key = search_text.lower()
        
        with self._search_lock:
            cached = self._dealer_cache.get(cache_key)
        if cached:
            result = DealerSearchResult(**asdict(cached))
            result.original_message, result.cache_used = original, True
            return result
        
        result = DealerSearchResult(original, search_text, normalized, alias_used=alias)
        
        try:
            candidates, cache_used = self._dealer_candidates(session)
            result.cache_used = cache_used
            
            # Fast exact code match
            token = original.strip()
            for item in candidates:
                if token == item["dealer_code"] or token == item["customer_code"]:
                    result.dealer_found = item["name"]
                    result.dealer_code = item["dealer_code"]
                    result.customer_code = item["customer_code"]
                    self._cache_result(cache_key, result)
                    return result
            
            # Fast exact match
            norm_search = self._normalize_dealer_text(search_text)
            exact_matches = [item for item in candidates if item["normalized"] == norm_search]
            if exact_matches:
                best = exact_matches[0]
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
                result.rapidfuzz_score = 100.0
                self._cache_result(cache_key, result)
                return result
            
            # Contains match
            if search_text:
                contains_matches = [
                    item for item in candidates 
                    if search_text in item["normalized"] or item["normalized"] in search_text
                ]
                if len(contains_matches) == 1:
                    best = contains_matches[0]
                    result.dealer_found = best["name"]
                    result.dealer_code = best["dealer_code"]
                    result.customer_code = best["customer_code"]
                    result.rapidfuzz_score = 95.0
                    self._cache_result(cache_key, result)
                    return result
            
            # Semantic search with Sentence Transformers
            if self._semantic_search.encoder:
                semantic_matches = []
                for item in candidates:
                    score = self._semantic_search.semantic_similarity(
                        search_text, 
                        item["normalized"]
                    )
                    if score > 0.7:
                        semantic_matches.append((item, score))
                
                if semantic_matches:
                    semantic_matches.sort(key=lambda x: x[1], reverse=True)
                    best, score = semantic_matches[0]
                    result.dealer_found = best["name"]
                    result.dealer_code = best["dealer_code"]
                    result.customer_code = best["customer_code"]
                    result.semantic_score = round(score, 3)
                    self._cache_result(cache_key, result)
                    return result
            
            # Fallback: rapidfuzz
            choices = {index: item["normalized"] for index, item in enumerate(candidates)}
            matches = process.extract(search_text, choices, scorer=fuzz.WRatio, limit=5)
            scored = [(candidates[index], float(score)) for _, score, index in matches]
            
            result.suggestions = [
                {"dealer_name": item["name"], "similarity": round(score, 2), "dealer_code": item["dealer_code"]}
                for item, score in scored
            ]
            
            if scored:
                result.rapidfuzz_score = round(scored[0][1], 2)
            
            confident = [entry for entry in scored if entry[1] >= 85]
            if len(confident) == 1 or (len(confident) > 1 and confident[0][1] - confident[1][1] >= 5):
                best = confident[0][0]
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
            elif confident:
                result.ambiguous = True
            
            self._cache_result(cache_key, result)
            
        except Exception as error:
            result.exception = str(error)
            logger.exception(f"Dealer resolution failed for {original}")
        
        elapsed_ms = (time.perf_counter() - started) * 1000
        self._last_diagnostic = {**asdict(result), "execution_time_ms": round(elapsed_ms, 2)}
        
        if elapsed_ms > 100:
            logger.warning(f"Slow dealer resolution: {elapsed_ms:.2f}ms for {original}")
        
        return result

    def _cache_result(self, key: str, result: DealerSearchResult) -> None:
        with self._search_lock:
            self._dealer_cache[key] = result

    @staticmethod
    def _suggestion_response(search: DealerSearchResult) -> dict[str, Any]:
        suggestions = search.suggestions[:5]
        if search.ambiguous:
            lines = ["Multiple Dealers Found", ""]
            for index, item in enumerate(suggestions, 1):
                lines.extend((str(index), item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            lines.append("Reply with dealer number.")
            code = "MULTIPLE_DEALERS_FOUND"
        else:
            lines = ["Did you mean", ""]
            for item in suggestions:
                lines.extend((item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            code = "DEALER_SUGGESTIONS"
        
        message = "\n".join(lines).strip()
        return {
            "success": False, 
            "error_code": code, 
            "message": message, 
            "response": message, 
            "formatted_response": message, 
            "whatsapp_message": message, 
            "suggestions": suggestions, 
            "search": search
        }

    def _aggregate_query(self, session: Session, dealer: Optional[str] = None) -> list[Any]:
        cache_key = dealer or "all"
        cached = self._aggregate_cache.get(cache_key)
        if cached is not None:
            return cached
        
        started = time.perf_counter()
        
        completed = or_(DeliveryReport.pending_flag.is_(False), _status_complete(DeliveryReport.delivery_status), DeliveryReport.pod_date.isnot(None))
        pending = or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
        pgi_pending = DeliveryReport.good_issue_date.is_(None)
        pod_pending = and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None))
        
        query = session.query(
            func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
            func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
            func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
            func.max(DeliveryReport.ship_to_city).label("city"), 
            func.max(DeliveryReport.delivery_location).label("delivery_location"), 
            func.max(DeliveryReport.warehouse).label("warehouse"),
            func.max(DeliveryReport.warehouse_code).label("warehouse_code"), 
            func.max(DeliveryReport.sales_office).label("sales_office"),
            func.max(DeliveryReport.sales_manager).label("sales_manager"), 
            func.max(DeliveryReport.division).label("division"),
            func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
            func.count(distinct(case((completed, DeliveryReport.dn_no)))).label("completed_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("pending_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("delivered_revenue"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("pending_revenue"),
            func.count(distinct(case((pgi_pending, DeliveryReport.dn_no)))).label("pgi_pending_dn"),
            func.count(distinct(case((pod_pending, DeliveryReport.dn_no)))).label("pod_pending_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("delivery_pending_dn"),
            func.min(case((pending, DeliveryReport.dn_create_date))).label("oldest_pending_date"),
            func.avg(case((pending, func.current_date() - DeliveryReport.dn_create_date))).label("pending_average_days"),
            func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
            func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
            func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
            func.max(DeliveryReport.pod_date).label("latest_pod_date"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
            func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
            func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            func.min(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("fastest_delivery"),
            func.max(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("slowest_delivery"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 0, DeliveryReport.dn_no)))).label("same_day_deliveries"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 1, DeliveryReport.dn_no)))).label("next_day_deliveries"),
            func.count(distinct(case((_status_complete(DeliveryReport.delivery_status), DeliveryReport.dn_no)))).label("delivery_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pgi_status), DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no)))).label("pgi_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pod_status), DeliveryReport.pod_date.isnot(None)), DeliveryReport.dn_no)))).label("pod_success"),
        ).filter(DeliveryReport.customer_name.isnot(None))
        
        if dealer:
            query = query.filter(self._dealer_filter(dealer))
        
        result = query.group_by(
            DeliveryReport.customer_name, 
            DeliveryReport.dealer_code, 
            DeliveryReport.customer_code
        ).all()
        
        self._aggregate_cache[cache_key] = result
        
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.debug(f"Aggregate query: {elapsed_ms:.2f}ms")
        return result

    @staticmethod
    def _days(value: Any) -> float:
        if value is None:
            return 0.0
        if hasattr(value, "days"):
            return round(float(value.days), 2)
        return round(_number(value), 2)

    def _row_to_dashboard(self, row: Any, include_distance: bool = True) -> DealerDashboard:
        total = int(row.total_dn or 0)
        
        dashboard = DealerDashboard(
            dealer_name=_text(row.dealer_name), 
            dealer_code=_text(row.dealer_code), 
            customer_code=_text(row.customer_code),
            city=_text(row.city), 
            delivery_location=_text(getattr(row, "delivery_location", None)), 
            warehouse=_text(row.warehouse), 
            warehouse_code=_text(row.warehouse_code),
            sales_office=_text(row.sales_office), 
            sales_manager=_text(row.sales_manager), 
            division=_text(row.division),
            total_dn=total, 
            completed_dn=int(row.completed_dn or 0), 
            pending_dn=int(row.pending_dn or 0),
            total_units=int(row.total_units or 0), 
            total_revenue=round(_number(row.total_revenue), 2),
            average_revenue_per_dn=round(_number(row.total_revenue) / total, 2) if total else 0,
            average_units_per_dn=round(_number(row.total_units) / total, 2) if total else 0,
            first_delivery_date=_date_text(row.first_delivery_date), 
            latest_delivery_date=_date_text(row.latest_delivery_date),
            average_delivery_days=self._days(row.avg_delivery), 
            average_pod_days=self._days(row.avg_pod),
            average_total_cycle_time=self._days(row.avg_cycle), 
            delivery_success_pct=_percent(row.delivery_success, total),
            pgi_success_pct=_percent(row.pgi_success, total), 
            pod_success_pct=_percent(row.pod_success, total),
            pending_pct=_percent(row.pending_dn, total),
            distance=(self._safe_distance(row.warehouse, row.city) if include_distance else DistanceAnalytics(_text(row.warehouse), _text(row.city))),
            delivered_units=int(getattr(row, "delivered_units", 0) or 0),
            pending_units=int(getattr(row, "pending_units", 0) or 0),
            delivered_revenue=round(_number(getattr(row, "delivered_revenue", 0)), 2),
            pending_revenue=round(_number(getattr(row, "pending_revenue", 0)), 2),
            pgi_pending_dn=int(getattr(row, "pgi_pending_dn", 0) or 0),
            pod_pending_dn=int(getattr(row, "pod_pending_dn", 0) or 0),
            delivery_pending_dn=int(getattr(row, "delivery_pending_dn", 0) or 0),
            oldest_pending_days=max(0, (date.today() - row.oldest_pending_date).days) if getattr(row, "oldest_pending_date", None) else 0,
            pending_average_days=self._days(getattr(row, "pending_average_days", 0)),
            average_revenue_per_unit=round(_number(row.total_revenue) / _number(row.total_units), 2) if _number(row.total_units) else 0.0,
            delivery_coverage=_percent(row.completed_dn, total),
            latest_pgi_date=_date_text(getattr(row, "latest_pgi_date", None)),
            latest_pod_date=_date_text(getattr(row, "latest_pod_date", None)),
            fastest_delivery_days=self._days(getattr(row, "fastest_delivery", 0)),
            slowest_delivery_days=self._days(getattr(row, "slowest_delivery", 0)),
            same_day_deliveries=int(getattr(row, "same_day_deliveries", 0) or 0),
            next_day_deliveries=int(getattr(row, "next_day_deliveries", 0) or 0),
        )
        
        dashboard.insights = self._basic_insights(dashboard)
        return dashboard

    def _safe_distance(self, warehouse: Any, city: Any) -> DistanceAnalytics:
        try:
            if self._distance is not None:
                return self._distance.calculate(warehouse, city)
        except Exception:
            pass
        return DistanceAnalytics(_text(warehouse), _text(city))

    def _apply_extended_analytics(self, session: Session, item: DealerDashboard) -> None:
        identity = item.dealer_code if item.dealer_code != "Unknown" else item.customer_code
        identity = identity if identity != "Unknown" else item.dealer_name
        cache_key = str(identity).lower()
        
        cached = self._extended_cache.get(cache_key)
        if cached:
            for key, value in cached.items():
                setattr(item, key, value)
            self._apply_business_health(item)
            item.insights, item.recommendations = self._business_insights(item)
            return
        
        condition = self._dealer_filter(str(identity))
        values: dict[str, Any] = {}
        
        # SQLAlchemy Session is intentionally used only by this request thread.
        # Dealer code/customer code indexes keep these scoped queries fast.
        for loader in (
            self._get_dn_analytics,
            self._get_monthly_analytics,
            self._get_product_analytics,
            self._get_division_analytics,
        ):
            try:
                result = loader(session, condition)
                if result:
                    values.update(result)
            except Exception:
                logger.exception("Extended analytics loader failed: %s", loader.__name__)
        
        self._apply_dealer_rankings(session, item, values)
        
        for key, value in values.items():
            setattr(item, key, value)
        
        self._apply_business_health(item)
        self._extended_cache[cache_key] = values
        item.insights, item.recommendations = self._business_insights(item)

    def _get_dn_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        try:
            dn_rows = session.query(
                DeliveryReport.dn_no.label("dn"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.min(DeliveryReport.dn_create_date).label("created"),
                func.max(DeliveryReport.good_issue_date).label("issued"),
                func.max(DeliveryReport.pod_date).label("pod"),
                func.max(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label("pending"),
            ).filter(condition).group_by(DeliveryReport.dn_no).all()
            
            if not dn_rows:
                return {}
            
            by_revenue = sorted(dn_rows, key=lambda row: _number(row.revenue))
            by_units = sorted(dn_rows, key=lambda row: _number(row.units))
            by_date = sorted(dn_rows, key=lambda row: row.created or date.min)
            pending_rows = [row for row in dn_rows if int(row.pending or 0)]
            
            delivery_days = []
            for row in dn_rows:
                if row.created and row.issued and row.issued >= row.created:
                    delivery_days.append((row.issued - row.created).days)
            
            values = {
                "highest_revenue_dn": _text(by_revenue[-1].dn, "N/A"),
                "lowest_revenue_dn": _text(by_revenue[0].dn, "N/A"),
                "highest_unit_dn": _text(by_units[-1].dn, "N/A"),
                "lowest_unit_dn": _text(by_units[0].dn, "N/A"),
                "newest_dn": _text(by_date[-1].dn, "N/A"),
                "fastest_delivery_days": float(min(delivery_days)) if delivery_days else 0.0,
                "slowest_delivery_days": float(max(delivery_days)) if delivery_days else 0.0,
            }
            
            if pending_rows:
                oldest = min(pending_rows, key=lambda row: row.created or date.max)
                ages = [max(0, (date.today() - row.created).days) for row in pending_rows if row.created]
                values.update({
                    "oldest_pending_dn": _text(oldest.dn, "N/A"),
                    "oldest_pending_days": max(ages) if ages else 0,
                    "pending_average_days": round(sum(ages) / len(ages), 2) if ages else 0.0,
                    "critical_pending": sum(1 for age in ages if age > 7),
                    "overdue_pending": sum(1 for age in ages if age > 14),
                })
            
            return values
        except Exception:
            return {}

    def _get_monthly_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        try:
            monthly = session.query(
                func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
            
            if not monthly:
                return {}
            
            month_map = {row.month: row for row in monthly}
            current = date.today().strftime("%Y-%m")
            previous_date = date.today().replace(day=1)
            previous_date = (previous_date.replace(year=previous_date.year - 1, month=12) 
                           if previous_date.month == 1 
                           else previous_date.replace(month=previous_date.month - 1))
            previous = previous_date.strftime("%Y-%m")
            
            current_row, previous_row = month_map.get(current), month_map.get(previous)
            current_revenue = _number(current_row.revenue) if current_row else 0.0
            previous_revenue = _number(previous_row.revenue) if previous_row else 0.0
            growth = ((current_revenue - previous_revenue) * 100 / previous_revenue) if previous_revenue else (100.0 if current_revenue else 0.0)
            
            best = max(monthly, key=lambda row: _number(row.revenue))
            worst = min(monthly, key=lambda row: _number(row.revenue))
            
            return {
                "current_month_revenue": round(current_revenue, 2), 
                "previous_month_revenue": round(previous_revenue, 2),
                "monthly_growth": round(growth, 2), 
                "current_month_units": int(current_row.units or 0) if current_row else 0,
                "previous_month_units": int(previous_row.units or 0) if previous_row else 0,
                "current_month_dn": int(current_row.dns or 0) if current_row else 0,
                "previous_month_dn": int(previous_row.dns or 0) if previous_row else 0,
                "best_month": _text(best.month), 
                "worst_month": _text(worst.month), 
                "busiest_month": _text(best.month),
                "revenue_growth_pct": round(growth, 2),
            }
        except Exception:
            return {}

    def _get_product_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        try:
            top_product = self._get_top_value(session, condition, DeliveryReport.customer_model)
            top_material = self._get_top_value(session, condition, DeliveryReport.material_no)
            return {
                "top_product": top_product,
                "top_model": top_product,
                "top_material": top_material,
            }
        except Exception:
            return {}

    def _get_division_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        try:
            division_rows = session.query(
                DeliveryReport.division.label("value"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
            ).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).all()
            
            if not division_rows:
                return {
                    "top_division": "Unknown",
                    "strongest_product_category": "Unknown",
                    "weakest_product_category": "Unknown",
                }
            
            return {
                "top_division": _text(division_rows[0].value),
                "strongest_product_category": _text(division_rows[0].value),
                "weakest_product_category": _text(division_rows[-1].value) if len(division_rows) > 1 else _text(division_rows[0].value),
            }
        except Exception:
            return {}

    def _get_top_value(self, session: Session, condition: Any, column: Any) -> str:
        try:
            row = session.query(
                column.label("value"), 
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(condition, column.isnot(None)).group_by(column).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).first()
            return _text(row.value) if row else "Unknown"
        except Exception:
            return "Unknown"

    def _apply_dealer_rankings(self, session: Session, item: DealerDashboard, values: dict) -> None:
        cache_key = f"rankings_{item.dealer_code}"
        cached_rankings = self._ranking_cache.get(cache_key)
        if cached_rankings:
            values.update(cached_rankings)
            return

        try:
            ranking_map = self._national_ranking_cache.get("all")
            if ranking_map is None:
                ranking_map = self._build_national_ranking_map(session)
                self._national_ranking_cache["all"] = ranking_map
            rankings = (
                ranking_map.get(f"code:{item.dealer_code}")
                or ranking_map.get(f"name:{item.dealer_name.lower()}")
            )
            if not rankings:
                return
            values.update(rankings)
            self._ranking_cache[cache_key] = rankings
        except Exception:
            logger.exception("Dealer ranking lookup failed")

    def _build_national_ranking_map(self, session: Session) -> dict[str, dict[str, Any]]:
        """Scan national dealer aggregates once and cache every dealer rank."""
        rows = session.query(
            DeliveryReport.customer_name.label("name"), DeliveryReport.dealer_code.label("code"),
            func.max(DeliveryReport.ship_to_city).label("city"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("delivery"),
            func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod"),
            func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending"),
        ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
            DeliveryReport.customer_name, DeliveryReport.dealer_code
        ).all()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            ranks: dict[str, Any] = {}
            result[f"name:{_text(row.name).lower()}"] = ranks
            if _text(row.code, ""):
                result[f"code:{_text(row.code, '')}"] = ranks

        def assign(field: str, key: Any, reverse: bool) -> None:
            for index, row in enumerate(sorted(rows, key=key, reverse=reverse), 1):
                result[f"name:{_text(row.name).lower()}"][field] = index

        assign("revenue_rank", lambda row: _number(row.revenue), True)
        assign("unit_rank", lambda row: _number(row.units), True)
        assign("dn_rank", lambda row: int(row.dns or 0), True)
        assign("delivery_rank", lambda row: self._days(row.delivery) if row.delivery is not None else float("inf"), False)
        assign("pod_rank", lambda row: _percent(row.pod, row.dns), True)
        assign("pending_rank", lambda row: _percent(row.pending, row.dns), False)
        assign("national_rank", lambda row: (_number(row.revenue), _percent(row.pod, row.dns)), True)

        cities = {_text(row.city, "").lower() for row in rows}
        for city in cities:
            regional = [row for row in rows if _text(row.city, "").lower() == city]
            for index, row in enumerate(sorted(regional, key=lambda value: _number(value.revenue), reverse=True), 1):
                result[f"name:{_text(row.name).lower()}"]["regional_rank"] = index
        return result

    @staticmethod
    def _apply_business_health(item: DealerDashboard) -> None:
        score = (
            item.delivery_success_pct * 0.35
            + item.pgi_success_pct * 0.20
            + item.pod_success_pct * 0.25
            + max(0.0, 100.0 - item.pending_pct) * 0.20
        )
        item.business_score = round(max(0.0, min(100.0, score)), 1)
        item.overall_status = "Excellent" if score >= 90 else ("Good" if score >= 75 else ("Watch" if score >= 60 else "Needs Attention"))
        trend = "growing" if item.monthly_growth >= 0 else "declining"
        action = "maintain current controls" if item.overall_status in {"Excellent", "Good"} else "prioritize pending DN and POD closure"
        item.executive_summary = (
            f"{item.dealer_name} is {trend} with a {item.business_score:.1f}/100 business score. "
            f"Delivery success is {item.delivery_success_pct:.1f}% and {item.pending_dn} DNs remain pending; {action}."
        )

    @staticmethod
    def _business_insights(item: DealerDashboard) -> tuple[list[str], list[str]]:
        trend = "increasing" if item.monthly_growth >= 0 else "decreasing"
        insights = [
            f"Dealer revenue is {trend} ({item.monthly_growth:+.1f}% month over month).",
            f"Dealer has {item.pending_dn:,} pending DNs and {item.pending_units:,} pending units.",
            f"Pending revenue is PKR {item.pending_revenue:,.2f}.",
            f"Delivery success is {item.delivery_success_pct:.1f}% with average delivery of {item.average_delivery_days:.1f} days.",
            f"POD completion is {item.pod_success_pct:.1f}% and PGI completion is {item.pgi_success_pct:.1f}%.",
            f"Warehouse {item.warehouse} serves this dealer and contributes {item.warehouse_utilization:.1f}% of its unit throughput.",
            f"{item.top_model} is the leading model; top material is {item.top_material}.",
            f"Best revenue month is {item.best_month}; national rank is {item.national_rank or 'N/A'}.",
        ]
        if item.oldest_pending_days:
            insights.append(f"Oldest pending DN {item.oldest_pending_dn} is {item.oldest_pending_days} days old.")
        
        recommendations = []
        if item.overdue_pending:
            recommendations.append(f"Escalate {item.overdue_pending} DNs pending for more than 14 days.")
        if item.pod_success_pct < 90:
            recommendations.append("Prioritize POD collection and closure.")
        if item.pgi_pending_dn:
            recommendations.append(f"Review {item.pgi_pending_dn} DNs awaiting PGI.")
        if not recommendations:
            recommendations.append("Maintain the current delivery and POD control process.")
        
        return insights, recommendations

    @staticmethod
    def _basic_insights(item: DealerDashboard) -> list[str]:
        insights = []
        if item.delivery_success_pct >= 95:
            insights.append("Dealer has excellent delivery performance.")
        if item.pending_pct >= 25:
            insights.append("Dealer has high pending deliveries requiring attention.")
        if item.pod_success_pct < 80:
            insights.append("Dealer has low POD completion.")
        if item.average_delivery_days and item.average_delivery_days <= 2:
            insights.append("Dealer receives deliveries quickly.")
        if item.distance.distance_km is not None:
            insights.append(f"Dealer is {item.distance.distance_km:,.1f} KM from the primary warehouse.")
        return insights

    def _enrich_profile(self, session: Session, item: DealerDashboard) -> None:
        condition = self._dealer_filter(item.dealer_code if item.dealer_code != "Unknown" else item.dealer_name)
        
        month = session.query(
            func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("period"), 
            func.sum(DeliveryReport.dn_amount).label("revenue")
        ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("period").order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        
        products = session.query(
            DeliveryReport.division.label("category"), 
            func.sum(DeliveryReport.dn_amount).label("revenue")
        ).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).all()
        
        item.busiest_month = _text(month.period) if month else "Unknown"
        if products:
            item.strongest_product_category = _text(products[0].category)
            item.weakest_product_category = _text(products[-1].category)
            item.insights.append(f"Strongest product category is {item.strongest_product_category}.")
        if item.busiest_month != "Unknown":
            item.insights.append(f"Dealer's busiest month is {item.busiest_month}.")

    # ============================================================
    # PUBLIC API - AI-POWERED QUESTION ANSWERING
    # ============================================================

    def answer_dealer_question(self, question: str, dealer_name: str = "", **kwargs) -> Dict[str, Any]:
        """
        Answer any dealer question with AI-powered responses.
        Returns in < 1 second.
        """
        start_time = time.perf_counter()
        
        try:
            with self._session() as session:
                # Step 1: Resolve dealer (< 50ms)
                if not dealer_name:
                    dealer_name = self._extract_dealer_from_question(question)
                
                search = self._resolve_dealer(session, dealer_name or question)
                if search.exception:
                    return {"success": False, "error_code": "SEARCH_ERROR", "message": "Dealer search failed", "error": search.exception}
                if not search.dealer_found:
                    return self._suggestion_response(search)
                
                # Step 2: Get dealer data (< 200ms from cache)
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    data = cached_dashboard.get("data")
                else:
                    rows = self._aggregate_query(session, resolved_identity)
                    if not rows:
                        return self._suggestion_response(search)
                    data = self._row_to_dashboard(rows[0])
                    self._apply_extended_analytics(session, data)
                    
                    # Cache for future
                    self._dashboard_cache[dashboard_key] = {"data": data, "search": search}
                
                # Step 3: Generate AI response (< 300ms)
                if USE_AI and self._ai_agent:
                    ai_response = self._ai_agent.generate_response(
                        data.dealer_name,
                        data.to_dict(),
                        question
                    )
                    response_text = ai_response.answer
                    insights = ai_response.insights
                    recommendations = ai_response.recommendations
                else:
                    # Fallback: intent-based response
                    intent = IntentResolver.resolve_intent(question)
                    response_text = self._generate_intent_response(data, intent)
                    insights = data.insights[:3]
                    recommendations = data.recommendations[:3]
                
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                if elapsed_ms > 1000:
                    logger.warning(f"Slow response: {elapsed_ms:.2f}ms for {question}")
                
                return {
                    "success": True,
                    "dealer": data.dealer_name,
                    "question": question,
                    "answer": response_text,
                    "whatsapp_message": response_text,
                    "insights": insights,
                    "recommendations": recommendations,
                    "data": data,
                    "execution_time_ms": round(elapsed_ms, 2),
                    "ai_generated": USE_AI and self._ai_agent is not None,
                }
                
        except Exception as error:
            logger.exception(f"Question answering failed: {error}")
            return {
                "success": False,
                "error_code": "ANSWER_ERROR",
                "message": f"Unable to answer question: {str(error)}",
                "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
            }

    def _extract_dealer_from_question(self, question: str) -> str:
        """Extract dealer name from question"""
        # Simple extraction - look for common patterns
        patterns = [
            r'for\s+([\w\s]+?)(?:\?|$|\.)',
            r'about\s+([\w\s]+?)(?:\?|$|\.)',
            r'of\s+([\w\s]+?)(?:\?|$|\.)',
            r'on\s+([\w\s]+?)(?:\?|$|\.)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, question, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return ""

    def _generate_intent_response(self, data: DealerDashboard, intent: DealerIntent) -> str:
        """Generate response based on intent"""
        if intent == DealerIntent.REVENUE:
            return f"ðŸ’° Revenue: PKR {data.total_revenue:,.2f}\nGrowth: {data.monthly_growth:+.1f}%"
        elif intent == DealerIntent.PENDING:
            return f"âš ï¸ Pending: {data.pending_dn} DNs\nValue: PKR {data.pending_revenue:,.2f}"
        elif intent == DealerIntent.DELIVERY:
            return f"ðŸšš Delivery Success: {data.delivery_success_pct:.1f}%\nAvg: {data.average_delivery_days:.2f} days"
        elif intent == DealerIntent.POD:
            return f"ðŸ“„ POD Success: {data.pod_success_pct:.1f}%\nAvg: {data.average_pod_days:.2f} days"
        elif intent == DealerIntent.HEALTH:
            return f"ðŸ’³ Business Score: {data.business_score:.1f}/100\nStatus: {data.overall_status}"
        elif intent == DealerIntent.WAREHOUSE:
            dist = data.distance.distance_km or 0
            return f"ðŸ­ Warehouse: {data.warehouse}\nDistance: {dist:.1f} KM"
        elif intent == DealerIntent.DASHBOARD:
            return data.to_whatsapp_message()
        else:
            return f"ðŸ“Š Dealer: {data.dealer_name}\nRevenue: PKR {data.total_revenue:,.2f}\nDNs: {data.total_dn}\nPending: {data.pending_dn}"

    # ============================================================
    # EXISTING PUBLIC METHODS (Maintained for Backward Compatibility)
    # ============================================================

    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get complete dealer dashboard (existing method)"""
        start_time = time.perf_counter()
        
        identifier = dealer_name or kwargs.get("dealer") or kwargs.get("dealer_code") or kwargs.get("customer_code") or ""
        if not identifier:
            return {"success": False, "error_code": "DEALER_REQUIRED", "message": "Please provide a dealer name or code."}
        
        try:
            with self._session() as session:
                search = self._resolve_dealer(session, str(identifier))
                if search.exception:
                    return {"success": False, "error_code": "SEARCH_ERROR", "message": "Dealer search is temporarily unavailable.", "error": search.exception}
                if not search.dealer_found:
                    return self._suggestion_response(search)
                
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    return cached_dashboard
                
                rows = self._aggregate_query(session, resolved_identity)
                if not rows:
                    return self._suggestion_response(search)
                
                data = self._row_to_dashboard(rows[0])
                
                try:
                    self._apply_extended_analytics(session, data)
                except Exception:
                    logger.exception("Extended dealer analytics failed")
                    data.insights, data.recommendations = self._business_insights(data)
                
                try:
                    formatted = data.to_whatsapp_message()
                except Exception:
                    formatted = f"Dealer Dashboard\nDealer: {data.dealer_name}\nRevenue: {data.total_revenue:,.2f}\nUnits: {data.total_units:,}\nDN: {data.total_dn:,}"
                
                response = {
                    "success": True, 
                    "data": data, 
                    "dashboard": data, 
                    "search": search, 
                    "whatsapp_message": formatted, 
                    "formatted_response": formatted, 
                    "message": formatted, 
                    "response": formatted,
                    "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
                }
                
                self._dashboard_cache[dashboard_key] = response
                return response
                
        except Exception as error:
            logger.exception("Dealer dashboard query failed")
            return {
                "success": False, 
                "error_code": "DATABASE_UNAVAILABLE", 
                "message": "Dealer database is currently unavailable.", 
                "error": str(error),
                "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
            }

    def diagnose_dealer_search(self, message: str = "", **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                result = self._resolve_dealer(session, message or kwargs.get("dealer_name") or kwargs.get("dealer") or "")
                rows = len(self._aggregate_query(session, result.dealer_code or result.customer_code or result.dealer_found)) if result.dealer_found else 0
            
            output = asdict(result)
            output.update({
                "rows_returned": rows, 
                "distance_calculated": False, 
                "distance_source": "Unknown", 
                "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)
            })
            return {"success": result.exception is None, "diagnostic": output}
        except Exception as error:
            logger.exception("Dealer diagnostics failed")
            return {"success": False, "diagnostic": {"original_message": message, "any_exception": str(error), "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)}}

    def get_dealer_profile(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            result = self.get_dealer_dashboard(dealer_name, **kwargs)
            if not result.get("success"):
                return result
            
            with self._session() as session:
                self._enrich_profile(session, result["data"])
            
            result["profile"] = result["data"]
            result["whatsapp_message"] = result["data"].to_whatsapp_message()
            result["message"] = result["whatsapp_message"]
            result["response"] = result["whatsapp_message"]
            return result
        except Exception as error:
            logger.exception("Dealer profile failed")
            return {"success": False, "error_code": "PROFILE_ERROR", "message": "Dealer profile is temporarily unavailable.", "error": str(error)}

    def compare_dealers(self, dealer_names: Any = None, dealer_two: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        try:
            values = dealer_names or kwargs.get("dealers") or kwargs.get("dealer1") or []
            if isinstance(values, str):
                values = [values]
            values = list(values)
            second = dealer_two or kwargs.get("dealer2")
            if second:
                values.append(second)
            values = list(dict.fromkeys(str(value) for value in values if value))
            
            if len(values) < 2:
                return {"success": False, "error_code": "TWO_DEALERS_REQUIRED", "message": "Please provide at least two dealers."}
            
            dashboards = []
            for value in values[:10]:
                result = self.get_dealer_dashboard(value)
                if result.get("success"):
                    dashboards.append(result["data"])
            
            if len(dashboards) < 2:
                return {"success": False, "error_code": "DEALERS_NOT_FOUND", "message": "At least two matching dealers are required."}
            
            comparison = DealerComparison(
                dashboards, 
                max(dashboards, key=lambda x: x.total_revenue).dealer_name,
                max(dashboards, key=lambda x: x.total_units).dealer_name, 
                max(dashboards, key=lambda x: x.total_dn).dealer_name,
                min(dashboards, key=lambda x: x.average_delivery_days or float("inf")).dealer_name,
                [
                    f"{max(dashboards, key=lambda x: x.total_revenue).dealer_name} leads revenue.",
                    f"{min(dashboards, key=lambda x: x.pending_pct).dealer_name} has the lowest pending rate."
                ],
            )
            return {"success": True, "data": comparison, "comparison": comparison}
        except Exception as error:
            logger.exception("Dealer comparison failed")
            return {"success": False, "error_code": "COMPARISON_ERROR", "message": "Dealer comparison is temporarily unavailable.", "error": str(error)}

    def _rank(self, sort_by: str, limit: int, bottom: bool) -> dict[str, Any]:
        try:
            cache_key = f"{sort_by.lower()}|{int(limit)}|{int(bottom)}"
            cached = self._ranking_cache.get(cache_key)
            if cached:
                return cached
            
            with self._session() as session:
                items = [self._row_to_dashboard(row, include_distance=False) for row in self._aggregate_query(session)]
            
            key_name = self.SORT_ALIASES.get(sort_by.lower().replace(" ", "_"), "total_revenue")
            naturally_low = key_name in {"average_delivery_days", "pending_pct", "total_revenue", "total_units", "pod_success_pct"}
            reverse = (not bottom and not (key_name in {"average_delivery_days", "pending_pct"})) or (bottom and key_name in {"average_delivery_days", "pending_pct"})
            
            items.sort(
                key=lambda value: getattr(value, key_name, 0) if getattr(value, key_name, None) is not None else 0, 
                reverse=reverse
            )
            
            ranking = DealerRanking(sort_by, "bottom" if bottom else "top", items[: max(1, min(int(limit), 100))])
            response = {"success": True, "data": ranking, "dealers": ranking.dealers, "count": len(ranking.dealers)}
            self._ranking_cache[cache_key] = response
            return response
        except (SQLAlchemyError, ValueError) as error:
            logger.exception("Dealer ranking failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is currently unavailable.", "error": str(error)}

    def get_top_dealers(self, limit: int = 10, sort_by: str = "revenue", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), False)
        except Exception as error:
            logger.exception("Top dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def get_bottom_dealers(self, limit: int = 10, sort_by: str = "highest_pending", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), True)
        except Exception as error:
            logger.exception("Bottom dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
            return {
                "healthy": True, 
                "service": self._service_name, 
                "version": self._version, 
                "database": "connected", 
                "records": int(rows), 
                "ai_enabled": USE_AI and self._ai_agent is not None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2), 
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as error:
            logger.exception("Dealer analytics health check failed")
            return {
                "healthy": False, 
                "service": self._service_name, 
                "version": self._version, 
                "database": "disconnected", 
                "ai_enabled": False,
                "error": str(error), 
                "timestamp": datetime.utcnow().isoformat()
            }

    def validation_query(self) -> dict[str, Any]:
        try:
            with self._session() as session:
                records = session.query(
                    func.count(distinct(func.coalesce(DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.customer_name)))
                ).scalar() or 0
            return {"success": True, "records": int(records), "error": None}
        except Exception as error:
            return {"success": False, "records": 0, "error": str(error)}

    def get_service_metadata(self) -> dict[str, Any]:
        return {
            "service_name": self._service_name, 
            "version": self._version, 
            "status": "DEGRADED" if self._initialization_errors else "READY", 
            "source": "PostgreSQL DeliveryReport", 
            "distance_provider": "OpenRouteService" if self._distance and self._distance._ors else "geopy great-circle",
            "ai_enabled": USE_AI and self._ai_agent is not None,
            "ai_provider": AI_PROVIDER,
            "libraries": {
                "pydantic_ai": Agent is not None,
                "instructor": instructor is not None,
                "sqlglot": sqlglot is not None,
                "pgvector": Vector is not None,
                "pyarrow": pa is not None,
                "sentence_transformers": SentenceTransformer is not None,
            },
            "startup_time": self._startup_time, 
            "initialization_errors": self._initialization_errors
        }


# ============================================================
# SERVICE INITIALIZATION
# ============================================================

_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()


def get_dealer_analytics_service() -> DealerAnalyticsService:
    """Get singleton instance of DealerAnalyticsService"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                try:
                    _service = DealerAnalyticsService()
                    logger.info(f"DealerAnalyticsService initialized (AI enabled: {USE_AI})")
                except Exception as e:
                    logger.exception("DealerAnalyticsService initialization failed")
                    _service = DealerAnalyticsService.__new__(DealerAnalyticsService)
                    _service._service_name = "dealer_analytics"
                    _service._version = "6.1.0-performance-degraded"
                    _service._startup_time = datetime.utcnow().isoformat()
                    _service._initialization_errors = [f"Emergency mode: {str(e)}"]
                    _service._coordinates = CityCoordinateService()
                    _service._distance = None
                    _service._ai_agent = None
                    _service._ai_manager = get_ai_provider_manager() if get_ai_provider_manager else None
                    _service._semantic_search = SemanticSearchEngine()
                    _service._dealer_cache = TTLCache(maxsize=8192, ttl=CACHE_TTL)
                    _service._candidate_cache = TTLCache(maxsize=1, ttl=3600)
                    _service._extended_cache = TTLCache(maxsize=8192, ttl=3600)
                    _service._dashboard_cache = TTLCache(maxsize=8192, ttl=600)
                    _service._ranking_cache = TTLCache(maxsize=256, ttl=600)
                    _service._national_ranking_cache = TTLCache(maxsize=1, ttl=600)
                    _service._search_lock = threading.RLock()
                    _service._aggregate_cache = TTLCache(maxsize=2048, ttl=300)
                    _service._normalized_stop_phrases = set()
                    _service._last_diagnostic = {}
    return _service


__all__ = [
    "DealerAnalyticsService", 
    "DealerDashboard", 
    "DealerComparison", 
    "DealerRanking", 
    "DealerSearchResult", 
    "DistanceAnalytics", 
    "CityCoordinateService", 
    "get_dealer_analytics_service",
    "AIDealerAgent",
    "AIResponse",
    "SemanticSearchEngine",
    "SQLOptimizer",
    "IntentResolver",
    "DealerIntent",
]
