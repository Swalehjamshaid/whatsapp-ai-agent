# ==========================================================
# FILE: app/services/ai_query_service.py (v5.0 - PRODUCTION FIX)
# ==========================================================
# PURPOSE: Enterprise Routing Controller - Detect, Classify, Route
# ARCHITECTURE: Brain that decides, never executes
#
# FIXES APPLIED:
# ✅ Enhanced dealer detection with partial matching
# ✅ Dealer names now properly recognized
# ✅ Pure dealer names route to dealer_dashboard
# ✅ Better handling of dealer name variations
# ✅ Improved logging for debugging
# ==========================================================

import re
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple, Set
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
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
# COMPILED REGEX PATTERNS
# ==========================================================

# DN Number Pattern (8-12 digits)
DN_PATTERN = re.compile(r'\b(\d{8,12})\b')

# Dealer Extraction Pattern - ENHANCED
DEALER_PATTERN = re.compile(r'(?:dealer|show|display|get|view|tell me about)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)

# Ranking Limit Pattern
RANKING_LIMIT_PATTERN = re.compile(r'(?:top|bottom)\s+(\d+)', re.IGNORECASE)

# Whitespace Normalization Pattern
WHITESPACE_PATTERN = re.compile(r'\s+')

# Special Characters Pattern (for normalization)
SPECIAL_CHARS_PATTERN = re.compile(r'[^\w\s\-&.]')

# Word boundary pattern for entity extraction
WORD_BOUNDARY_PATTERN = re.compile(r'\b')


# ==========================================================
# ENUMS
# ==========================================================

class QueryCategory(Enum):
    """Categories of queries for routing"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    KPI = "kpi"
    RANKING = "ranking"
    EXECUTIVE = "executive"
    CONTROL_TOWER = "control_tower"
    DATA_QUALITY = "data_quality"
    CONTEXT = "context"
    HELP = "help"
    GENERAL = "general"


class ServiceTarget(Enum):
    """Target services for routing"""
    ANALYTICS = "analytics"
    KPI = "kpi"
    GROQ = "groq"


# ==========================================================
# QUERY PLAN DATA CLASS - ENHANCED VERSION
# ==========================================================

@dataclass
class QueryPlan:
    """
    Routing decision output - ENHANCED with full backward compatibility.
    
    ALL parameters have defaults to prevent initialization errors.
    Supports both named and positional initialization.
    Handles legacy attributes gracefully.
    """
    
    # Core required fields (with defaults)
    intent: str = "general_ai"
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    service: str = "groq"
    confidence: float = 0.0
    needs_groq: bool = True
    query_category: str = "general"
    reason: str = ""
    original_message: str = ""
    
    # Extended fields (with defaults)
    normalized_message: str = ""
    query_id: str = ""
    processing_time_ms: float = 0.0
    filters: Dict[str, Any] = field(default_factory=dict)
    date_range: Optional[Dict[str, str]] = None
    ranking_type: Optional[str] = None
    limit: int = 10
    sort_by: Optional[str] = None
    from_context: bool = False
    
    # Legacy/Backward compatibility fields
    entity2: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Post-initialization to ensure all fields are properly set."""
        if self.entity2 is None and self.filters.get('entity2'):
            self.entity2 = self.filters.get('entity2')
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "intent": self.intent,
            "entity": self.entity,
            "entity_type": self.entity_type,
            "service": self.service,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "query_category": self.query_category,
            "reason": self.reason,
            "original_message": self.original_message,
            "normalized_message": self.normalized_message,
            "query_id": self.query_id,
            "processing_time_ms": self.processing_time_ms,
            "filters": self.filters,
            "date_range": self.date_range,
            "ranking_type": self.ranking_type,
            "limit": self.limit,
            "sort_by": self.sort_by,
            "from_context": self.from_context,
            "entity2": self.entity2,
            "context": self.context
        }
    
    def __repr__(self) -> str:
        return (f"QueryPlan(intent={self.intent}, entity={self.entity}, "
                f"service={self.service}, confidence={self.confidence:.2f})")
    
    def __getattr__(self, name: str) -> Any:
        """Handle missing attributes gracefully for backward compatibility."""
        legacy_attrs = {
            'entity2': None,
            'limit': 10,
            'sort_by': 'revenue',
            'context': {},
            'ranking_limit': 10,
            'sort_order': 'desc',
            'aggregation': None,
            'group_by': None
        }
        
        if name in legacy_attrs:
            return legacy_attrs[name]
        
        if hasattr(self, name):
            return getattr(self, name)
        
        logger.warning(f"QueryPlan: Attribute '{name}' not found - returning None")
        return None


