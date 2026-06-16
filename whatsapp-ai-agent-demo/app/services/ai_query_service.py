# ==========================================================
# FILE: app/services/ai_query_service.py (v2.2 - PRODUCTION HARDENED)
# ==========================================================
# PURPOSE: Intent Detection and Query Planning Engine
# CHANGES: Thread-safe singleton, input validation, compiled regex,
#          enhanced date parsing, better confidence scoring,
#          structured logging, defensive programming
# ==========================================================

import re
import threading
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from datetime import date, timedelta
from loguru import logger

from app.schemas.schema_service import get_schema_service


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


# ==========================================================
# AI QUERY SERVICE CLASS
# ==========================================================

class AIQueryService:
    """INTENT DETECTION ENGINE - Brain of the Platform
    
    This service processes natural language queries and converts them
    into structured QueryPlan objects for routing to appropriate services.
    
    Thread-safe singleton instance available via get_ai_query_service().
    """
    
    def __init__(self):
        """Initialize AIQueryService with schema metadata.
        
        Raises:
            Exception: If schema service initialization fails
        """
        try:
            self.schema = get_schema_service()
            self.today = date.today()
            logger.info("AIQueryService initialized successfully")
            
            # Log metadata statistics for diagnostics
            if hasattr(self.schema, 'validate_metadata'):
                report = self.schema.validate_metadata()
                logger.debug(f"Schema metadata: {report.get('counts', {})}")
                
        except Exception as e:
            logger.exception(f"Failed to initialize AIQueryService: {str(e)}")
            raise RuntimeError(f"AIQueryService initialization failed: {str(e)}") from e
    
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
        # ==========================================================
        # INPUT VALIDATION - Defensive Programming
        # ==========================================================
        
        # Validate question input
        if question is None:
            logger.warning("Received None query - returning default plan")
            return self._create_default_plan("No query provided")
        
        if not isinstance(question, str):
            logger.warning(f"Invalid query type: {type(question)} - returning default plan")
            return self._create_default_plan(f"Invalid query type: {type(question)}")
        
        # Strip and check for whitespace-only
        cleaned_question = question.strip()
        if not cleaned_question:
            logger.warning("Received empty/whitespace-only query - returning default plan")
            return self._create_default_plan("Empty query")
        
        # ==========================================================
        # QUERY PROCESSING PIPELINE
        # ==========================================================
        
        try:
            # Normalize query
            normalized = self._normalize(cleaned_question)
            
            # Detect intent
            intent, confidence = self._detect_intent(normalized, cleaned_question)
            
            # Extract entities
            entities = self._extract_entities(normalized, cleaned_question, intent, context)
            
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
            
            # Calculate confidence
            query_plan.confidence_score = self._calculate_confidence(query_plan, confidence)
            
            # Determine service routing
            query_plan.service = self._determine_service(query_plan)
            query_plan.requires_groq = self._determine_groq_requirement(query_plan)
            
            # ==========================================================
            # STRUCTURED LOGGING
            # ==========================================================
            
            logger.info(
                f"Query processed: intent={query_plan.intent}, "
                f"service={query_plan.service}, "
                f"confidence={query_plan.confidence_score:.2f}, "
                f"entity={query_plan.entity_type or 'none'}"
            )
            
            if query_plan.entity:
                logger.debug(f"Entity extracted: {query_plan.entity_type}={query_plan.entity}")
            
            if query_plan.date_range:
                logger.debug(f"Date range: {query_plan.date_range}")
            
            if query_plan.from_context:
                logger.debug("Context used for entity resolution")
            
            return query_plan
            
        except Exception as e:
            logger.exception(f"Query processing failed: {str(e)}")
            # Return default plan on error
            return self._create_default_plan(f"Processing error: {str(e)}")
    
    def _create_default_plan(self, reason: str) -> QueryPlan:
        """Create a default query plan for error/empty cases."""
        return QueryPlan(
            intent="general_ai",
            confidence_score=0.1,
            requires_groq=True,
            service="groq",
            original_message=reason,
            normalized_message=reason
        )
    
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
        
        # DN Number Extraction
        dn_match = DN_PATTERN.search(original)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        # Warehouse Extraction
        for alias, full_name in self.schema.warehouses.items():
            if alias in normalized or full_name.lower() in normalized:
                entities['warehouse'] = full_name
                break
        
        # City Extraction
        for alias, full_name in self.schema.cities.items():
            if alias in normalized or full_name.lower() in normalized:
                entities['city'] = full_name
                break
        
        # Dealer Extraction (Pattern-based)
        dealer_match = DEALER_PATTERN.search(original)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            if not self.schema.is_logistics_keyword(candidate):
                resolved = self.schema.resolve_dealer(candidate)
                if resolved:
                    entities['dealer'] = resolved
        
        # Dealer Extraction (Fallback - Single word check)
        if not entities.get('dealer') and len(normalized.split()) <= 5:
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
            - this week (NEW)
            - last week (NEW)
            - this year (NEW)
            - last month (NEW)
            - current month (NEW)
            - current year (NEW)
        """
        # Today
        if 'today' in normalized:
            return {'start_date': self.today.isoformat(), 'end_date': self.today.isoformat()}
        
        # Yesterday
        if 'yesterday' in normalized:
            yesterday = self.today - timedelta(days=1)
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
                start = self.today - timedelta(days=days)
                return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        
        # This Month / Current Month
        if 'this month' in normalized or 'current month' in normalized:
            start = self.today.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        
        # Last Month
        if 'last month' in normalized:
            last_month = self.today.replace(day=1) - timedelta(days=1)
            start = last_month.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': last_month.isoformat()}
        
        # This Week / Current Week
        if 'this week' in normalized or 'current week' in normalized:
            # Start of week (Monday)
            start = self.today - timedelta(days=self.today.weekday())
            return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        
        # Last Week
        if 'last week' in normalized:
            end = self.today - timedelta(days=self.today.weekday() + 1)
            start = end - timedelta(days=6)
            return {'start_date': start.isoformat(), 'end_date': end.isoformat()}
        
        # This Year / Current Year
        if 'this year' in normalized or 'current year' in normalized:
            start = self.today.replace(month=1, day=1)
            return {'start_date': start.isoformat(), 'end_date': self.today.isoformat()}
        
        return None
    
    def _extract_ranking(self, normalized: str) -> Dict[str, Any]:
        """Extract ranking information from normalized text.
        
        Supports:
            - top, best, highest
            - bottom, worst, lowest
            - top performers, top dealers (NEW)
            - highest sales, lowest sales (NEW)
            - most revenue, least revenue (NEW)
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
        
        Returns:
            float: Confidence score between 0.0 and 1.0
        """
        # Start with intent confidence (capped at 1.0)
        score = min(intent_confidence, 1.0) * 0.3
        
        # Entity presence
        if query_plan.entity:
            score += 0.25
        
        # Metric presence
        if query_plan.metric:
            score += 0.20
        
        # Date range presence
        if query_plan.date_range:
            score += 0.15
        
        # Context usage (only if entities exist)
        if query_plan.from_context and query_plan.entity:
            score += 0.10
        
        # Ensure score doesn't exceed 1.0
        return round(min(score, 1.0), 2)
    
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
    """
    global _ai_query_service
    
    if _ai_query_service is None:
        with _service_lock:
            if _ai_query_service is None:
                try:
                    _ai_query_service = AIQueryService()
                    logger.info("AIQueryService singleton initialized")
                except Exception as e:
                    logger.exception(f"AIQueryService singleton initialization failed: {e}")
                    raise
    
    return _ai_query_service


# ==========================================================
# MODULE INITIALIZATION LOGGING
# ==========================================================

logger.debug("AIQueryService module loaded")
