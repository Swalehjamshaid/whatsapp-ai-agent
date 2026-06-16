# ==========================================================
# FILE: app/services/ai_query_service.py (v3.2 - ENTITY-FIRST ROUTING)
# ==========================================================
# PURPOSE: Intent Detection and Query Planning Engine
# ARCHITECTURE: Entity-first routing with analytics-first priority
# 
# ROUTING PRIORITY:
# 1. DN Lookup → analytics
# 2. Dealer Recognition → analytics
# 3. Warehouse Recognition → analytics
# 4. City Recognition → analytics
# 5. KPI Queries → analytics/kpi
# 6. Executive Insight → analytics + Groq
# 7. Root Cause → analytics + Groq
# 8. General AI → Groq only
# 
# CRITICAL RULE: Groq never receives known business entities
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

try:
    from app.services.ai_provider_service import get_ai_provider_service
    logger.debug("Successfully imported get_ai_provider_service from app.services.ai_provider_service")
except ImportError as e:
    logger.warning(f"Failed to import get_ai_provider_service: {e}")
    logger.warning("Groq AI features will be disabled")


# ==========================================================
# COMPILED REGEX PATTERNS
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
    query_id: str = ""
    processing_time_ms: float = 0.0
    groq_response: Optional[str] = None
    groq_processing_time_ms: float = 0.0
    analytics_data: Optional[Dict[str, Any]] = None  # Pre-fetched analytics data


# ==========================================================
# VALID INTENT AND SERVICE LISTS
# ==========================================================

VALID_INTENTS: Set[str] = {
    # Priority 1: DN Lookup
    'dn_lookup',
    
    # Priority 2: Dealer Intents
    'dealer_dashboard', 'dealer_revenue', 'dealer_units', 
    'dealer_performance', 'dealer_aging',
    
    # Priority 3: Warehouse Intents
    'warehouse_dashboard', 'warehouse_performance',
    
    # Priority 4: City Intents
    'city_dashboard', 'city_performance',
    
    # Priority 5: KPI Intents
    'pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging',
    'top_dealers', 'bottom_dealers', 'top_warehouses',
    'delivery_performance',
    
    # Priority 6: Executive Insight (analytics + Groq)
    'executive_insight', 'control_tower',
    
    # Priority 7: Root Cause (analytics + Groq)
    'root_cause',
    
    # Priority 8: Trend & Comparison
    'trend', 'comparison',
    
    # Priority 9: Help
    'help',
    
    # Priority 10: General AI (Groq only - no analytics)
    'general_ai'
}

VALID_SERVICES: Set[str] = {'analytics', 'kpi', 'groq'}

VALID_ENTITY_TYPES: Set[str] = {'dealer', 'warehouse', 'city', 'dn', None}

# Intents that should fetch analytics data before Groq
ANALYTICS_FIRST_INTENTS: Set[str] = {
    'root_cause', 'executive_insight', 'control_tower'
}

# Intents that use Groq (with or without analytics data)
GROQ_INTENTS: Set[str] = {
    'general_ai', 'root_cause', 'executive_insight', 
    'help', 'control_tower'
}

# Intents that are strictly analytics (no Groq)
STRICTLY_ANALYTICS_INTENTS: Set[str] = {
    'dn_lookup', 'dealer_dashboard', 'dealer_revenue', 'dealer_units',
    'dealer_performance', 'dealer_aging', 'warehouse_dashboard',
    'warehouse_performance', 'city_dashboard', 'city_performance',
    'pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging',
    'top_dealers', 'bottom_dealers', 'top_warehouses',
    'delivery_performance', 'trend', 'comparison'
}


# ==========================================================
# AI QUERY SERVICE CLASS
# ==========================================================

