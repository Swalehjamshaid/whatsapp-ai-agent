# ==========================================================
# FILE: app/services/ai_query_service.py (v2.0 - REFACTORED)
# PURPOSE: Natural Language Intelligence Engine
#          Converts Human Questions → Structured Query Plans
#
# WHAT THIS FILE DOES:
# ✅ Understand User Question
# ✅ Extract Meaning
# ✅ Extract Business Intent
# ✅ Extract Entities (with Alias Resolution)
# ✅ Extract Metrics
# ✅ Extract Date Ranges
# ✅ Extract Filters
# ✅ Extract Ranking Requirements
# ✅ Extract Comparison Requirements
# ✅ Extract Executive Insights
# ✅ Extract Root Cause Analysis
# ✅ Create Query Plan
# ✅ Context Awareness
# ✅ Groq Enhancement for Low Confidence
#
# WHAT THIS FILE NEVER DOES:
# ✗ SQL Queries
# ✗ Database Access
# ✗ KPI Calculations
# ✗ Revenue Calculations
# ✗ POD/PGI Calculations
# ✗ WhatsApp Sending
# ✗ Response Formatting
# ✗ Dashboard Formatting
# ✗ Data Aggregation
# ✗ Trend Calculations
# ==========================================================

import re
import json
from enum import Enum
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from loguru import logger
from cachetools import TTLCache

# Optional GROQ for complex queries
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False

from app.config import config


# ==========================================================
# QUERY PLAN DATA CLASS (PRESERVED)
# ==========================================================