# ==========================================================
# VALID INTENT AND SERVICE LISTS
# ==========================================================

VALID_INTENTS: Set[str] = {
    'dn_lookup',
    'dealer_dashboard', 'dealer_revenue', 'dealer_units', 
    'dealer_performance', 'dealer_aging', 'dealer_dns',
    'warehouse_dashboard', 'warehouse_performance',
    'city_dashboard', 'city_performance',
    'pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging',
    'delivery_aging',
    'top_dealers_revenue', 'top_dealers_units', 'top_warehouses_pending',
    'bottom_dealers', 'top_dealers',
    'executive_insight', 'control_tower',
    'data_quality_analysis',
    'trend', 'comparison',
    'help', 'general_ai'
}

VALID_SERVICES: Set[str] = {'analytics', 'kpi', 'groq'}

VALID_ENTITY_TYPES: Set[str] = {'dealer', 'warehouse', 'city', 'dn', None}

CATEGORY_TO_SERVICE: Dict[str, str] = {
    'dn': 'analytics',
    'dealer': 'analytics',
    'warehouse': 'analytics',
    'city': 'analytics',
    'kpi': 'kpi',
    'ranking': 'analytics',
    'executive': 'groq',
    'control_tower': 'groq',
    'data_quality': 'analytics',
    'context': 'analytics',
    'help': 'groq',
    'general': 'groq'
}

GROQ_INTENTS: Set[str] = {
    'general_ai', 'root_cause', 'executive_insight', 
    'help', 'control_tower', 'trend', 'comparison'
}

STRICTLY_ANALYTICS_INTENTS: Set[str] = {
    'dn_lookup', 'dealer_dashboard', 'dealer_revenue', 'dealer_units',
    'dealer_performance', 'dealer_aging', 'dealer_dns',
    'warehouse_dashboard', 'warehouse_performance', 
    'city_dashboard', 'city_performance',
    'pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging',
    'delivery_aging', 'top_dealers_revenue', 'top_dealers_units',
    'top_warehouses_pending', 'bottom_dealers', 'top_dealers',
    'data_quality_analysis'
}


# ==========================================================
# AI QUERY SERVICE - ENTERPRISE ROUTING CONTROLLER
# ==========================================================