class AIQueryService:
    """
    INTENT DETECTION ENGINE - Entity-First Routing
    
    Routing Priority:
    1. DN Lookup → analytics
    2. Dealer Recognition → analytics
    3. Warehouse Recognition → analytics
    4. City Recognition → analytics
    5. KPI Queries → analytics/kpi
    6. Executive Insight → analytics + Groq
    7. Root Cause → analytics + Groq
    8. General AI → Groq only
    
    CRITICAL RULE: Groq never receives known business entities
    """
    
    def __init__(self):
        """Initialize AIQueryService with schema metadata."""
        start_time = time.time()
        
        try:
            # ==========================================================
            # IMPORT SAFETY - Schema Loading with Diagnostics
            # ==========================================================
            
            logger.info("Loading SchemaService for AIQueryService...")
            self.schema = get_schema_service()
            logger.info("SchemaService loaded successfully")
            
            # ==========================================================
            # GROQ PROVIDER (Optional)
            # ==========================================================
            
            self._ai_provider = None
            self._groq_enabled = False
            
            try:
                self._ai_provider = get_ai_provider_service()
                self._groq_enabled = True
                logger.info("Groq AI provider loaded successfully")
            except Exception as e:
                logger.warning(f"Groq AI provider not available: {e}")
                logger.warning("Groq AI features will be disabled")
            
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
            
            self._initialization_time = time.time()
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"AIQueryService initialized successfully in {init_duration:.2f}ms")
            logger.info(f"Groq AI: {'ENABLED' if self._groq_enabled else 'DISABLED'}")
            logger.info(f"Strictly Analytics Intents: {len(STRICTLY_ANALYTICS_INTENTS)}")
            logger.info(f"Analytics + Groq Intents: {len(ANALYTICS_FIRST_INTENTS)}")
            logger.info(f"Groq Only Intents: {len(GROQ_INTENTS - ANALYTICS_FIRST_INTENTS)}")
            
        except Exception as e:
            logger.exception(f"Failed to initialize AIQueryService: {str(e)}")
            raise RuntimeError(f"AIQueryService initialization failed: {str(e)}") from e
    
    def _validate_schema_service(self):
        """Validate that schema service is fully functional."""
        logger.info("Validating SchemaService...")
        
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
        """Get current date dynamically."""
        return date.today()
    
    async def process_query(self, question: Optional[str], context: Optional[Dict] = None) -> QueryPlan:
        """Process natural language query with entity-first routing."""
        query_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        # ==========================================================
        # INPUT VALIDATION
        # ==========================================================
        
        if question is None:
            logger.warning(f"Query {query_id}: Received None query")
            return self._create_default_plan("No query provided", query_id)
        
        if not isinstance(question, str):
            logger.warning(f"Query {query_id}: Invalid query type: {type(question)}")
            return self._create_default_plan(f"Invalid query type: {type(question)}", query_id)
        
        cleaned_question = question.strip()
        if not cleaned_question:
            logger.warning(f"Query {query_id}: Received empty query")
            return self._create_default_plan("Empty query", query_id)
        
        # ==========================================================
        # QUERY PROCESSING PIPELINE
        # ==========================================================
        
        try:
            logger.info(f"Query {query_id}: Processing: '{cleaned_question[:100]}'")
            
            normalized = self._normalize(cleaned_question)
            
            # ==========================================================
            # STEP 1: DN DETECTION (Highest Priority)
            # ==========================================================
            
            dn_match = DN_PATTERN.search(cleaned_question)
            if dn_match:
                dn_number = dn_match.group(1)
                logger.info(f"Query {query_id}: ✅ DN Detected: {dn_number} → dn_lookup (analytics)")
                
                return QueryPlan(
                    intent="dn_lookup",
                    entity=dn_number,
                    entity_type="dn",
                    service="analytics",
                    requires_groq=False,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=1.0,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 2: ENTITY DETECTION
            # ==========================================================
            
            # Try to resolve as dealer, warehouse, or city
            resolved_entity = self._resolve_entity_with_priority(normalized, cleaned_question)
            
            if resolved_entity:
                entity_type = resolved_entity['type']
                entity_name = resolved_entity['name']
                
                # Determine intent based on entity type and query context
                intent = self._determine_entity_intent(entity_type, normalized, cleaned_question)
                
                logger.info(f"Query {query_id}: ✅ Entity Detected: {entity_type}='{entity_name}' → {intent} (analytics)")
                
                return QueryPlan(
                    intent=intent,
                    entity=entity_name,
                    entity_type=entity_type,
                    service="analytics",
                    requires_groq=False,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.95,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    filters=self._extract_filters(normalized, {entity_type: entity_name})
                )
            
            # ==========================================================
            # STEP 3: KPI QUERY DETECTION
            # ==========================================================
            
            kpi_intent = self._detect_kpi_intent(normalized, cleaned_question)
            if kpi_intent:
                logger.info(f"Query {query_id}: ✅ KPI Detected: {kpi_intent} (analytics/kpi)")
                
                return QueryPlan(
                    intent=kpi_intent,
                    service="kpi" if kpi_intent in ['pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging'] else "analytics",
                    requires_groq=False,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.9,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    filters=self._extract_filters(normalized, {})
                )
            
            # ==========================================================
            # STEP 4: ROOT CAUSE / EXECUTIVE INSIGHT (Analytics + Groq)
            # ==========================================================
            
            root_cause_intent = self._detect_root_cause_intent(normalized)
            if root_cause_intent:
                logger.info(f"Query {query_id}: 🔍 Root Cause Detected: {root_cause_intent} (analytics + Groq)")
                
                # This will fetch analytics data first, then use Groq for explanation
                return QueryPlan(
                    intent=root_cause_intent,
                    service="groq",
                    requires_groq=True,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.85,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            executive_intent = self._detect_executive_intent(normalized)
            if executive_intent:
                logger.info(f"Query {query_id}: 📊 Executive Detected: {executive_intent} (analytics + Groq)")
                
                return QueryPlan(
                    intent=executive_intent,
                    service="groq",
                    requires_groq=True,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.85,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 5: TREND / COMPARISON (Analytics)
            # ==========================================================
            
            trend_intent = self._detect_trend_intent(normalized)
            if trend_intent:
                logger.info(f"Query {query_id}: 📈 Trend Detected: {trend_intent} (analytics)")
                
                return QueryPlan(
                    intent=trend_intent,
                    service="analytics",
                    requires_groq=False,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.85,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 6: HELP (Groq - Last Priority Before General AI)
            # ==========================================================
            
            if self._is_help_query(normalized):
                logger.info(f"Query {query_id}: ❓ Help Detected (Groq)")
                
                return QueryPlan(
                    intent="help",
                    service="groq",
                    requires_groq=True,
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    confidence_score=0.9,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    groq_response=self._get_fallback_groq_response("help", cleaned_question)
                )
            
            # ==========================================================
            # STEP 7: GENERAL AI (Groq Only - No Analytics)
            # ==========================================================
            
            logger.info(f"Query {query_id}: 🤖 General AI Detected (Groq only)")
            
            return QueryPlan(
                intent="general_ai",
                service="groq",
                requires_groq=True,
                original_message=cleaned_question,
                normalized_message=normalized,
                query_id=query_id,
                confidence_score=0.5,
                processing_time_ms=(time.time() - start_time) * 1000
            )
            
        except Exception as e:
            logger.exception(f"Query {query_id}: Processing failed: {str(e)}")
            return self._create_default_plan(f"Processing error: {str(e)}", query_id)
    
    # ==========================================================
    # ENTITY RESOLUTION (Priority-based)
    # ==========================================================
    
    def _resolve_entity_with_priority(self, normalized: str, original: str) -> Optional[Dict[str, Any]]:
        """
        Resolve entity with priority: Dealer > Warehouse > City
        
        Returns:
            Dict with 'type' and 'name' or None
        """
        # Priority 1: Try dealer resolution
        dealer = self._resolve_entity(normalized, original, "dealer")
        if dealer:
            return {"type": "dealer", "name": dealer}
        
        # Priority 2: Try warehouse resolution
        warehouse = self._resolve_entity(normalized, original, "warehouse")
        if warehouse:
            return {"type": "warehouse", "name": warehouse}
        
        # Priority 3: Try city resolution
        city = self._resolve_entity(normalized, original, "city")
        if city:
            return {"type": "city", "name": city}
        
        return None
    
    def _resolve_entity(self, normalized: str, original: str, entity_type: str) -> Optional[str]:
        """Resolve entity from text using schema service."""
        if entity_type == "dealer":
            # Try pattern-based extraction
            dealer_match = DEALER_PATTERN.search(original)
            if dealer_match:
                candidate = dealer_match.group(1).strip()
                if candidate and candidate not in self._logistics_keywords_cache:
                    resolved = self.schema.resolve_dealer(candidate)
                    if resolved:
                        return resolved
            
            # Try resolving the entire text
            resolved = self.schema.resolve_dealer(original)
            if resolved:
                return resolved
            
            resolved = self.schema.resolve_dealer(normalized)
            if resolved:
                return resolved
            
            # Try resolving the first few words
            words = normalized.split()
            if len(words) > 1:
                for i in range(1, min(len(words), 4)):
                    candidate = ' '.join(words[:i])
                    resolved = self.schema.resolve_dealer(candidate)
                    if resolved:
                        return resolved
            
            return None
        
        elif entity_type == "warehouse":
            resolved = self.schema.resolve_warehouse(original)
            if resolved:
                return resolved
            
            resolved = self.schema.resolve_warehouse(normalized)
            if resolved:
                return resolved
            
            words = normalized.split()
            for word in words:
                if len(word) >= 2:
                    resolved = self.schema.resolve_warehouse(word)
                    if resolved:
                        return resolved
            
            return None
        
        elif entity_type == "city":
            resolved = self.schema.resolve_city(original)
            if resolved:
                return resolved
            
            resolved = self.schema.resolve_city(normalized)
            if resolved:
                return resolved
            
            words = normalized.split()
            for word in words:
                if len(word) >= 2:
                    resolved = self.schema.resolve_city(word)
                    if resolved:
                        return resolved
            
            return None
        
        return None
    
    def _determine_entity_intent(self, entity_type: str, normalized: str, original: str) -> str:
        """Determine intent based on entity type and query context."""
        # Check for metric keywords
        metric = self.schema.detect_metric(normalized)
        
        if entity_type == "dealer":
            if metric == "revenue":
                return "dealer_revenue"
            elif metric == "units":
                return "dealer_units"
            elif 'performance' in normalized or 'kpi' in normalized:
                return "dealer_performance"
            elif 'aging' in normalized or 'delay' in normalized:
                return "dealer_aging"
            else:
                return "dealer_dashboard"
        
        elif entity_type == "warehouse":
            if 'performance' in normalized or 'kpi' in normalized:
                return "warehouse_performance"
            else:
                return "warehouse_dashboard"
        
        elif entity_type == "city":
            if 'performance' in normalized or 'kpi' in normalized:
                return "city_performance"
            else:
                return "city_dashboard"
        
        return "general_ai"
    
    # ==========================================================
    # KPI DETECTION
    # ==========================================================
    
    def _detect_kpi_intent(self, normalized: str, original: str) -> Optional[str]:
        """Detect KPI intent from query."""
        kpi_patterns = {
            'pending_pgi': ['pending pgi', 'pgi pending', 'open pgi', 'pgi not done'],
            'pending_pod': ['pending pod', 'pod pending', 'open pod', 'pod not done'],
            'pgi_aging': ['pgi aging', 'aging pgi', 'pgi delay'],
            'pod_aging': ['pod aging', 'aging pod', 'pod delay'],
            'top_dealers': ['top dealer', 'best dealer', 'top performing', 'top 10 dealers'],
            'bottom_dealers': ['bottom dealer', 'worst dealer', 'poor performing', 'bottom 10 dealers'],
            'top_warehouses': ['top warehouse', 'best warehouse'],
            'delivery_performance': ['delivery performance', 'delivery kpi', 'delivery metrics']
        }
        
        for intent, patterns in kpi_patterns.items():
            for pattern in patterns:
                if pattern in normalized:
                    return intent
        
        return None
    
    def _detect_root_cause_intent(self, normalized: str) -> Optional[str]:
        """Detect root cause intent."""
        patterns = [
            'root cause', 'why', 'reason', 'cause', 'what caused',
            'why delayed', 'why aging', 'why pending', 'why not delivered'
        ]
        
        for pattern in patterns:
            if pattern in normalized:
                return "root_cause"
        
        return None
    
    def _detect_executive_intent(self, normalized: str) -> Optional[str]:
        """Detect executive insight intent."""
        patterns = [
            'executive insight', 'executive summary', 'key issue',
            'critical alert', 'bottleneck', 'control tower'
        ]
        
        for pattern in patterns:
            if pattern in normalized:
                return "executive_insight"
        
        return None
    
    def _detect_trend_intent(self, normalized: str) -> Optional[str]:
        """Detect trend intent."""
        patterns = [
            'trend', 'month over month', 'trends', 'over time',
            'historical', 'performance trend', 'delivery trend'
        ]
        
        for pattern in patterns:
            if pattern in normalized:
                return "trend"
        
        return None
    
    def _is_help_query(self, normalized: str) -> bool:
        """Check if query is a help request."""
        patterns = ['help', 'menu', 'commands', 'what can you do', 'available commands']
        return any(pattern in normalized for pattern in patterns)
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _normalize(self, text: str) -> str:
        """Normalize text for processing."""
        if not text:
            return ""
        
        normalized = text.lower()
        normalized = WHITESPACE_PATTERN.sub(' ', normalized)
        normalized = SPECIAL_CHARS_PATTERN.sub('', normalized)
        return normalized.strip()
    
    def _extract_filters(self, normalized: str, entities: Dict[str, Any]) -> Dict[str, Any]:
        """Extract filters from query."""
        filters = {}
        
        if entities.get('city'):
            filters['city'] = entities['city']
        
        if entities.get('warehouse'):
            filters['warehouse'] = entities['warehouse']
        
        if entities.get('dealer'):
            filters['dealer'] = entities['dealer']
        
        if 'pending' in normalized:
            filters['status'] = 'pending'
        elif 'delivered' in normalized:
            filters['status'] = 'delivered'
        elif 'in transit' in normalized or 'transit' in normalized:
            filters['status'] = 'in_transit'
        
        return filters
    
    def _get_fallback_groq_response(self, intent: str, question: str) -> str:
        """Get fallback response when Groq is unavailable."""
        fallbacks = {
            "general_ai": "I'm here to help with your logistics queries. Please ask about dealers, warehouses, cities, or delivery notes.",
            "root_cause": "To analyze root causes, I need specific data about the issue. Please provide more details about the problem.",
            "executive_insight": "Executive insights require data analysis. Please specify which metrics or KPIs you're interested in.",
            "help": """📋 *Available Commands*

• *Track DN* - Send any 8-12 digit number
• *Dealer Performance* - "Show dealer [name]"
• *Warehouse Status* - "[Warehouse name]"
• *City Dashboard* - "[City name]"
• *Pending PODs* - "Pending POD"
• *KPI Dashboard* - "Show me KPIs"
• *Control Tower* - "Control tower"
• *Root Cause* - "Why is this happening?"

Need help? Just ask!""",
            "control_tower": "Control tower insights require real-time data. Please specify which metrics or alerts you want to monitor."
        }
        return fallbacks.get(intent, "I'm here to help with your logistics queries. Please ask a specific question.")
    
    def _create_default_plan(self, reason: str, query_id: str = None) -> QueryPlan:
        """Create a default query plan for error/empty cases."""
        return QueryPlan(
            intent="general_ai",
            confidence_score=0.1,
            requires_groq=True,
            service="groq",
            original_message=reason,
            normalized_message=reason,
            query_id=query_id or str(uuid.uuid4())[:8],
            groq_response=self._get_fallback_groq_response("general_ai", reason)
        )
    
    # ==========================================================
    # LEGACY METHODS (Maintained for Backward Compatibility)
    # ==========================================================
    
    def detect_intent(self, normalized: str, original: str) -> Tuple[str, float]:
        """Legacy method - kept for backward compatibility."""
        # Check DN first
        if DN_PATTERN.search(original):
            return "dn_lookup", 1.0
        
        # Try entity resolution
        resolved = self._resolve_entity_with_priority(normalized, original)
        if resolved:
            return "dealer_dashboard" if resolved['type'] == "dealer" else \
                   "warehouse_dashboard" if resolved['type'] == "warehouse" else \
                   "city_dashboard", 0.95
        
        # Use schema service
        intent, confidence = self.schema.detect_intent(normalized)
        if intent:
            return intent, confidence
        
        return "general_ai", 0.3
    
    def extract_entities(self, normalized: str, original: str, intent: str, context: Optional[Dict]) -> Dict[str, Any]:
        """Legacy method - kept for backward compatibility."""
        entities = {}
        
        dn_match = DN_PATTERN.search(original)
        if dn_match:
            entities['dn_number'] = dn_match.group(1)
            return entities
        
        resolved = self._resolve_entity_with_priority(normalized, original)
        if resolved:
            entities[resolved['type']] = resolved['name']
            return entities
        
        if context and isinstance(context, dict):
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


# ==========================================================
# THREAD-SAFE SINGLETON
# ==========================================================

_ai_query_service = None
_service_lock = threading.Lock()


def get_ai_query_service() -> AIQueryService:
    """Thread-safe singleton getter for AIQueryService."""
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

logger.debug("AIQueryService v3.2 module loaded - Entity-First Routing")
logger.debug(f"Valid intents: {len(VALID_INTENTS)}")
logger.debug(f"Strictly Analytics Intents: {len(STRICTLY_ANALYTICS_INTENTS)}")
logger.debug(f"Analytics + Groq Intents: {len(ANALYTICS_FIRST_INTENTS)}")
logger.debug(f"Groq Only Intents: {len(GROQ_INTENTS - ANALYTICS_FIRST_INTENTS)}")
logger.debug("=" * 60)
logger.debug("ROUTING PRIORITY:")
logger.debug("  1. DN Lookup → analytics")
logger.debug("  2. Dealer Recognition → analytics")
logger.debug("  3. Warehouse Recognition → analytics")
logger.debug("  4. City Recognition → analytics")
logger.debug("  5. KPI Queries → analytics/kpi")
logger.debug("  6. Executive Insight → analytics + Groq")
logger.debug("  7. Root Cause → analytics + Groq")
logger.debug("  8. General AI → Groq only")
logger.debug("=" * 60)