@dataclass
class QueryPlan:
    """The main output of this service - contract for entire application"""
    
    # Core intent
    intent: str = "unknown"
    
    # Entity information
    entity_type: Optional[str] = None
    entity_value: Optional[str] = None
    entity_confidence: float = 0.0
    
    # Metrics
    metric: Optional[str] = None
    dimension: Optional[str] = None
    
    # Time filters
    date_range: Optional[Dict[str, str]] = None
    
    # Additional filters
    filters: Dict[str, Any] = field(default_factory=dict)
    
    # Ranking
    ranking_type: Optional[str] = None
    limit: Optional[int] = None
    sort_order: Optional[str] = None
    sort_by: Optional[str] = None
    
    # Comparison
    comparison_entities: Optional[Dict[str, str]] = None
    
    # Dashboard type
    dashboard_type: Optional[str] = None
    
    # Control tower
    control_tower_type: Optional[str] = None
    
    # Trend
    trend_period: Optional[str] = None
    trend_metric: Optional[str] = None
    
    # Root cause
    root_cause_target: Optional[str] = None
    
    # Confidence & routing
    confidence_score: float = 0.0
    requires_groq: bool = False
    requires_kpi: bool = False
    requires_analytics: bool = False
    requires_control_tower: bool = False
    requires_trend_analysis: bool = False
    requires_root_cause: bool = False
    requires_executive_insight: bool = False
    
    # Raw original message
    original_message: str = ""
    normalized_message: str = ""
    
    # Context from previous conversation
    from_context: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert QueryPlan to dictionary for serialization"""
        return {
            "intent": self.intent,
            "entity_type": self.entity_type,
            "entity_value": self.entity_value,
            "entity_confidence": self.entity_confidence,
            "metric": self.metric,
            "dimension": self.dimension,
            "date_range": self.date_range,
            "filters": self.filters,
            "ranking_type": self.ranking_type,
            "limit": self.limit,
            "sort_order": self.sort_order,
            "sort_by": self.sort_by,
            "comparison_entities": self.comparison_entities,
            "dashboard_type": self.dashboard_type,
            "control_tower_type": self.control_tower_type,
            "trend_period": self.trend_period,
            "trend_metric": self.trend_metric,
            "root_cause_target": self.root_cause_target,
            "confidence_score": self.confidence_score,
            "requires_groq": self.requires_groq,
            "requires_kpi": self.requires_kpi,
            "requires_analytics": self.requires_analytics,
            "requires_control_tower": self.requires_control_tower,
            "requires_trend_analysis": self.requires_trend_analysis,
            "requires_root_cause": self.requires_root_cause,
            "requires_executive_insight": self.requires_executive_insight,
            "from_context": self.from_context
        }


# ==========================================================
# INTENT TYPES (ENHANCED)
# ==========================================================

class IntentType:
    DEALER_DASHBOARD = "dealer_dashboard"
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    CITY_DASHBOARD = "city_dashboard"
    PRODUCT_DASHBOARD = "product_dashboard"
    DIVISION_DASHBOARD = "division_dashboard"
    SALES_MANAGER_DASHBOARD = "sales_manager_dashboard"
    DN_LOOKUP = "dn_lookup"
    DN_STATUS = "dn_status"
    POD_ANALYSIS = "pod_analysis"
    PGI_ANALYSIS = "pgi_analysis"
    DELIVERY_ANALYSIS = "delivery_analysis"
    KPI_REPORT = "kpi_report"
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    EXECUTIVE_INSIGHT = "executive_insight"
    CONTROL_TOWER = "control_tower"
    RANKING = "ranking"
    COMPARISON = "comparison"
    TREND = "trend"
    ROOT_CAUSE = "root_cause"
    HELP = "help"
    UNKNOWN = "unknown"


# ==========================================================
# METRIC TYPES (ENHANCED)
# ==========================================================

class MetricType:
    REVENUE = "revenue"
    UNITS = "units"
    DN_COUNT = "dn_count"
    POD_COUNT = "pod_count"
    PGI_COUNT = "pgi_count"
    PENDING_POD = "pending_pod"
    PENDING_DELIVERY = "pending_delivery"
    DELIVERY_AGING = "delivery_aging"
    POD_AGING = "pod_aging"
    FULL_CYCLE = "full_cycle"
    POD_RATE = "pod_rate"
    PGI_RATE = "pgi_rate"
    DELIVERY_RATE = "delivery_rate"


# ==========================================================
# ALIAS MAPPINGS
# ==========================================================

DEALER_ALIASES = {
    "nce": "New China Electronics",
    "new china": "New China Electronics",
    "china electronics": "New China Electronics",
    "ag": "Abdullah Group",
    "abdullah group": "Abdullah Group",
    "mg": "Mian Group",
    "mian group": "Mian Group",
}

WAREHOUSE_ALIASES = {
    "lhr": "Lahore",
    "lahore": "Lahore",
    "rwp": "Rawalpindi",
    "rawalpindi": "Rawalpindi",
    "khi": "Karachi",
    "karachi": "Karachi",
    "isb": "Islamabad",
    "islamabad": "Islamabad",
    "mux": "Multan",
    "multan": "Multan",
    "fsd": "Faisalabad",
    "faisalabad": "Faisalabad",
}

CITY_ALIASES = {
    "lhr": "Lahore",
    "lahore": "Lahore",
    "rwp": "Rawalpindi",
    "rawalpindi": "Rawalpindi",
    "isb": "Islamabad",
    "islamabad": "Islamabad",
    "khi": "Karachi",
    "karachi": "Karachi",
}


# ==========================================================
# GROQ SERVICE (ENHANCED)
# ==========================================================

class GroqQueryService:
    """Dedicated Groq service for query understanding and enhancement"""
    
    def __init__(self):
        self.api_key = config.GROQ_API_KEY if hasattr(config, 'GROQ_API_KEY') else None
        self.model = config.GROQ_MODEL if hasattr(config, 'GROQ_MODEL') else "llama-3.3-70b-versatile"
        self.is_available = bool(self.api_key) and GROQ_AVAILABLE
        
        if self.is_available:
            try:
                self.client = Groq(api_key=self.api_key)
                logger.info("GroqQueryService initialized")
            except Exception as e:
                logger.error(f"Groq initialization failed: {e}")
                self.is_available = False
        else:
            logger.warning("GroqQueryService not available - using rule-based only")
    
    def classify_intent_with_groq(self, question: str) -> Tuple[str, float, Dict]:
        """Use Groq to classify intent when rule-based is uncertain"""
        if not self.is_available:
            return "unknown", 0.0, {}
        
        try:
            system_prompt = """You are an intent classifier for a logistics system.
            Classify the user query into EXACTLY ONE of these categories:
            
            DEALER_DASHBOARD - Asking about a specific dealer's performance
            WAREHOUSE_DASHBOARD - Asking about a warehouse
            EXECUTIVE_INSIGHT - Asking about key issues, bottlenecks, what management should focus on
            CONTROL_TOWER - Asking about critical delays, alerts, urgent issues
            RANKING - Asking for top/best/highest or bottom/worst/lowest
            TREND - Asking about trends, month-over-month, changes over time
            ROOT_CAUSE - Asking why something is happening
            COMPARISON - Comparing two entities (vs, versus, compare)
            HELP - Asking for help, commands, what you can do
            GENERAL_AI - Everything else (greetings, chit-chat, general questions)
            
            Return JSON: {"intent": "CATEGORY", "confidence": 0.95, "reasoning": "brief explanation"}
            Only return JSON, no other text."""
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question[:500]}
                ],
                max_tokens=150,
                temperature=0.1
            )
            
            result = response.choices[0].message.content
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return (
                    data.get("intent", "unknown"),
                    data.get("confidence", 0.5),
                    {"reasoning": data.get("reasoning", "")}
                )
            
            return "unknown", 0.0, {}
            
        except Exception as e:
            logger.error(f"Groq intent classification failed: {e}")
            return "unknown", 0.0, {}
    
    def extract_entities_with_groq(self, question: str, intent: str) -> Dict[str, Any]:
        """Use Groq to extract entities from query"""
        if not self.is_available:
            return {}
        
        try:
            system_prompt = f"""Extract entities from this {intent} query.
            Return JSON with these fields (only include if present):
            - dealer_name: specific dealer name
            - warehouse_name: specific warehouse name  
            - city_name: specific city name
            - dn_number: 8-12 digit number
            - metric: revenue, units, pending_pod, pending_delivery, delivery_aging, pod_aging
            - limit_number: number for top/bottom queries
            - date_range: {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} if mentioned
            
            Return ONLY the JSON, no other text."""
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question[:500]}
                ],
                max_tokens=300,
                temperature=0.1
            )
            
            result = response.choices[0].message.content
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            
            return {}
            
        except Exception as e:
            logger.error(f"Groq entity extraction failed: {e}")
            return {}
    
    def generate_root_cause_analysis(self, question: str, context: Dict = None) -> Optional[str]:
        """Generate root cause analysis for why questions"""
        if not self.is_available:
            return None
        
        try:
            system_prompt = """You are a logistics analyst. Analyze why the user is seeing problems.
            Provide a concise analysis (2-3 sentences) of potential root causes.
            Be specific and actionable. Do NOT query databases - just provide analytical reasoning."""
            
            user_message = question
            if context:
                user_message = f"Context: {context}\nQuestion: {question}"
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                max_tokens=200,
                temperature=0.5
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"Groq root cause analysis failed: {e}")
            return None


# ==========================================================
# ALIAS RESOLVERS (NEW)
# ==========================================================

class DealerAliasResolver:
    """Resolve dealer aliases to full names"""
    
    def __init__(self):
        self.aliases = DEALER_ALIASES
        self.cache = TTLCache(maxsize=200, ttl=3600)
    
    def resolve(self, input_name: str) -> Optional[str]:
        """Resolve alias to full dealer name"""
        if not input_name:
            return None
        
        input_lower = input_name.lower().strip()
        
        # Check cache
        if input_lower in self.cache:
            return self.cache[input_lower]
        
        # Exact alias match
        if input_lower in self.aliases:
            result = self.aliases[input_lower]
            self.cache[input_lower] = result
            return result
        
        # Partial match
        for alias, full_name in self.aliases.items():
            if alias in input_lower or input_lower in alias:
                self.cache[input_lower] = full_name
                return full_name
        
        return None


class WarehouseAliasResolver:
    """Resolve warehouse aliases to full names"""
    
    def __init__(self):
        self.aliases = WAREHOUSE_ALIASES
        self.cache = TTLCache(maxsize=100, ttl=3600)
    
    def resolve(self, input_name: str) -> Optional[str]:
        """Resolve alias to full warehouse name"""
        if not input_name:
            return None
        
        input_lower = input_name.lower().strip()
        
        if input_lower in self.cache:
            return self.cache[input_lower]
        
        if input_lower in self.aliases:
            result = self.aliases[input_lower]
            self.cache[input_lower] = result
            return result
        
        # Check if it's already a full name
        for full_name in set(self.aliases.values()):
            if full_name.lower() == input_lower:
                return full_name
        
        return None


# ==========================================================
# AI QUERY SERVICE (ENHANCED)
# ==========================================================

class AIQueryService:
    """
    Natural Language Intelligence Engine
    Converts human questions into structured query plans
    """
    
    def __init__(self):
        """Initialize the AI Query Service"""
        self.groq = GroqQueryService()
        self.dealer_resolver = DealerAliasResolver()
        self.warehouse_resolver = WarehouseAliasResolver()
        self.intent_cache = TTLCache(maxsize=500, ttl=300)
        
        logger.info("AI Query Service v2.0 initialized with Groq + Alias Resolution")
    
    # ==========================================================
    # 1. PROCESS QUERY - Master Entry Point (PRESERVED SIGNATURE)
    # ==========================================================
    
    async def process_query(self, user_message: str, context: Dict = None) -> QueryPlan:
        """
        Master entry point for query understanding
        
        Input: "Top 5 dealers by pending POD aging in Lahore this month"
        Output: QueryPlan object
        
        Args:
            user_message: The user's question
            context: Optional conversation context (phone_number, last_dealer, etc.)
        """
        logger.info(f"Processing query: {user_message[:100]}")
        
        # Step 1: Normalize query
        normalized = self.normalize_query(user_message)
        
        # Step 2: Detect intent (rule-based first)
        intent, intent_confidence = self.detect_intent_with_confidence(normalized)
        
        # Step 3: If confidence low, use Groq
        if intent_confidence < 0.6 and self.groq.is_available:
            groq_intent, groq_confidence, groq_data = self.groq.classify_intent_with_groq(user_message)
            if groq_confidence > intent_confidence:
                intent = groq_intent
                intent_confidence = groq_confidence
                logger.info(f"Groq override: {intent} with confidence {groq_confidence}")
        
        # Step 4: Extract entities (with alias resolution)
        entities = await self.extract_entities_enhanced(normalized, user_message, intent, context)
        
        # Step 5: Extract metrics
        metric = self.extract_metrics(normalized)
        
        # Step 6: Extract date range
        date_range = self.extract_date_range(normalized)
        
        # Step 7: Extract filters
        filters = self.extract_filters(normalized, entities)
        
        # Step 8: Extract ranking
        ranking = self.extract_ranking(normalized)
        
        # Step 9: Extract comparison
        comparison = self.extract_comparison(normalized)
        
        # Step 10: Detect dashboard type
        dashboard_type = self.detect_dashboard_type(intent, entities)
        
        # Step 11: Build query plan
        query_plan = self.build_query_plan(
            intent=intent,
            entities=entities,
            metric=metric,
            date_range=date_range,
            filters=filters,
            ranking=ranking,
            comparison=comparison,
            dashboard_type=dashboard_type,
            normalized=normalized,
            original=user_message,
            context=context
        )
        
        # Step 12: Calculate multi-factor confidence score
        query_plan.confidence_score = self.calculate_confidence_enhanced(query_plan, intent_confidence)
        
        # Step 13: Set routing flags
        self.set_routing_flags(query_plan)
        
        # Step 14: Validate query plan
        is_valid = self.validate_query_plan(query_plan)
        
        if not is_valid:
            logger.warning(f"Query plan validation failed: {query_plan}")
            query_plan.confidence_score = min(query_plan.confidence_score, 0.4)
            query_plan.requires_groq = True
        
        # Step 15: Use Groq for root cause if applicable
        if query_plan.intent == IntentType.ROOT_CAUSE and self.groq.is_available:
            query_plan.requires_root_cause = True
        
        logger.info(f"Query plan created: intent={query_plan.intent}, confidence={query_plan.confidence_score}")
        
        return query_plan
    
    # ==========================================================
    # 2. NORMALIZE QUERY
    # ==========================================================
    
    def normalize_query(self, message: str) -> str:
        """Clean input text"""
        if not message:
            return ""
        
        normalized = message.lower()
        normalized = re.sub(r'\s+', ' ', normalized)
        normalized = re.sub(r'[^\w\s\-&]', '', normalized)
        normalized = normalized.strip()
        
        return normalized
    
    # ==========================================================
    # 3. DETECT INTENT WITH CONFIDENCE (ENHANCED)
    # ==========================================================
    
    def detect_intent_with_confidence(self, normalized: str) -> Tuple[str, float]:
        """Detect intent with confidence score"""
        
        # Help intent (confidence 1.0)
        if any(word in normalized for word in ['help', 'menu', 'commands', 'what can you do']):
            return IntentType.HELP, 1.0
        
        # Executive Insight (confidence 0.95)
        executive_keywords = [
            'key issue', 'biggest problem', 'bottleneck', 'executive insight',
            'ceo dashboard', 'management summary', 'what should management focus on',
            'top risk', 'biggest risk', 'critical issue'
        ]
        if any(phrase in normalized for phrase in executive_keywords):
            return IntentType.EXECUTIVE_INSIGHT, 0.95
        
        # Root Cause (confidence 0.9)
        if normalized.startswith('why') and any(word in normalized for word in ['delay', 'aging', 'underperforming', 'slow', 'problem']):
            return IntentType.ROOT_CAUSE, 0.9
        
        # Control Tower (confidence 0.9)
        control_keywords = ['critical deliveries', 'critical pod', 'worst dealer', 'worst warehouse', 'control tower', 'alerts']
        if any(phrase in normalized for phrase in control_keywords):
            return IntentType.CONTROL_TOWER, 0.9
        
        # Trend (confidence 0.85)
        if any(word in normalized for word in ['trend', 'trends']) or 'month over month' in normalized:
            return IntentType.TREND, 0.85
        
        # Comparison (confidence 0.85)
        if any(word in normalized for word in ['compare', 'vs', 'versus']) or ' vs ' in normalized:
            return IntentType.COMPARISON, 0.85
        
        # Ranking (confidence 0.9 if keywords present)
        if any(word in normalized for word in ['top', 'bottom', 'best', 'worst', 'highest', 'lowest']):
            return IntentType.RANKING, 0.9
        
        # DN Lookup (confidence 1.0)
        dn_match = re.search(r'\b(\d{8,12})\b', normalized)
        if dn_match:
            return IntentType.DN_LOOKUP, 1.0
        
        # Executive Dashboard (confidence 0.85)
        if any(phrase in normalized for phrase in ['executive dashboard', 'business summary', 'overall performance']):
            return IntentType.EXECUTIVE_DASHBOARD, 0.85
        
        # Warehouse Dashboard (confidence 0.8)
        for warehouse in WAREHOUSE_ALIASES.values():
            if warehouse.lower() in normalized and 'warehouse' in normalized:
                return IntentType.WAREHOUSE_DASHBOARD, 0.8
        
        # Dealer Dashboard (confidence 0.7 - may need Groq)
        if len(normalized.split()) <= 5 and not self._is_question_word(normalized):
            return IntentType.DEALER_DASHBOARD, 0.7
        
        return IntentType.UNKNOWN, 0.3
    
    def _is_question_word(self, text: str) -> bool:
        """Check if text is a question word/phrase"""
        question_words = ['what', 'how', 'why', 'when', 'where', 'who', 'which', 'can you', 'could you']
        return any(word in text for word in question_words)
    
    # ==========================================================
    # 4. EXTRACT ENTITIES ENHANCED (with Alias Resolution)
    # ==========================================================
    
    async def extract_entities_enhanced(self, normalized: str, original: str, 
                                         intent: str, context: Dict = None) -> Dict[str, Any]:
        """Extract entities with alias resolution and context awareness"""
        entities = {}
        
        # Extract DN
        dn_match = re.search(r'\b(\d{8,12})\b', normalized)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        # Extract Warehouse (with alias resolution)
        warehouse_name = self._extract_warehouse_with_alias(normalized)
        if warehouse_name:
            entities['warehouse'] = warehouse_name
            entities['warehouse_name'] = warehouse_name
        
        # Extract City (with alias resolution)
        city_name = self._extract_city_with_alias(normalized)
        if city_name:
            entities['city'] = city_name
            entities['city_name'] = city_name
        
        # Extract Dealer (with alias resolution and context)
        dealer_name = self._extract_dealer_with_alias(original, normalized, context)
        if dealer_name:
            entities['dealer'] = dealer_name
            entities['dealer_name'] = dealer_name
        
        # Extract Division
        divisions = {'refrigerator': 'REF', 'fridge': 'REF', 'tv': 'TV', 'ac': 'CAC'}
        for div_name, div_code in divisions.items():
            if div_name in normalized:
                entities['division'] = div_code
                entities['division_name'] = div_name.title()
                break
        
        # If no entities and context exists, use context
        if not entities and context:
            if context.get('last_dealer'):
                entities['dealer'] = context['last_dealer']
                entities['dealer_name'] = context['last_dealer']
                entities['from_context'] = True
            elif context.get('last_warehouse'):
                entities['warehouse'] = context['last_warehouse']
                entities['warehouse_name'] = context['last_warehouse']
                entities['from_context'] = True
        
        # Use Groq for entity extraction if still no entities and confidence low
        if not entities and self.groq.is_available:
            groq_entities = self.groq.extract_entities_with_groq(original, intent)
            if groq_entities:
                entities.update(groq_entities)
        
        return entities
    
    def _extract_warehouse_with_alias(self, normalized: str) -> Optional[str]:
        """Extract warehouse name with alias resolution"""
        for alias, full_name in WAREHOUSE_ALIASES.items():
            if alias in normalized:
                return full_name
        return None
    
    def _extract_city_with_alias(self, normalized: str) -> Optional[str]:
        """Extract city name with alias resolution"""
        for alias, full_name in CITY_ALIASES.items():
            if alias in normalized:
                return full_name
        return None
    
    def _extract_dealer_with_alias(self, original: str, normalized: str, context: Dict = None) -> Optional[str]:
        """Extract dealer name with alias resolution and context"""
        
        # Check alias first
        input_text = original.lower().strip()
        resolved = self.dealer_resolver.resolve(input_text)
        if resolved:
            return resolved
        
        # Check for explicit dealer pattern
        dealer_match = re.search(r'(?:dealer|show|display)\s+([a-z0-9\s&]+)', normalized)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            resolved = self.dealer_resolver.resolve(candidate)
            return resolved or candidate.title()
        
        # Short message - could be dealer name
        if len(normalized.split()) <= 5 and not self._is_question_word(normalized):
            resolved = self.dealer_resolver.resolve(original)
            return resolved or original.title()
        
        return None
    
    # ==========================================================
    # 5. EXTRACT METRICS
    # ==========================================================
    
    def extract_metrics(self, normalized: str) -> Optional[str]:
        """Determine what KPI is requested"""
        
        metric_map = {
            MetricType.REVENUE: ['revenue', 'sales', 'amount', 'value'],
            MetricType.UNITS: ['units', 'quantity', 'qty', 'pieces'],
            MetricType.DN_COUNT: ['dns', 'delivery notes', 'orders'],
            MetricType.PENDING_POD: ['pending pod', 'pod pending', 'pod not done'],
            MetricType.PENDING_DELIVERY: ['pending delivery', 'delivery pending'],
            MetricType.DELIVERY_AGING: ['delivery aging', 'pgi aging', 'delivery delay'],
            MetricType.POD_AGING: ['pod aging', 'pod delay', 'pod latency'],
            MetricType.POD_RATE: ['pod rate', 'pod percentage', 'pod %'],
            MetricType.PGI_RATE: ['pgi rate', 'pgi percentage', 'pgi %'],
        }
        
        for metric, keywords in metric_map.items():
            if any(keyword in normalized for keyword in keywords):
                return metric
        
        return None
    
    # ==========================================================
    # 6. EXTRACT DATE RANGE (ENHANCED)
    # ==========================================================
    
    def extract_date_range(self, normalized: str) -> Optional[Dict[str, str]]:
        """Convert human dates into SQL dates"""
        today = date.today()
        
        # Today
        if 'today' in normalized:
            return {'start_date': today.isoformat(), 'end_date': today.isoformat()}
        
        # Yesterday
        if 'yesterday' in normalized:
            yesterday = today - timedelta(days=1)
            return {'start_date': yesterday.isoformat(), 'end_date': yesterday.isoformat()}
        
        # Last 7, 15, 30, 90, 180 days
        day_matches = {'last 7 days': 7, 'last 15 days': 15, 'last 30 days': 30, 
                       'last 90 days': 90, 'last 180 days': 180}
        for phrase, days in day_matches.items():
            if phrase in normalized:
                start = today - timedelta(days=days)
                return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # Last month / This month
        if 'last month' in normalized:
            first_of_this_month = today.replace(day=1)
            end = first_of_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': end.isoformat()}
        
        if 'this month' in normalized:
            start = today.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # This year / YTD
        if 'this year' in normalized or 'ytd' in normalized:
            start = date(today.year, 1, 1)
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # Quarter
        quarter_match = re.search(r'q([1-4])', normalized)
        if quarter_match:
            quarter = int(quarter_match.group(1))
            year = today.year
            quarter_starts = {1: (year, 1, 1), 2: (year, 4, 1), 3: (year, 7, 1), 4: (year, 10, 1)}
            quarter_ends = {1: (year, 3, 31), 2: (year, 6, 30), 3: (year, 9, 30), 4: (year, 12, 31)}
            start = date(*quarter_starts[quarter])
            end = date(*quarter_ends[quarter])
            return {'start_date': start.isoformat(), 'end_date': end.isoformat()}
        
        return None
    
    # ==========================================================
    # 7. EXTRACT RANKING
    # ==========================================================
    
    def extract_ranking(self, normalized: str) -> Dict[str, Any]:
        """Identify ranking requests"""
        ranking = {}
        
        if any(word in normalized for word in ['top', 'best', 'highest']):
            ranking['ranking_type'] = 'top'
        elif any(word in normalized for word in ['bottom', 'worst', 'lowest']):
            ranking['ranking_type'] = 'bottom'
        
        limit_match = re.search(r'(?:top|bottom|best|worst)\s+(\d+)', normalized)
        if limit_match:
            ranking['limit'] = int(limit_match.group(1))
        else:
            ranking['limit'] = 10
        
        if 'revenue' in normalized or 'sales' in normalized:
            ranking['sort_by'] = 'revenue'
        elif 'units' in normalized:
            ranking['sort_by'] = 'units'
        elif 'aging' in normalized:
            ranking['sort_by'] = 'aging'
        
        return ranking
    
    # ==========================================================
    # 8. EXTRACT COMPARISON
    # ==========================================================
    
    def extract_comparison(self, normalized: str) -> Optional[Dict[str, str]]:
        """Identify comparison requests"""
        pattern = r'compare\s+([a-z\s]+?)\s+vs\s+([a-z\s]+?)(?:$|\.)'
        match = re.search(pattern, normalized)
        if match:
            return {'left': match.group(1).strip(), 'right': match.group(2).strip()}
        
        pattern2 = r'([a-z\s]+?)\s+vs\s+([a-z\s]+?)(?:$|\.)'
        match2 = re.search(pattern2, normalized)
        if match2:
            return {'left': match2.group(1).strip(), 'right': match2.group(2).strip()}
        
        return None
    
    # ==========================================================
    # 9. EXTRACT FILTERS
    # ==========================================================
    
    def extract_filters(self, normalized: str, entities: Dict[str, Any]) -> Dict[str, Any]:
        """Extract business filters"""
        filters = {}
        
        if entities.get('city'):
            filters['city'] = entities['city']
        if entities.get('warehouse'):
            filters['warehouse'] = entities['warehouse']
        if entities.get('division'):
            filters['division'] = entities['division']
        
        if 'pending' in normalized:
            filters['status'] = 'pending'
        elif 'delivered' in normalized:
            filters['status'] = 'delivered'
        
        location_match = re.search(r'in\s+([a-z]+)', normalized)
        if location_match:
            city = location_match.group(1).title()
            resolved = self._extract_city_with_alias(city.lower())
            if resolved:
                filters['city'] = resolved
        
        return filters
    
    # ==========================================================
    # 10. DETECT DASHBOARD TYPE
    # ==========================================================
    
    def detect_dashboard_type(self, intent: str, entities: Dict[str, Any]) -> Optional[str]:
        """Identify dashboard request"""
        if intent == IntentType.DEALER_DASHBOARD:
            return "dealer_dashboard"
        elif intent == IntentType.WAREHOUSE_DASHBOARD:
            return "warehouse_dashboard"
        elif intent == IntentType.EXECUTIVE_DASHBOARD:
            return "executive_dashboard"
        elif intent == IntentType.EXECUTIVE_INSIGHT:
            return "executive_insight"
        
        if entities.get('dealer'):
            return "dealer_dashboard"
        if entities.get('warehouse'):
            return "warehouse_dashboard"
        
        return None
    
    # ==========================================================
    # 11. BUILD QUERY PLAN
    # ==========================================================
    
    def build_query_plan(
        self,
        intent: str,
        entities: Dict[str, Any],
        metric: Optional[str],
        date_range: Optional[Dict[str, str]],
        filters: Dict[str, Any],
        ranking: Dict[str, Any],
        comparison: Optional[Dict[str, str]],
        dashboard_type: Optional[str],
        normalized: str,
        original: str,
        context: Dict = None
    ) -> QueryPlan:
        """Build the complete QueryPlan object"""
        
        # Determine entity type and value
        entity_type = None
        entity_value = None
        entity_confidence = 0.0
        
        if entities.get('dealer'):
            entity_type = 'dealer'
            entity_value = entities['dealer']
            entity_confidence = 0.9 if entities.get('from_context') else 0.85
        elif entities.get('warehouse'):
            entity_type = 'warehouse'
            entity_value = entities['warehouse']
            entity_confidence = 0.85
        elif entities.get('city'):
            entity_type = 'city'
            entity_value = entities['city']
            entity_confidence = 0.8
        elif entities.get('dn_number'):
            entity_type = 'dn'
            entity_value = entities['dn_number']
            entity_confidence = 1.0
        
        # Determine dimension for ranking
        dimension = None
        if intent == IntentType.RANKING:
            if 'dealer' in normalized:
                dimension = 'dealer'
            elif 'warehouse' in normalized:
                dimension = 'warehouse'
            elif 'city' in normalized:
                dimension = 'city'
        
        return QueryPlan(
            intent=intent,
            entity_type=entity_type,
            entity_value=entity_value,
            entity_confidence=entity_confidence,
            metric=metric,
            dimension=dimension,
            date_range=date_range,
            filters=filters,
            ranking_type=ranking.get('ranking_type'),
            limit=ranking.get('limit'),
            sort_by=ranking.get('sort_by'),
            comparison_entities=comparison,
            dashboard_type=dashboard_type,
            control_tower_type=self._detect_control_tower_type(normalized),
            trend_period=self._detect_trend_period(normalized),
            root_cause_target=self._detect_root_cause_target(normalized),
            original_message=original,
            normalized_message=normalized,
            from_context=bool(entities.get('from_context'))
        )
    
    def _detect_control_tower_type(self, normalized: str) -> Optional[str]:
        """Detect specific control tower query type"""
        if 'critical deliveries' in normalized:
            return 'critical_deliveries'
        if 'critical pod' in normalized:
            return 'critical_pod'
        if 'worst dealer' in normalized:
            return 'worst_dealer'
        if 'worst warehouse' in normalized:
            return 'worst_warehouse'
        return 'general'
    
    def _detect_trend_period(self, normalized: str) -> Optional[str]:
        """Detect trend period"""
        if 'daily' in normalized:
            return 'daily'
        if 'weekly' in normalized:
            return 'weekly'
        if 'monthly' in normalized or 'month over month' in normalized:
            return 'monthly'
        if 'quarterly' in normalized:
            return 'quarterly'
        if 'yearly' in normalized:
            return 'yearly'
        return 'monthly'
    
    def _detect_root_cause_target(self, normalized: str) -> Optional[str]:
        """Detect root cause target"""
        if 'delivery' in normalized and 'delay' in normalized:
            return 'delivery_delays'
        if 'pod' in normalized and 'aging' in normalized:
            return 'pod_aging'
        if 'lahore' in normalized:
            return 'lahore'
        return None
    
    # ==========================================================
    # 12. SET ROUTING FLAGS
    # ==========================================================
    
    def set_routing_flags(self, query_plan: QueryPlan):
        """Set routing flags based on intent"""
        
        query_plan.requires_kpi = query_plan.intent in [
            IntentType.KPI_REPORT, IntentType.EXECUTIVE_DASHBOARD,
            IntentType.POD_ANALYSIS, IntentType.PGI_ANALYSIS, IntentType.DELIVERY_ANALYSIS
        ]
        
        query_plan.requires_analytics = query_plan.intent in [
            IntentType.RANKING, IntentType.COMPARISON, IntentType.TREND, IntentType.CONTROL_TOWER
        ]
        
        query_plan.requires_control_tower = query_plan.intent == IntentType.CONTROL_TOWER
        query_plan.requires_trend_analysis = query_plan.intent == IntentType.TREND
        query_plan.requires_root_cause = query_plan.intent == IntentType.ROOT_CAUSE
        query_plan.requires_executive_insight = query_plan.intent == IntentType.EXECUTIVE_INSIGHT
    
    # ==========================================================
    # 13. VALIDATE QUERY PLAN
    # ==========================================================
    
    def validate_query_plan(self, query_plan: QueryPlan) -> bool:
        """Verify plan is executable"""
        
        valid_intents = [
            IntentType.DEALER_DASHBOARD, IntentType.WAREHOUSE_DASHBOARD,
            IntentType.CITY_DASHBOARD, IntentType.DN_LOOKUP,
            IntentType.KPI_REPORT, IntentType.EXECUTIVE_DASHBOARD,
            IntentType.EXECUTIVE_INSIGHT, IntentType.CONTROL_TOWER,
            IntentType.RANKING, IntentType.COMPARISON, IntentType.TREND,
            IntentType.ROOT_CAUSE, IntentType.HELP
        ]
        
        if query_plan.intent not in valid_intents:
            logger.warning(f"Invalid intent: {query_plan.intent}")
            return False
        
        if query_plan.intent == IntentType.RANKING and not query_plan.limit:
            query_plan.limit = 10
        
        return True
    
    # ==========================================================
    # 14. CALCULATE CONFIDENCE ENHANCED
    # ==========================================================
    
    def calculate_confidence_enhanced(self, query_plan: QueryPlan, intent_confidence: float) -> float:
        """Multi-factor confidence calculation"""
        
        score = intent_confidence * 0.3  # Intent weight: 30%
        
        # Entity confidence (25%)
        if query_plan.entity_confidence > 0:
            score += query_plan.entity_confidence * 0.25
        elif query_plan.entity_type and query_plan.entity_value:
            score += 0.2
        elif query_plan.entity_type or query_plan.entity_value:
            score += 0.1
        
        # Metric confidence (20%)
        if query_plan.metric:
            score += 0.2
        
        # Filters confidence (15%)
        if query_plan.filters:
            score += 0.15
        
        # Date range confidence (10%)
        if query_plan.date_range:
            score += 0.1
        
        # Context boost (additional 10%)
        if query_plan.from_context:
            score = min(score + 0.1, 1.0)
        
        return round(score, 2)


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_ai_query_service = None

def get_ai_query_service() -> AIQueryService:
    """Get singleton instance of AIQueryService"""
    global _ai_query_service
    if _ai_query_service is None:
        _ai_query_service = AIQueryService()
    return _ai_query_service


# ==========================================================
# INITIALIZATION LOGGING
# ==========================================================

logger.info("=" * 60)
logger.info("AI Query Service v2.0 - Natural Language Intelligence Engine")
logger.info("=" * 60)
logger.info("")
logger.info("   RESPONSIBILITIES:")
logger.info("   ✅ Natural Language Understanding")
logger.info("   ✅ Intent Detection (Rule-based + Groq)")
logger.info("   ✅ Entity Extraction (with Alias Resolution)")
logger.info("   ✅ Metric Extraction")
logger.info("   ✅ Date Intelligence")
logger.info("   ✅ Ranking Detection")
logger.info("   ✅ Comparison Detection")
logger.info("   ✅ Executive Insight Detection")
logger.info("   ✅ Root Cause Detection")
logger.info("   ✅ Context Awareness")
logger.info("   ✅ Query Planning")
logger.info("")
logger.info("   WHAT IT NEVER DOES:")
logger.info("   ✗ SQL Queries")
logger.info("   ✗ KPI Calculations")
logger.info("   ✗ WhatsApp Sending")
logger.info("   ✗ Dashboard Formatting")
logger.info("")
logger.info(f"   GROQ AVAILABLE: {GROQ_AVAILABLE and bool(getattr(config, 'GROQ_API_KEY', ''))}")
logger.info("   STATUS: ✅ READY")
logger.info("=" * 60)
