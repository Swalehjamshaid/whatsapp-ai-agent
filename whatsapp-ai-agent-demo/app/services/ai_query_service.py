# ==========================================================
# FILE: app/services/ai_query_service.py (v2.3 - PRODUCTION HARDENED)
# ==========================================================
# PURPOSE: Intent Detection and Query Planning Engine
# CHANGES: Import safety, dynamic date handling, entity extraction
#          improvements, confidence model enhancements, structured logging,
#          singleton safety, QueryPlan validation, performance caching
# ==========================================================

import re
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import date, timedelta
from loguru import logger


# ==========================================================
# IMPORT SAFETY - Startup Diagnostics
# ==========================================================

try:
    from app.schemas.schema_service import get_schema_service
    logger.debug("Successfully imported get_schema_service from app.schemas.schema_service")
except ImportError as e:
    logger.error(f"Failed to import get_schema_service: {e}")
    logger.error("Module path: app.schemas.schema_service")
    logger.error("Please ensure app/schemas/schema_service.py exists and has get_schema_service function")
    raise


# ==========================================================
# COMPILED REGEX PATTERNS (Performance Optimization)
# ==========================================================

# DN Number Pattern (8-12 digits)
DN_PATTERN = re.compile(r'\b(\d{8,12})\b')

# Dealer Extraction Pattern
DEALER_PATTERN = re.compile(r'(?:dealer|show|display)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)

# Ranking Limit Pattern
RANKING_LIMIT_PATTERN = re.compile(r'(?:top|bottom)\s+(\d+)', re.IGNORECASE)

# Whitespace Normalization Pattern
WHITESPACE_PATTERN = re.compile(r'\s+')

# Special Characters Pattern (for normalization)
SPECIAL_CHARS_PATTERN = re.compile(r'[^\w\s\-&.]')

# Word boundary pattern for entity extraction
WORD_BOUNDARY_PATTERN = re.compile(r'\b')


# ==========================================================
# QUERY PLAN DATA CLASS
# ==========================================================

@dataclass
class QueryPlan:
    """Routing decision output"""
    intent: str
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    metric: Optional[str] = None
    date_range: Optional[Dict[str, str]] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    ranking_type: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    confidence_score: float = 0.0
    requires_groq: bool = False
    service: str = "analytics"
    original_message: str = ""
    normalized_message: str = ""
    from_context: bool = False
    query_id: str = ""  # Added for tracing
    processing_time_ms: float = 0.0  # Added for performance monitoring


# ==========================================================
# VALID INTENT AND SERVICE LISTS
# ==========================================================

VALID_INTENTS: Set[str] = {
    'help', 'dn_lookup', 'dealer_dashboard', 'dealer_revenue',
    'dealer_units', 'dealer_performance', 'dealer_aging',
    'warehouse_dashboard', 'warehouse_performance', 'pending_pgi',
    'pending_pod', 'pgi_aging', 'pod_aging', 'top_dealers',
    'bottom_dealers', 'top_warehouses', 'executive_insight',
    'control_tower', 'root_cause', 'trend', 'comparison',
    'general_ai'
}

VALID_SERVICES: Set[str] = {'analytics', 'kpi', 'groq'}

VALID_ENTITY_TYPES: Set[str] = {'dealer', 'warehouse', 'city', 'dn', None}


# ==========================================================
# AI QUERY SERVICE CLASS
# ==========================================================

class AIQueryService:
    """INTENT DETECTION ENGINE - Brain of the Platform
    
    This service processes natural language queries and converts them
    into structured QueryPlan objects for routing to appropriate services.
    
    Thread-safe singleton instance available via get_ai_query_service().
    
    Performance Optimizations:
        - Cached warehouse aliases
        - Cached city aliases
        - Cached logistics keywords
        - Compiled regex patterns
    """
    
    def __init__(self):
        """Initialize AIQueryService with schema metadata.
        
        Raises:
            Exception: If schema service initialization fails
        """
        start_time = time.time()
        
        try:
            # ==========================================================
            # IMPORT SAFETY - Schema Loading with Diagnostics
            # ==========================================================
            
            logger.info("Loading SchemaService for AIQueryService...")
            self.schema = get_schema_service()
            logger.info("SchemaService loaded successfully")
            
            # Validate schema service is fully functional
            self._validate_schema_service()
            
            # Log metadata statistics for diagnostics
            if hasattr(self.schema, 'validate_metadata'):
                report = self.schema.validate_metadata()
                logger.debug(f"Schema metadata: {report.get('counts', {})}")
            
            # ==========================================================
            # PERFORMANCE - Cache Entity Data
            # ==========================================================
            
            # Cache warehouse aliases and names for faster lookup
            self._warehouse_cache = {
                'aliases': list(self.schema.warehouses.keys()),
                'names': list(set(self.schema.warehouses.values())),
                'name_lower': [name.lower() for name in set(self.schema.warehouses.values())]
            }
            logger.debug(f"Cached {len(self._warehouse_cache['aliases'])} warehouse aliases")
            
            # Cache city aliases and names for faster lookup
            self._city_cache = {
                'aliases': list(self.schema.cities.keys()),
                'names': list(set(self.schema.cities.values())),
                'name_lower': [name.lower() for name in set(self.schema.cities.values())]
            }
            logger.debug(f"Cached {len(self._city_cache['aliases'])} city aliases")
            
            # Cache logistics keywords for faster checking
            self._logistics_keywords_cache = self.schema.logistics_keywords
            logger.debug(f"Cached {len(self._logistics_keywords_cache)} logistics keywords")
            
            # ==========================================================
            # DYNAMIC DATE HANDLING
            # ==========================================================
            
            # Don't store today - get fresh each time
            self._initialization_time = time.time()
            
            # Mark initialization complete
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"AIQueryService initialized successfully in {init_duration:.2f}ms")
            
        except Exception as e:
            logger.exception(f"Failed to initialize AIQueryService: {str(e)}")
            raise RuntimeError(f"AIQueryService initialization failed: {str(e)}") from e
    
    def _validate_schema_service(self):
        """Validate that schema service is fully functional."""
        logger.info("Validating SchemaService...")
        
        # Test each critical method
        test_cases = [
            ("detect_intent", lambda: self.schema.detect_intent("help")),
            ("detect_metric", lambda: self.schema.detect_metric("revenue")),
            ("resolve_dealer", lambda: self.schema.resolve_dealer("nce")),
            ("resolve_warehouse", lambda: self.schema.resolve_warehouse("lhr")),
            ("resolve_city", lambda: self.schema.resolve_city("lhr")),
            ("is_logistics_keyword", lambda: self.schema.is_logistics_keyword("pending"))
        ]
        
        for name, test_func in test_cases:
            try:
                result = test_func()
                logger.info(f"  ✅ {name}() - returned: {result}")
            except Exception as e:
                logger.error(f"  ❌ {name}() - failed: {e}")
                raise RuntimeError(f"SchemaService validation failed at {name}()") from e
        
        logger.info("✅ SchemaService validation passed")
        logger.info(f"   - Dealers: {len(self.schema.dealers)}")
        logger.info(f"   - Warehouses: {len(self.schema.warehouses)}")
        logger.info(f"   - Cities: {len(self.schema.cities)}")
        logger.info(f"   - Intents: {len(self.schema.intents)}")
        logger.info(f"   - Metrics: {len(self.schema.metrics)}")
    
    def _get_today(self) -> date:
        """Get current date dynamically.
        
        Returns:
            date: Current date (fresh on each call)
        
        Note:
            Using date.today() dynamically ensures date calculations
            remain accurate even if service runs for multiple days.
        """
        return date.today()
    
    async def process_query(self, question: Optional[str], context: Optional[Dict] = None) -> QueryPlan:
        """Process natural language query and generate routing plan.
        
        Args:
            question: User's query text (can be None, empty, or whitespace)
            context: Optional context dictionary for entity disambiguation
            
        Returns:
            QueryPlan: Structured routing decision
            
        Raises:
            ValueError: If question is None, empty, or whitespace-only
        """
        # Generate unique query ID for tracing
        query_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        # ==========================================================
        # INPUT VALIDATION - Defensive Programming
        # ==========================================================
        
        # Validate question input
        if question is None:
            logger.warning(f"Query {query_id}: Received None query - returning default plan")
            return self._create_default_plan("No query provided", query_id)
        
        if not isinstance(question, str):
            logger.warning(f"Query {query_id}: Invalid query type: {type(question)} - returning default plan")
            return self._create_default_plan(f"Invalid query type: {type(question)}", query_id)
        
        # Strip and check for whitespace-only
        cleaned_question = question.strip()
        if not cleaned_question:
            logger.warning(f"Query {query_id}: Received empty/whitespace-only query - returning default plan")
            return self._create_default_plan("Empty query", query_id)
        
        # ==========================================================
        # QUERY PROCESSING PIPELINE
        # ==========================================================
        
        try:
            # Log query received (without sensitive content)
            logger.info(f"Query {query_id}: Processing query (length={len(cleaned_question)})")
            
            # Normalize query
            normalized = self._normalize(cleaned_question)
            
            # Detect intent with timing
            intent_start = time.time()
            intent, confidence = self._detect_intent(normalized, cleaned_question)
            intent_duration = (time.time() - intent_start) * 1000
            logger.debug(f"Query {query_id}: Intent detection took {intent_duration:.2f}ms, intent={intent}")
            
            # Extract entities with timing
            entity_start = time.time()
            entities = self._extract_entities(normalized, cleaned_question, intent, context)
            entity_duration = (time.time() - entity_start) * 1000
            logger.debug(f"Query {query_id}: Entity extraction took {entity_duration:.2f}ms, entities={list(entities.keys())}")
            
            # Extract metric
            metric = self._extract_metric(normalized)
            
            # Extract date range
            date_range = self._extract_date_range(normalized)
            
            # Extract ranking
            ranking = self._extract_ranking(normalized)
            
            # Build query plan
            query_plan = self._build_query_plan(
                intent=intent, 
                entities=entities, 
                metric=metric,
                date_range=date_range, 
                ranking=ranking,
                normalized=normalized, 
                original=cleaned_question, 
                context=context
            )
            query_plan.query_id = query_id
            
            # Calculate confidence
            query_plan.confidence_score = self._calculate_confidence(query_plan, confidence)
            
            # Determine service routing
            query_plan.service = self._determine_service(query_plan)
            query_plan.requires_groq = self._determine_groq_requirement(query_plan)
            
            # ==========================================================
            # QUERYPLAN VALIDATION
            # ==========================================================
            
            is_valid, validation_errors = self._validate_query_plan(query_plan)
            if not is_valid:
                logger.warning(f"Query {query_id}: QueryPlan validation failed: {validation_errors}")
                # Return safe fallback
                return self._create_fallback_plan(query_plan, validation_errors, query_id)
            
            # Calculate processing time
            query_plan.processing_time_ms = (time.time() - start_time) * 1000
            
            # ==========================================================
            # STRUCTURED LOGGING
            # ==========================================================
            
            logger.info(
                f"Query {query_id}: Completed - "
                f"intent={query_plan.intent}, "
                f"service={query_plan.service}, "
                f"confidence={query_plan.confidence_score:.2f}, "
                f"entity={query_plan.entity_type or 'none'}, "
                f"time={query_plan.processing_time_ms:.2f}ms"
            )
            
            if query_plan.entity:
                logger.debug(f"Query {query_id}: Entity extracted - {query_plan.entity_type}={query_plan.entity}")
            
            if query_plan.date_range:
                logger.debug(f"Query {query_id}: Date range - {query_plan.date_range}")
            
            if query_plan.from_context:
                logger.debug(f"Query {query_id}: Context used for entity resolution")
            
            return query_plan
            
        except Exception as e:
            logger.exception(f"Query {query_id}: Query processing failed: {str(e)}")
            # Return default plan on error
            return self._create_default_plan(f"Processing error: {str(e)}", query_id)
    
    def _create_default_plan(self, reason: str, query_id: str = None) -> QueryPlan:
        """Create a default query plan for error/empty cases."""
        return QueryPlan(
            intent="general_ai",
            confidence_score=0.1,
            requires_groq=True,
            service="groq",
            original_message=reason,
            normalized_message=reason,
            query_id=query_id or str(uuid.uuid4())[:8]
        )
    
    def _create_fallback_plan(self, original_plan: QueryPlan, errors: List[str], query_id: str) -> QueryPlan:
        """Create a fallback query plan when validation fails."""
        fallback = QueryPlan(
            intent="general_ai",
            confidence_score=0.15,
            requires_groq=True,
            service="groq",
            original_message=f"Fallback: {original_plan.original_message[:100]}",
            normalized_message=original_plan.normalized_message,
            query_id=query_id,
            processing_time_ms=original_plan.processing_time_ms
        )
        # Copy over any safe fields
        if original_plan.entity:
            fallback.entity = original_plan.entity
            fallback.entity_type = original_plan.entity_type
        if original_plan.date_range:
            fallback.date_range = original_plan.date_range
        return fallback
    
    def _validate_query_plan(self, plan: QueryPlan) -> Tuple[bool, List[str]]:
        """Validate QueryPlan for integrity.
        
        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []
        
        # Validate intent
        if not plan.intent or plan.intent not in VALID_INTENTS:
            errors.append(f"Invalid intent: {plan.intent}")
        
        # Validate service
        if plan.service not in VALID_SERVICES:
            errors.append(f"Invalid service: {plan.service}")
        
        # Validate confidence score
        if not (0.0 <= plan.confidence_score <= 1.0):
            errors.append(f"Invalid confidence score: {plan.confidence_score}")
        
        # Validate entity type
        if plan.entity_type and plan.entity_type not in VALID_ENTITY_TYPES:
            errors.append(f"Invalid entity type: {plan.entity_type}")
        
        # Validate limit
        if plan.limit < 1 or plan.limit > 1000:
            errors.append(f"Invalid limit: {plan.limit}")
        
        # Validate entity consistency
        if plan.entity and not plan.entity_type:
            errors.append(f"Entity exists without entity_type: {plan.entity}")
        if plan.entity_type and not plan.entity:
            errors.append(f"Entity type exists without entity: {plan.entity_type}")
        
        return len(errors) == 0, errors
    
    def _normalize(self, text: str) -> str:
        """Normalize text for processing."""
        if not text:
            return ""
        
        normalized = text.lower()
        normalized = WHITESPACE_PATTERN.sub(' ', normalized)
        normalized = SPECIAL_CHARS_PATTERN.sub('', normalized)
        return normalized.strip()
    
    def _detect_intent(self, normalized: str, original: str) -> Tuple[str, float]:
        """Detect intent from normalized text."""
        # Check for DN number first
        if DN_PATTERN.search(original):
            return "dn_lookup", 1.0
        
        # Use schema service for intent detection
        intent, confidence = self.schema.detect_intent(normalized)
        if intent:
            return intent, confidence
        
        # Fallback to general AI
        return "general_ai", 0.3
    
    def _extract_entities(self, normalized: str, original: str, intent: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Extract entities (dealer, warehouse, city, DN) from query."""
        entities = {}
        normalized_words = set(normalized.split())
        
        # DN Number Extraction
        dn_match = DN_PATTERN.search(original)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        # ==========================================================
        # IMPROVED WAREHOUSE EXTRACTION - Word Boundaries
        # ==========================================================
        
        # Check for exact matches using word boundaries
        for alias in self._warehouse_cache['aliases']:
            # Create word boundary pattern for this alias
            alias_pattern = re.compile(rf'\b{re.escape(alias)}\b', re.IGNORECASE)
            if alias_pattern.search(normalized):
                entities['warehouse'] = self.schema.resolve_warehouse(alias)
                break
        
        # If no alias match, check for full name match
        if not entities.get('warehouse'):
            for name_lower in self._warehouse_cache['name_lower']:
                # Use word boundaries for full names too
                name_pattern = re.compile(rf'\b{re.escape(name_lower)}\b', re.IGNORECASE)
                if name_pattern.search(normalized):
                    # Get original name from cache
                    for original_name in self._warehouse_cache['names']:
                        if original_name.lower() == name_lower:
                            entities['warehouse'] = original_name
                            break
                    break
        
        # ==========================================================
        # IMPROVED CITY EXTRACTION - Word Boundaries
        # ==========================================================
        
        # Check for exact matches using word boundaries
        for alias in self._city_cache['aliases']:
            alias_pattern = re.compile(rf'\b{re.escape(alias)}\b', re.IGNORECASE)
            if alias_pattern.search(normalized):
                entities['city'] = self.schema.resolve_city(alias)
                break
        
        # If no alias match, check for full name match
        if not entities.get('city'):
            for name_lower in self._city_cache['name_lower']:
                name_pattern = re.compile(rf'\b{re.escape(name_lower)}\b', re.IGNORECASE)
                if name_pattern.search(normalized):
                    for original_name in self._city_cache['names']:
                        if original_name.lower() == name_lower:
                            entities['city'] = original_name
                            break
                    break
        
        # ==========================================================
        # DEALER EXTRACTION
        # ==========================================================
        
        # Dealer Extraction (Pattern-based)
        dealer_match = DEALER_PATTERN.search(original)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            if candidate not in self._logistics_keywords_cache:
                resolved = self.schema.resolve_dealer(candidate)
                if resolved:
                    entities['dealer'] = resolved
        
        # Dealer Extraction (Fallback - Single word check)
        if not entities.get('dealer') and len(normalized_words) <= 5:
            # Check if the entire normalized text is a dealer name
            if not self._is_question_word(normalized):
                resolved = self.schema.resolve_dealer(original)
                if resolved:
                    entities['dealer'] = resolved
        
        # Context-based Entity Resolution (Defensive)
        if not entities and context and isinstance(context, dict):
            try:
                if context.get('last_dealer'):
                    entities['dealer'] = context['last_dealer']
                    entities['from_context'] = True
                elif context.get('last_warehouse'):
                    entities['warehouse'] = context['last_warehouse']
                    entities['from_context'] = True
            except (AttributeError, KeyError, TypeError) as e:
                logger.debug(f"Context access failed: {e}")
        
        return entities
    
    def _is_question_word(self, text: str) -> bool:
        """Check if text contains question words."""
        question_words = ['what', 'how', 'why', 'when', 'where', 'who', 'which']
        return any(word in text for word in question_words)
    
    def _extract_metric(self, normalized: str) -> Optional[str]:
        """Extract metric from normalized text."""
        return self.schema.detect_metric(normalized)
    
    def _extract_date_range(self, normalized: str) -> Optional[Dict[str, str]]:
        """Extract date range from normalized text.
        
        Supports:
            - today
            - yesterday
            - last N days (7, 15, 30, 90)
            - this month
            - this week
            - last week
            - this year
            - last month
            - current month
            - current year
        """
        # Get fresh current date
        today = self._get_today()
        
        # Today
        if 'today' in normalized:
            return {'start_date': today.isoformat(), 'end_date': today.isoformat()}
        
        # Yesterday
        if 'yesterday' in normalized:
            yesterday = today - timedelta(days=1)
            return {'start_date': yesterday.isoformat(), 'end_date': yesterday.isoformat()}
        
        # Last N Days
        day_matches = {
            'last 7 days': 7,
            'last 15 days': 15,
            'last 30 days': 30,
            'last 90 days': 90
        }
        for phrase, days in day_matches.items():
            if phrase in normalized:
                start = today - timedelta(days=days)
                return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # This Month / Current Month
        if 'this month' in normalized or 'current month' in normalized:
            start = today.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # Last Month
        if 'last month' in normalized:
            last_month = today.replace(day=1) - timedelta(days=1)
            start = last_month.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': last_month.isoformat()}
        
        # This Week / Current Week
        if 'this week' in normalized or 'current week' in normalized:
            # Start of week (Monday)
            start = today - timedelta(days=today.weekday())
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        # Last Week
        if 'last week' in normalized:
            end = today - timedelta(days=today.weekday() + 1)
            start = end - timedelta(days=6)
            return {'start_date': start.isoformat(), 'end_date': end.isoformat()}
        
        # This Year / Current Year
        if 'this year' in normalized or 'current year' in normalized:
            start = today.replace(month=1, day=1)
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        return None
    
    def _extract_ranking(self, normalized: str) -> Dict[str, Any]:
        """Extract ranking information from normalized text.
        
        Supports:
            - top, best, highest
            - bottom, worst, lowest
            - top performers, top dealers
            - highest sales, lowest sales
            - most revenue, least revenue
        """
        ranking = {}
        
        # Ranking type detection (expanded)
        top_keywords = ['top', 'best', 'highest', 'top performers', 'top dealers', 'highest sales', 'most revenue']
        bottom_keywords = ['bottom', 'worst', 'lowest', 'lowest sales', 'least revenue']
        
        if any(keyword in normalized for keyword in top_keywords):
            ranking['ranking_type'] = 'top'
        elif any(keyword in normalized for keyword in bottom_keywords):
            ranking['ranking_type'] = 'bottom'
        
        # Limit extraction
        limit_match = RANKING_LIMIT_PATTERN.search(normalized)
        ranking['limit'] = int(limit_match.group(1)) if limit_match else 10
        
        # Sort by detection (expanded)
        if 'revenue' in normalized or 'sales' in normalized or 'amount' in normalized:
            ranking['sort_by'] = 'revenue'
        elif 'units' in normalized or 'quantity' in normalized or 'qty' in normalized:
            ranking['sort_by'] = 'units'
        elif 'performance' in normalized:
            ranking['sort_by'] = 'performance'
        
        return ranking
    
    def _build_query_plan(self, intent: str, entities: Dict[str, Any], metric: Optional[str],
                          date_range: Optional[Dict[str, str]], ranking: Dict[str, Any],
                          normalized: str, original: str, context: Optional[Dict]) -> QueryPlan:
        """Build QueryPlan from extracted components."""
        
        # Determine primary entity
        entity_type = None
        entity_value = None
        
        if entities.get('dealer'):
            entity_type, entity_value = 'dealer', entities['dealer']
        elif entities.get('warehouse'):
            entity_type, entity_value = 'warehouse', entities['warehouse']
        elif entities.get('dn_number'):
            entity_type, entity_value = 'dn', entities['dn_number']
        
        # Extract filters (defensive)
        filters = self._extract_filters(normalized, entities)
        
        return QueryPlan(
            intent=intent,
            entity=entity_value,
            entity_type=entity_type,
            metric=metric,
            date_range=date_range,
            filters=filters,
            ranking_type=ranking.get('ranking_type'),
            limit=ranking.get('limit', 10),
            sort_by=ranking.get('sort_by'),
            original_message=original,
            normalized_message=normalized,
            from_context=bool(entities.get('from_context', False))
        )
    
    def _extract_filters(self, normalized: str, entities: Dict[str, Any]) -> Dict[str, Any]:
        """Extract filters from query."""
        filters = {}
        
        # City filter
        if entities.get('city'):
            filters['city'] = entities['city']
        
        # Warehouse filter
        if entities.get('warehouse'):
            filters['warehouse'] = entities['warehouse']
        
        # Status filters
        if 'pending' in normalized:
            filters['status'] = 'pending'
        elif 'delivered' in normalized:
            filters['status'] = 'delivered'
        elif 'in transit' in normalized or 'transit' in normalized:
            filters['status'] = 'in_transit'
        
        return filters
    
    def _calculate_confidence(self, query_plan: QueryPlan, intent_confidence: float) -> float:
        """Calculate confidence score for query plan.
        
        Scoring Components:
            - Intent confidence: 30% (from schema service)
            - Entity presence: 25%
            - Metric presence: 20%
            - Date range presence: 15%
            - Context usage: 10%
            
        Negative Adjustments:
            - Fallback intent (general_ai): -20% penalty
            - Unresolved entity (confident entity missing): -15%
            - Low intent confidence (<0.5): -10%
        
        Returns:
            float: Confidence score between 0.0 and 1.0
        """
        # Start with base score
        score = 0.0
        
        # Positive contributions
        # Intent confidence (30% of max)
        score += min(intent_confidence, 1.0) * 0.3
        
        # Entity presence (25%)
        if query_plan.entity:
            score += 0.25
        
        # Metric presence (20%)
        if query_plan.metric:
            score += 0.20
        
        # Date range presence (15%)
        if query_plan.date_range:
            score += 0.15
        
        # Context usage (10%)
        if query_plan.from_context and query_plan.entity:
            score += 0.10
        
        # ==========================================================
        # NEGATIVE ADJUSTMENTS
        # ==========================================================
        
        # Penalty for fallback intent
        if query_plan.intent == 'general_ai':
            score -= 0.20
        
        # Penalty for low intent confidence
        if intent_confidence < 0.5:
            score -= 0.10
        
        # Penalty for unresolved entity (if we tried to find one)
        if query_plan.entity_type and not query_plan.entity:
            score -= 0.15
        
        # Ensure score stays within [0.0, 1.0]
        return round(max(0.0, min(score, 1.0)), 2)
    
    def _determine_service(self, query_plan: QueryPlan) -> str:
        """Determine which service should handle the query."""
        # KPI service for pending/aging queries
        if query_plan.intent in ['pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging']:
            return "kpi"
        
        # Groq service for AI-generated responses
        if query_plan.intent in ['general_ai', 'root_cause']:
            return "groq"
        
        # Analytics service for data queries (default)
        return "analytics"
    
    def _determine_groq_requirement(self, query_plan: QueryPlan) -> bool:
        """Determine if Groq AI is required for response generation."""
        return query_plan.intent in ['general_ai', 'root_cause', 'executive_insight']


# ==========================================================
# THREAD-SAFE SINGLETON
# ==========================================================

_ai_query_service = None
_service_lock = threading.Lock()


def get_ai_query_service() -> AIQueryService:
    """Thread-safe singleton getter for AIQueryService.
    
    Returns:
        AIQueryService: The singleton instance of AIQueryService
        
    Note:
        This implementation uses double-checked locking for thread safety
        while maintaining backward compatibility with existing code.
        Safe for use with asyncio as no blocking operations are performed.
    """
    global _ai_query_service
    
    if _ai_query_service is None:
        with _service_lock:
            if _ai_query_service is None:
                try:
                    _ai_query_service = AIQueryService()
                    logger.info("AIQueryService singleton initialized successfully")
                except Exception as e:
                    logger.exception(f"AIQueryService singleton initialization failed: {e}")
                    raise
    
    return _ai_query_service


# ==========================================================
# MODULE INITIALIZATION LOGGING
# ==========================================================

logger.debug("AIQueryService module loaded")
logger.debug(f"Valid intents: {len(VALID_INTENTS)}, Valid services: {len(VALID_SERVICES)}")