class AIQueryService:
    """
    ENTERPRISE ROUTING CONTROLLER - Brain that decides, never executes
    
    This service is the central routing engine that:
    1. Detects intent from natural language
    2. Resolves entities (dealers, warehouses, cities, DNs)
    3. Classifies query category
    4. Determines target service (analytics, kpi, groq)
    5. Generates immutable QueryPlan
    """
    
    def __init__(self):
        """Initialize AIQueryService with schema metadata."""
        start_time = time.time()
        
        try:
            logger.info("Loading SchemaService for AIQueryService...")
            self.schema = get_schema_service()
            logger.info("SchemaService loaded successfully")
            
            self._validate_schema_service()
            
            if hasattr(self.schema, 'validate_metadata'):
                report = self.schema.validate_metadata()
                logger.debug(f"Schema metadata: {report.get('counts', {})}")
            
            self._warehouse_cache = {
                'aliases': list(self.schema.warehouses.keys()),
                'names': list(set(self.schema.warehouses.values())),
                'name_lower': [name.lower() for name in set(self.schema.warehouses.values())]
            }
            logger.debug(f"Cached {len(self._warehouse_cache['aliases'])} warehouse aliases")
            
            self._city_cache = {
                'aliases': list(self.schema.cities.keys()),
                'names': list(set(self.schema.cities.values())),
                'name_lower': [name.lower() for name in set(self.schema.cities.values())]
            }
            logger.debug(f"Cached {len(self._city_cache['aliases'])} city aliases")
            
            self._logistics_keywords_cache = self.schema.logistics_keywords
            logger.debug(f"Cached {len(self._logistics_keywords_cache)} logistics keywords")
            
            self._last_context = {
                'dn': None,
                'dealer': None,
                'warehouse': None,
                'city': None,
                'intent': None
            }
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"AIQueryService initialized successfully in {init_duration:.2f}ms")
            logger.info("=" * 60)
            logger.info("ROUTING CAPABILITIES:")
            logger.info("  1. DN Lookup → analytics")
            logger.info("  2. Dealer (dashboard/revenue/units/aging/performance/dns) → analytics")
            logger.info("  3. Warehouse (dashboard/performance) → analytics")
            logger.info("  4. City (dashboard/performance) → analytics")
            logger.info("  5. KPI (pending_pgi/pending_pod/pgi_aging/pod_aging) → kpi")
            logger.info("  6. Ranking → analytics")
            logger.info("  7. Executive/Control Tower → analytics + Groq")
            logger.info("  8. Data Quality → analytics")
            logger.info("  9. Context Resolution → analytics")
            logger.info(" 10. Help → Groq")
            logger.info(" 11. General AI → Groq")
            logger.info("=" * 60)
            
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
        return date.today()
    
    # ==========================================================
    # MAIN PROCESSING METHOD - ENHANCED DEALER DETECTION
    # ==========================================================
    
    async def process_query(self, question: Optional[str], context: Optional[Dict] = None) -> QueryPlan:
        """
        Process natural language query and generate routing plan.
        
        FIX: Enhanced dealer detection with multiple resolution attempts.
        """
        query_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
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
        
        try:
            logger.info(f"Query {query_id}: Processing: '{cleaned_question[:100]}'")
            
            normalized = self._normalize(cleaned_question)
            
            # ==========================================================
            # STEP 1: DN DETECTION (Highest Priority)
            # ==========================================================
            
            dn_match = DN_PATTERN.search(cleaned_question)
            if dn_match:
                dn_number = dn_match.group(1)
                self._last_context['dn'] = dn_number
                self._last_context['intent'] = 'dn_lookup'
                
                logger.info(f"Query {query_id}: ✅ DN Detected: {dn_number} → dn_lookup (analytics)")
                
                return QueryPlan(
                    intent="dn_lookup",
                    entity=dn_number,
                    entity_type="dn",
                    service="analytics",
                    confidence=1.0,
                    needs_groq=False,
                    query_category="dn",
                    reason="DN number detected",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 2: DEALER DETECTION (ENHANCED - HIGH PRIORITY)
            # ==========================================================
            
            dealer_result = self._detect_dealer_enhanced(cleaned_question, normalized, context)
            if dealer_result:
                dealer_name = dealer_result
                self._last_context['dealer'] = dealer_name
                self._last_context['intent'] = 'dealer_dashboard'
                
                logger.info(f"Query {query_id}: ✅ Dealer Detected: '{dealer_name}' → dealer_dashboard (analytics)")
                
                return QueryPlan(
                    intent="dealer_dashboard",
                    entity=dealer_name,
                    entity_type="dealer",
                    service="analytics",
                    confidence=0.95,
                    needs_groq=False,
                    query_category="dealer",
                    reason=f"Dealer resolved: {dealer_name}",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 3: DATA QUALITY DETECTION
            # ==========================================================
            
            if self._is_data_quality_query(normalized):
                logger.info(f"Query {query_id}: 📊 Data Quality Detected → data_quality_analysis (analytics)")
                
                return QueryPlan(
                    intent="data_quality_analysis",
                    entity=None,
                    entity_type=None,
                    service="analytics",
                    confidence=0.9,
                    needs_groq=False,
                    query_category="data_quality",
                    reason="Data quality/validation query",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 4: WAREHOUSE DETECTION
            # ==========================================================
            
            warehouse_result = self._detect_warehouse_enhanced(cleaned_question, normalized)
            if warehouse_result:
                warehouse_name = warehouse_result
                self._last_context['warehouse'] = warehouse_name
                self._last_context['intent'] = 'warehouse_dashboard'
                
                logger.info(f"Query {query_id}: ✅ Warehouse Detected: '{warehouse_name}' → warehouse_dashboard (analytics)")
                
                return QueryPlan(
                    intent="warehouse_dashboard",
                    entity=warehouse_name,
                    entity_type="warehouse",
                    service="analytics",
                    confidence=0.95,
                    needs_groq=False,
                    query_category="warehouse",
                    reason=f"Warehouse resolved: {warehouse_name}",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 5: CITY DETECTION
            # ==========================================================
            
            city_result = self._detect_city_enhanced(cleaned_question, normalized)
            if city_result:
                city_name = city_result
                self._last_context['city'] = city_name
                self._last_context['intent'] = 'city_dashboard'
                
                logger.info(f"Query {query_id}: ✅ City Detected: '{city_name}' → city_dashboard (analytics)")
                
                return QueryPlan(
                    intent="city_dashboard",
                    entity=city_name,
                    entity_type="city",
                    service="analytics",
                    confidence=0.95,
                    needs_groq=False,
                    query_category="city",
                    reason=f"City resolved: {city_name}",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 6: KPI DETECTION
            # ==========================================================
            
            kpi_intent = self._detect_kpi_intent(normalized)
            if kpi_intent:
                self._last_context['intent'] = kpi_intent
                
                logger.info(f"Query {query_id}: ✅ KPI Detected: {kpi_intent} → kpi")
                
                date_range = self._extract_date_range(normalized)
                
                return QueryPlan(
                    intent=kpi_intent,
                    entity=None,
                    entity_type=None,
                    service="kpi",
                    confidence=0.9,
                    needs_groq=False,
                    query_category="kpi",
                    reason="KPI query",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    date_range=date_range
                )
            
            # ==========================================================
            # STEP 7: RANKING DETECTION
            # ==========================================================
            
            ranking_intent = self._detect_ranking_intent(normalized)
            if ranking_intent:
                self._last_context['intent'] = ranking_intent
                
                logger.info(f"Query {query_id}: 📊 Ranking Detected: {ranking_intent} (analytics)")
                
                limit = 10
                limit_match = RANKING_LIMIT_PATTERN.search(normalized)
                if limit_match:
                    limit = int(limit_match.group(1))
                
                ranking_type = "top" if "top" in normalized else "bottom"
                
                return QueryPlan(
                    intent=ranking_intent,
                    entity=None,
                    entity_type=None,
                    service="analytics",
                    confidence=0.9,
                    needs_groq=False,
                    query_category="ranking",
                    reason="Ranking query",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    limit=limit,
                    ranking_type=ranking_type
                )
            
            # ==========================================================
            # STEP 8: EXECUTIVE / CONTROL TOWER DETECTION
            # ==========================================================
            
            executive_intent = self._detect_executive_intent(normalized)
            if executive_intent:
                self._last_context['intent'] = executive_intent
                
                logger.info(f"Query {query_id}: 📋 Executive Detected: {executive_intent} (analytics + Groq)")
                
                return QueryPlan(
                    intent=executive_intent,
                    entity=None,
                    entity_type=None,
                    service="groq",
                    confidence=0.85,
                    needs_groq=True,
                    query_category="executive",
                    reason="Executive/control tower query",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 9: CONTEXT RESOLUTION
            # ==========================================================
            
            context_intent = self._resolve_context_query(normalized, cleaned_question, context)
            if context_intent:
                logger.info(f"Query {query_id}: 🔄 Context Resolved: {context_intent} (analytics)")
                
                entity_name = None
                entity_type = None
                
                if 'status' in normalized or 'details' in normalized:
                    if context and context.get('last_dn'):
                        entity_name = context['last_dn']
                        entity_type = 'dn'
                    elif context and context.get('last_dealer'):
                        entity_name = context['last_dealer']
                        entity_type = 'dealer'
                
                return QueryPlan(
                    intent=context_intent,
                    entity=entity_name,
                    entity_type=entity_type,
                    service="analytics" if entity_type != 'dn' else "analytics",
                    confidence=0.85 if entity_name else 0.5,
                    needs_groq=False,
                    query_category="context",
                    reason="Context resolution",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000,
                    from_context=bool(entity_name)
                )
            
            # ==========================================================
            # STEP 10: HELP DETECTION
            # ==========================================================
            
            if self._is_help_query(normalized):
                logger.info(f"Query {query_id}: ❓ Help Detected → help (Groq)")
                
                return QueryPlan(
                    intent="help",
                    entity=None,
                    entity_type=None,
                    service="groq",
                    confidence=0.9,
                    needs_groq=True,
                    query_category="help",
                    reason="Help request",
                    original_message=cleaned_question,
                    normalized_message=normalized,
                    query_id=query_id,
                    processing_time_ms=(time.time() - start_time) * 1000
                )
            
            # ==========================================================
            # STEP 11: GENERAL AI (Groq Only - No Analytics)
            # ==========================================================
            
            logger.info(f"Query {query_id}: 🤖 General AI Detected → general_ai (Groq)")
            
            return QueryPlan(
                intent="general_ai",
                entity=None,
                entity_type=None,
                service="groq",
                confidence=0.5,
                needs_groq=True,
                query_category="general",
                reason="General conversation",
                original_message=cleaned_question,
                normalized_message=normalized,
                query_id=query_id,
                processing_time_ms=(time.time() - start_time) * 1000
            )
            
        except Exception as e:
            logger.exception(f"Query {query_id}: Processing failed: {str(e)}")
            return self._create_default_plan(f"Processing error: {str(e)}", query_id)
    
    # ==========================================================
    # ENHANCED DEALER DETECTION - FIXES "Rafi Electronics Oghi"
    # ==========================================================
    
    def _detect_dealer_enhanced(self, original: str, normalized: str, context: Optional[Dict]) -> Optional[str]:
        """
        ENHANCED dealer detection with multiple strategies.
        
        Fixes: "Rafi Electronics Oghi" → dealer_dashboard
        """
        logger.debug(f"Enhanced dealer detection for: '{original}'")
        
        # Strategy 1: Direct resolution from SchemaService
        dealer = self.schema.resolve_dealer(original)
        if dealer:
            logger.debug(f"✅ Dealer found via direct resolution: {dealer}")
            return dealer
        
        dealer = self.schema.resolve_dealer(normalized)
        if dealer:
            logger.debug(f"✅ Dealer found via normalized resolution: {dealer}")
            return dealer
        
        # Strategy 2: Pattern-based extraction
        dealer_match = DEALER_PATTERN.search(original)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            logger.debug(f"Pattern extracted: '{candidate}'")
            
            # Try resolving the extracted candidate
            resolved = self.schema.resolve_dealer(candidate)
            if resolved:
                logger.debug(f"✅ Dealer resolved from pattern: {resolved}")
                return resolved
            
            # Try with common variations
            for variation in self._generate_name_variations(candidate):
                resolved = self.schema.resolve_dealer(variation)
                if resolved:
                    logger.debug(f"✅ Dealer resolved from variation '{variation}': {resolved}")
                    return resolved
        
        # Strategy 3: Partial word matching
        words = normalized.split()
        for word in words:
            if len(word) >= 3:  # Only meaningful words
                resolved = self.schema.resolve_dealer(word)
                if resolved:
                    logger.debug(f"✅ Dealer found via partial word '{word}': {resolved}")
                    return resolved
        
        # Strategy 4: Try combinations of words
        if len(words) >= 2:
            for i in range(len(words) - 1):
                for j in range(i + 1, min(i + 4, len(words) + 1)):
                    candidate = ' '.join(words[i:j])
                    if len(candidate) >= 4:
                        resolved = self.schema.resolve_dealer(candidate)
                        if resolved:
                            logger.debug(f"✅ Dealer found via word combination '{candidate}': {resolved}")
                            return resolved
        
        # Strategy 5: Context-based detection
        if context and context.get('last_dealer'):
            # Check if this is a follow-up query
            follow_up_keywords = ['revenue', 'units', 'performance', 'aging', 'pending', 'pod', 'pgi']
            if any(kw in normalized for kw in follow_up_keywords):
                logger.debug(f"✅ Dealer from context: {context['last_dealer']}")
                return context['last_dealer']
        
        logger.debug("❌ No dealer detected")
        return None
    
    def _generate_name_variations(self, name: str) -> List[str]:
        """Generate variations of a name for better matching."""
        variations = []
        parts = name.split()
        
        if len(parts) >= 2:
            # First word + last word
            variations.append(f"{parts[0]} {parts[-1]}")
            # All words except first
            variations.append(' '.join(parts[1:]))
            # All words except last
            variations.append(' '.join(parts[:-1]))
            # First two words
            if len(parts) >= 2:
                variations.append(' '.join(parts[:2]))
            # Last two words
            if len(parts) >= 2:
                variations.append(' '.join(parts[-2:]))
        
        return variations
    
    # ==========================================================
    # ENHANCED WAREHOUSE DETECTION
    # ==========================================================
    
    def _detect_warehouse_enhanced(self, original: str, normalized: str) -> Optional[str]:
        """Enhanced warehouse detection."""
        logger.debug(f"Enhanced warehouse detection for: '{original}'")
        
        # Direct resolution
        warehouse = self.schema.resolve_warehouse(original)
        if warehouse:
            return warehouse
        
        warehouse = self.schema.resolve_warehouse(normalized)
        if warehouse:
            return warehouse
        
        # Check each word
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_warehouse(word)
                if resolved:
                    return resolved
        
        return None
    
    # ==========================================================
    # ENHANCED CITY DETECTION
    # ==========================================================
    
    def _detect_city_enhanced(self, original: str, normalized: str) -> Optional[str]:
        """Enhanced city detection."""
        logger.debug(f"Enhanced city detection for: '{original}'")
        
        # Direct resolution
        city = self.schema.resolve_city(original)
        if city:
            return city
        
        city = self.schema.resolve_city(normalized)
        if city:
            return city
        
        # Check each word
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_city(word)
                if resolved:
                    return resolved
        
        return None
    
    # ==========================================================
    # LEGACY METHODS (Preserved for compatibility)
    # ==========================================================
    
    def _resolve_entity_with_priority(self, normalized: str, original: str) -> Optional[Dict[str, Any]]:
        """Legacy entity resolution - preserved for compatibility."""
        dealer = self._resolve_dealer(normalized, original)
        if dealer:
            return {"type": "dealer", "name": dealer}
        
        warehouse = self._resolve_warehouse(normalized, original)
        if warehouse:
            return {"type": "warehouse", "name": warehouse}
        
        city = self._resolve_city(normalized, original)
        if city:
            return {"type": "city", "name": city}
        
        return None
    
    def _resolve_dealer(self, normalized: str, original: str) -> Optional[str]:
        """Legacy dealer resolution - preserved for compatibility."""
        return self._detect_dealer_enhanced(original, normalized, None)
    
    def _resolve_warehouse(self, normalized: str, original: str) -> Optional[str]:
        """Legacy warehouse resolution."""
        return self._detect_warehouse_enhanced(original, normalized)
    
    def _resolve_city(self, normalized: str, original: str) -> Optional[str]:
        """Legacy city resolution."""
        return self._detect_city_enhanced(original, normalized)
    
    def _determine_entity_intent(self, entity_type: str, normalized: str, original: str) -> str:
        """Determine intent based on entity type."""
        metric = self.schema.detect_metric(normalized)
        
        if entity_type == "dealer":
            if 'dns' in normalized or 'orders' in normalized or 'delivery notes' in normalized:
                return "dealer_dns"
            elif metric == "revenue" or 'revenue' in normalized or 'sales' in normalized:
                return "dealer_revenue"
            elif metric == "units" or 'units' in normalized or 'quantity' in normalized:
                return "dealer_units"
            elif 'performance' in normalized or 'kpi' in normalized:
                return "dealer_performance"
            elif 'aging' in normalized or 'delay' in normalized or 'pending' in normalized:
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
    
    def _detect_kpi_intent(self, normalized: str) -> Optional[str]:
        """Detect KPI intent from query."""
        kpi_patterns = {
            'pending_pgi': ['pending pgi', 'pgi pending', 'open pgi', 'pgi not done'],
            'pending_pod': ['pending pod', 'pod pending', 'open pod', 'pod not done'],
            'pgi_aging': ['pgi aging', 'aging pgi', 'pgi delay', 'pgi overdue'],
            'pod_aging': ['pod aging', 'aging pod', 'pod delay', 'pod overdue'],
            'delivery_aging': ['delivery aging', 'aging delivery', 'delivery delay']
        }
        
        for intent, patterns in kpi_patterns.items():
            for pattern in patterns:
                if pattern in normalized:
                    return intent
        
        return None
    
    def _detect_ranking_intent(self, normalized: str) -> Optional[str]:
        """Detect ranking intent from query."""
        if any(kw in normalized for kw in ['top dealer', 'top dealers', 'highest revenue', 'most revenue', 'top revenue']):
            if 'unit' in normalized:
                return "top_dealers_units"
            return "top_dealers_revenue"
        
        if any(kw in normalized for kw in ['top warehouse', 'worst warehouse', 'most pending', 'highest pending']):
            return "top_warehouses_pending"
        
        if any(kw in normalized for kw in ['bottom dealer', 'worst dealer', 'lowest']):
            return "bottom_dealers"
        
        return None
    
    def _detect_executive_intent(self, normalized: str) -> Optional[str]:
        """Detect executive/control tower intent."""
        executive_patterns = [
            'executive insight', 'executive summary', 'key issue',
            'critical issue', 'bottleneck', 'top issue'
        ]
        
        for pattern in executive_patterns:
            if pattern in normalized:
                return "executive_insight"
        
        control_tower_patterns = [
            'control tower', 'critical alert', 'critical delivery',
            'oldest pending', 'urgent matter', 'priority issue'
        ]
        
        for pattern in control_tower_patterns:
            if pattern in normalized:
                return "control_tower"
        
        return None
    
    def _is_data_quality_query(self, normalized: str) -> bool:
        """Check if query is about data quality."""
        patterns = [
            'data issue', 'invalid date', 'negative aging',
            'date mismatch', 'incorrect date', 'invalid pod',
            'pgi after pod', 'data quality', 'bad data'
        ]
        return any(pattern in normalized for pattern in patterns)
    
    def _resolve_context_query(self, normalized: str, original: str, context: Optional[Dict]) -> Optional[str]:
        """Resolve context-based queries."""
        if not context:
            return None
        
        context_keywords = ['status', 'details', 'info', 'show', 'get']
        if not any(kw in normalized for kw in context_keywords):
            return None
        
        if context.get('last_dn'):
            return "dn_lookup"
        elif context.get('last_dealer'):
            return "dealer_dashboard"
        elif context.get('last_warehouse'):
            return "warehouse_dashboard"
        elif context.get('last_city'):
            return "city_dashboard"
        
        return None
    
    def _is_help_query(self, normalized: str) -> bool:
        """Check if query is a help request."""
        patterns = ['help', 'menu', 'commands', 'what can you do', 'available commands']
        return any(pattern in normalized for pattern in patterns)
    
    def _extract_date_range(self, normalized: str) -> Optional[Dict[str, str]]:
        """Extract date range from normalized text."""
        today = self._get_today()
        
        if 'today' in normalized:
            return {'start_date': today.isoformat(), 'end_date': today.isoformat()}
        
        if 'yesterday' in normalized:
            yesterday = today - timedelta(days=1)
            return {'start_date': yesterday.isoformat(), 'end_date': yesterday.isoformat()}
        
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
        
        if 'this month' in normalized or 'current month' in normalized:
            start = today.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': today.isoformat()}
        
        if 'last month' in normalized:
            last_month = today.replace(day=1) - timedelta(days=1)
            start = last_month.replace(day=1)
            return {'start_date': start.isoformat(), 'end_date': last_month.isoformat()}
        
        return None
    
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
    
    def _normalize(self, text: str) -> str:
        """Normalize text for processing."""
        if not text:
            return ""
        
        normalized = text.lower()
        normalized = WHITESPACE_PATTERN.sub(' ', normalized)
        normalized = SPECIAL_CHARS_PATTERN.sub('', normalized)
        return normalized.strip()
    
    def _create_default_plan(self, reason: str, query_id: str = None) -> QueryPlan:
        """Create a default query plan for error/empty cases."""
        return QueryPlan(
            intent="general_ai",
            entity=None,
            entity_type=None,
            service="groq",
            confidence=0.1,
            needs_groq=True,
            query_category="general",
            reason=reason,
            original_message=reason,
            normalized_message=reason,
            query_id=query_id or str(uuid.uuid4())[:8]
        )
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics for diagnostics."""
        return {
            "valid_intents": len(VALID_INTENTS),
            "valid_services": len(VALID_SERVICES),
            "strictly_analytics_intents": len(STRICTLY_ANALYTICS_INTENTS),
            "groq_intents": len(GROQ_INTENTS),
            "last_context": self._last_context,
            "dealer_count": len(self.schema.dealers),
            "warehouse_count": len(self.schema.warehouses),
            "city_count": len(self.schema.cities)
        }
    
    def get_last_context(self) -> Dict[str, Any]:
        """Get last detected context."""
        return self._last_context.copy()
    
    def debug_route(self, question: str) -> Dict[str, Any]:
        """Debug routing decision for a question."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        plan = loop.run_until_complete(self.process_query(question, None))
        return plan.to_dict()


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

logger.debug("AIQueryService v5.0 - Enhanced Dealer Detection")
logger.debug(f"Valid intents: {len(VALID_INTENTS)}")
logger.debug(f"Valid services: {len(VALID_SERVICES)}")
logger.debug("=" * 60)
logger.debug("ENHANCEMENTS:")
logger.debug("  ✅ Enhanced dealer detection with partial matching")
logger.debug("  ✅ Dealer names now properly recognized")
logger.debug("  ✅ Pure dealer names route to dealer_dashboard")
logger.debug("  ✅ Better handling of dealer name variations")
logger.debug("  ✅ Improved logging for debugging")
logger.debug("=" * 60)
