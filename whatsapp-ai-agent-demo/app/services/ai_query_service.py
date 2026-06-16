# ==========================================================
# FILE: app/services/ai_query_service.py (v6.0 - PURE ROUTING FILE)
# ==========================================================
# PURPOSE: PURE ROUTING ENGINE - Detect Intent, Route to Service
# ARCHITECTURE: Single Source of Truth for Routing
#
# RESPONSIBILITIES:
# 1. Detect intent from user query
# 2. Extract entity (dealer, warehouse, city, DN)
# 3. Determine target service (analytics, kpi, groq)
# 4. Return standardized routing decision
#
# ROUTING MATRIX:
# | Input                          | Intent                | Service    |
# |--------------------------------|-----------------------|------------|
# | 6243610699                     | dn_lookup             | analytics  |
# | Dubai Electronics              | dealer_dashboard      | analytics  |
# | Dubai Electronics revenue      | dealer_revenue        | analytics  |
# | Dubai Electronics units        | dealer_units          | analytics  |
# | Dubai Electronics aging        | dealer_aging          | analytics  |
# | Dubai Electronics performance  | dealer_performance    | analytics  |
# | Dubai Electronics DNS          | dealer_dns            | analytics  |
# | Rawalpindi                     | warehouse_dashboard   | analytics  |
# | Haripur                        | city_dashboard        | analytics  |
# | pending pgi                    | pending_pgi           | kpi        |
# | pending pod                    | pending_pod           | kpi        |
# | top dealers revenue            | top_dealers_revenue   | analytics  |
# | top dealers units              | top_dealers_units     | analytics  |
# | top pending warehouses         | top_warehouses_pending| analytics  |
# | key issue                      | executive_insight     | groq       |
# | control tower                  | control_tower         | groq       |
# | invalid dates                  | data_quality_analysis | analytics  |
# | who is imran khan              | general_ai            | groq       |
# ==========================================================

import re
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from loguru import logger


# ==========================================================
# IMPORT SAFETY
# ==========================================================

try:
    from app.schemas.schema_service import get_schema_service
    logger.debug("Successfully imported get_schema_service")
except ImportError as e:
    logger.error(f"Failed to import get_schema_service: {e}")
    raise


# ==========================================================
# COMPILED REGEX PATTERNS
# ==========================================================

# DN Number Pattern (8-12 digits)
DN_PATTERN = re.compile(r'\b(\d{8,12})\b')

# Dealer Extraction Pattern
DEALER_PATTERN = re.compile(r'(?:dealer|show|display|get|view|tell me about)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)

# Ranking Limit Pattern
RANKING_LIMIT_PATTERN = re.compile(r'(?:top|bottom)\s+(\d+)', re.IGNORECASE)

# Whitespace Normalization
WHITESPACE_PATTERN = re.compile(r'\s+')
SPECIAL_CHARS_PATTERN = re.compile(r'[^\w\s\-&.]')


# ==========================================================
# ROUTING DECISION CLASS
# ==========================================================

@dataclass
class RoutingDecision:
    """
    Standardized routing decision output.
    
    This is the SINGLE SOURCE OF TRUTH for all routing decisions.
    """
    intent: str
    entity: Optional[str] = None
    entity_type: Optional[str] = None
    service: str = "analytics"
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "intent": self.intent,
            "entity": self.entity,
            "entity_type": self.entity_type,
            "service": self.service,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message
        }
    
    def __repr__(self) -> str:
        return f"RoutingDecision(intent={self.intent}, service={self.service}, entity={self.entity})"


# ==========================================================
# AI QUERY SERVICE - PURE ROUTING ENGINE
# ==========================================================

class AIQueryService:
    """
    PURE ROUTING ENGINE - Single Source of Truth for Routing
    
    This service ONLY does routing. It does NOT:
    - Execute database queries
    - Calculate analytics
    - Call Groq directly
    - Format responses
    - Send WhatsApp messages
    
    ROUTING MATRIX:
    - DN Lookup → analytics
    - Dealer Queries → analytics
    - Warehouse Queries → analytics
    - City Queries → analytics
    - KPI Queries → kpi
    - Ranking Queries → analytics
    - Executive Queries → groq (with analytics data)
    - Data Quality → analytics
    - General AI → groq
    """
    
    def __init__(self):
        """Initialize AIQueryService with SchemaService."""
        start_time = time.time()
        
        try:
            logger.info("Loading SchemaService for AIQueryService...")
            self.schema = get_schema_service()
            logger.info("SchemaService loaded successfully")
            
            # Cache for performance
            self._logistics_keywords_cache = self.schema.logistics_keywords
            logger.debug(f"Cached {len(self._logistics_keywords_cache)} logistics keywords")
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"AIQueryService initialized in {init_duration:.2f}ms")
            logger.info("=" * 60)
            logger.info("ROUTING CAPABILITIES:")
            logger.info("  1. DN Lookup → analytics")
            logger.info("  2. Dealer (dashboard/revenue/units/aging/performance/dns) → analytics")
            logger.info("  3. Warehouse → analytics")
            logger.info("  4. City → analytics")
            logger.info("  5. KPI (pending_pgi/pending_pod) → kpi")
            logger.info("  6. Ranking (top_dealers_revenue/top_dealers_units/top_warehouses_pending) → analytics")
            logger.info("  7. Executive/Control Tower → groq (analytics + Groq)")
            logger.info("  8. Data Quality → analytics")
            logger.info("  9. General AI → groq")
            logger.info("=" * 60)
            
        except Exception as e:
            logger.exception(f"Failed to initialize AIQueryService: {str(e)}")
            raise RuntimeError(f"AIQueryService initialization failed: {str(e)}") from e
    
    # ==========================================================
    # MAIN ROUTING METHOD
    # ==========================================================
    
    async def process_query(self, question: Optional[str], context: Optional[Dict] = None) -> RoutingDecision:
        """
        Process query and return routing decision.
        
        Args:
            question: User's query text
            context: Optional context dictionary
            
        Returns:
            RoutingDecision: Standardized routing decision
        """
        query_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
        # Input validation
        if not question or not question.strip():
            return RoutingDecision(
                intent="help",
                service="help",
                confidence=0.0,
                reason="Empty query",
                original_message=question or ""
            )
        
        cleaned_question = question.strip()
        normalized = self._normalize(cleaned_question)
        
        logger.info(f"Query {query_id}: Processing: '{cleaned_question[:100]}'")
        
        # ==========================================================
        # STEP 1: DN DETECTION (Highest Priority)
        # ==========================================================
        
        dn_match = DN_PATTERN.search(cleaned_question)
        if dn_match:
            dn_number = dn_match.group(1)
            logger.info(f"Query {query_id}: ✅ DN Detected: {dn_number} → dn_lookup (analytics)")
            
            return RoutingDecision(
                intent="dn_lookup",
                entity=dn_number,
                entity_type="dn",
                service="analytics",
                confidence=1.0,
                needs_groq=False,
                reason=f"DN number detected: {dn_number}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 2: KPI DETECTION (Before entity resolution)
        # ==========================================================
        
        kpi_result = self._detect_kpi(normalized)
        if kpi_result:
            intent, entity = kpi_result
            logger.info(f"Query {query_id}: ✅ KPI Detected: {intent} → kpi")
            
            return RoutingDecision(
                intent=intent,
                entity=entity,
                entity_type="kpi",
                service="kpi",
                confidence=0.95,
                needs_groq=False,
                reason=f"KPI pattern matched: {intent}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 3: RANKING DETECTION
        # ==========================================================
        
        ranking_result = self._detect_ranking(normalized)
        if ranking_result:
            intent, entity_type = ranking_result
            logger.info(f"Query {query_id}: ✅ Ranking Detected: {intent} → analytics")
            
            return RoutingDecision(
                intent=intent,
                entity=None,
                entity_type=entity_type,
                service="analytics",
                confidence=0.90,
                needs_groq=False,
                reason=f"Ranking pattern matched: {intent}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 4: EXECUTIVE / CONTROL TOWER DETECTION
        # ==========================================================
        
        executive_result = self._detect_executive(normalized)
        if executive_result:
            intent, needs_groq = executive_result
            logger.info(f"Query {query_id}: ✅ Executive Detected: {intent} → groq (analytics + Groq)")
            
            return RoutingDecision(
                intent=intent,
                entity=None,
                entity_type="executive",
                service="groq",
                confidence=0.90,
                needs_groq=needs_groq,
                reason=f"Executive pattern matched: {intent}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 5: DATA QUALITY DETECTION
        # ==========================================================
        
        if self._is_data_quality_query(normalized):
            logger.info(f"Query {query_id}: ✅ Data Quality Detected → data_quality_analysis (analytics)")
            
            return RoutingDecision(
                intent="data_quality_analysis",
                entity=None,
                entity_type=None,
                service="analytics",
                confidence=0.90,
                needs_groq=False,
                reason="Data quality/validation query",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 6: DEALER DETECTION
        # ==========================================================
        
        dealer_result = self._detect_dealer(cleaned_question, normalized, context)
        if dealer_result:
            dealer_name = dealer_result
            intent = self._determine_dealer_intent(normalized)
            logger.info(f"Query {query_id}: ✅ Dealer Detected: '{dealer_name}' → {intent} (analytics)")
            
            return RoutingDecision(
                intent=intent,
                entity=dealer_name,
                entity_type="dealer",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer resolved: {dealer_name}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 7: WAREHOUSE DETECTION
        # ==========================================================
        
        warehouse_result = self._detect_warehouse(cleaned_question, normalized)
        if warehouse_result:
            warehouse_name = warehouse_result
            logger.info(f"Query {query_id}: ✅ Warehouse Detected: '{warehouse_name}' → warehouse_dashboard (analytics)")
            
            return RoutingDecision(
                intent="warehouse_dashboard",
                entity=warehouse_name,
                entity_type="warehouse",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"Warehouse resolved: {warehouse_name}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 8: CITY DETECTION
        # ==========================================================
        
        city_result = self._detect_city(cleaned_question, normalized)
        if city_result:
            city_name = city_result
            logger.info(f"Query {query_id}: ✅ City Detected: '{city_name}' → city_dashboard (analytics)")
            
            return RoutingDecision(
                intent="city_dashboard",
                entity=city_name,
                entity_type="city",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"City resolved: {city_name}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 9: CONTEXT RESOLUTION
        # ==========================================================
        
        context_result = self._resolve_context(normalized, context)
        if context_result:
            intent, entity, entity_type = context_result
            logger.info(f"Query {query_id}: 🔄 Context Resolved: {intent} (analytics)")
            
            return RoutingDecision(
                intent=intent,
                entity=entity,
                entity_type=entity_type,
                service="analytics",
                confidence=0.85,
                needs_groq=False,
                reason=f"Context resolution: {intent}",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 10: HELP DETECTION
        # ==========================================================
        
        if self._is_help_query(normalized):
            logger.info(f"Query {query_id}: ❓ Help Detected → help (groq)")
            
            return RoutingDecision(
                intent="help",
                entity=None,
                entity_type=None,
                service="groq",
                confidence=0.95,
                needs_groq=True,
                reason="Help request",
                original_message=cleaned_question
            )
        
        # ==========================================================
        # STEP 11: GENERAL AI (GROQ - LAST RESORT)
        # ==========================================================
        
        logger.info(f"Query {query_id}: 🤖 General AI → general_ai (groq)")
        
        return RoutingDecision(
            intent="general_ai",
            entity=None,
            entity_type=None,
            service="groq",
            confidence=0.50,
            needs_groq=True,
            reason="No specific pattern matched - using Groq",
            original_message=cleaned_question
        )
    
    # ==========================================================
    # DETECTION METHODS
    # ==========================================================
    
    def _detect_kpi(self, normalized: str) -> Optional[Tuple[str, Optional[str]]]:
        """Detect KPI queries."""
        # Pending PGI
        if "pending pgi" in normalized or "pgi pending" in normalized:
            return ("pending_pgi", self._extract_entity(normalized))
        
        # Pending POD
        if "pending pod" in normalized or "pod pending" in normalized:
            return ("pending_pod", self._extract_entity(normalized))
        
        return None
    
    def _detect_ranking(self, normalized: str) -> Optional[Tuple[str, str]]:
        """Detect ranking queries."""
        # Top Dealers by Revenue
        if "top dealer" in normalized or "top dealers" in normalized:
            if "revenue" in normalized or "sales" in normalized:
                return ("top_dealers_revenue", "dealer")
            if "unit" in normalized or "quantity" in normalized:
                return ("top_dealers_units", "dealer")
            return ("top_dealers_revenue", "dealer")
        
        # Top Warehouses by Pending
        if "top warehouse" in normalized or "pending warehouse" in normalized:
            return ("top_warehouses_pending", "warehouse")
        
        return None
    
    def _detect_executive(self, normalized: str) -> Optional[Tuple[str, bool]]:
        """Detect executive/control tower queries."""
        if "key issue" in normalized or "critical issue" in normalized or "executive" in normalized:
            return ("executive_insight", True)
        
        if "control tower" in normalized or "critical alert" in normalized:
            return ("control_tower", True)
        
        return None
    
    def _detect_dealer(self, original: str, normalized: str, context: Optional[Dict]) -> Optional[str]:
        """Detect dealer from query."""
        # Strategy 1: Direct resolution
        dealer = self.schema.resolve_dealer(original)
        if dealer:
            return dealer
        
        dealer = self.schema.resolve_dealer(normalized)
        if dealer:
            return dealer
        
        # Strategy 2: Pattern extraction
        dealer_match = DEALER_PATTERN.search(original)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            resolved = self.schema.resolve_dealer(candidate)
            if resolved:
                return resolved
        
        # Strategy 3: Word combinations
        words = normalized.split()
        if len(words) >= 2:
            for i in range(len(words) - 1):
                for j in range(i + 1, min(i + 4, len(words) + 1)):
                    candidate = ' '.join(words[i:j])
                    if len(candidate) >= 4:
                        resolved = self.schema.resolve_dealer(candidate)
                        if resolved:
                            return resolved
        
        # Strategy 4: Single words
        for word in words:
            if len(word) >= 3:
                resolved = self.schema.resolve_dealer(word)
                if resolved:
                    return resolved
        
        # Strategy 5: Context
        if context and context.get('last_dealer'):
            follow_up = ['revenue', 'units', 'performance', 'aging', 'pending', 'pod', 'pgi']
            if any(kw in normalized for kw in follow_up):
                return context['last_dealer']
        
        return None
    
    def _detect_warehouse(self, original: str, normalized: str) -> Optional[str]:
        """Detect warehouse from query."""
        warehouse = self.schema.resolve_warehouse(original)
        if warehouse:
            return warehouse
        
        warehouse = self.schema.resolve_warehouse(normalized)
        if warehouse:
            return warehouse
        
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_warehouse(word)
                if resolved:
                    return resolved
        
        return None
    
    def _detect_city(self, original: str, normalized: str) -> Optional[str]:
        """Detect city from query."""
        city = self.schema.resolve_city(original)
        if city:
            return city
        
        city = self.schema.resolve_city(normalized)
        if city:
            return city
        
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_city(word)
                if resolved:
                    return resolved
        
        return None
    
    def _determine_dealer_intent(self, normalized: str) -> str:
        """Determine dealer intent based on query."""
        if "dns" in normalized or "orders" in normalized:
            return "dealer_dns"
        if "revenue" in normalized or "sales" in normalized:
            return "dealer_revenue"
        if "units" in normalized or "quantity" in normalized:
            return "dealer_units"
        if "performance" in normalized or "kpi" in normalized:
            return "dealer_performance"
        if "aging" in normalized or "delay" in normalized or "pending" in normalized:
            return "dealer_aging"
        return "dealer_dashboard"
    
    def _resolve_context(self, normalized: str, context: Optional[Dict]) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
        """Resolve context-based queries."""
        if not context:
            return None
        
        context_keywords = ['status', 'details', 'info', 'show']
        if not any(kw in normalized for kw in context_keywords):
            return None
        
        if context.get('last_dn'):
            return ("dn_lookup", context['last_dn'], "dn")
        if context.get('last_dealer'):
            return ("dealer_dashboard", context['last_dealer'], "dealer")
        if context.get('last_warehouse'):
            return ("warehouse_dashboard", context['last_warehouse'], "warehouse")
        if context.get('last_city'):
            return ("city_dashboard", context['last_city'], "city")
        
        return None
    
    def _is_data_quality_query(self, normalized: str) -> bool:
        """Check if query is about data quality."""
        patterns = ['data issue', 'invalid date', 'negative aging', 'date mismatch', 'data quality']
        return any(p in normalized for p in patterns)
    
    def _is_help_query(self, normalized: str) -> bool:
        """Check if query is a help request."""
        patterns = ['help', 'menu', 'commands', 'what can you do']
        return any(p in normalized for p in patterns)
    
    def _extract_entity(self, normalized: str) -> Optional[str]:
        """Extract entity from query."""
        dealer = self.schema.resolve_dealer(normalized)
        if dealer:
            return dealer
        
        city = self.schema.resolve_city(normalized)
        if city:
            return city
        
        warehouse = self.schema.resolve_warehouse(normalized)
        if warehouse:
            return warehouse
        
        return None
    
    def _normalize(self, text: str) -> str:
        """Normalize text for processing."""
        if not text:
            return ""
        
        normalized = text.lower()
        normalized = WHITESPACE_PATTERN.sub(' ', normalized)
        normalized = SPECIAL_CHARS_PATTERN.sub('', normalized)
        return normalized.strip()
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def debug_route(self, question: str) -> Dict[str, Any]:
        """Debug routing decision for a question."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        decision = loop.run_until_complete(self.process_query(question, None))
        return decision.to_dict()
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            "version": "6.0",
            "type": "Pure Routing Engine",
            "dealer_count": len(self.schema.dealers),
            "warehouse_count": len(self.schema.warehouses),
            "city_count": len(self.schema.cities)
        }


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
# MODULE INITIALIZATION
# ==========================================================

logger.debug("AIQueryService v6.0 - Pure Routing Engine")
logger.debug("=" * 60)
logger.debug("ROUTING MATRIX:")
logger.debug("  ✅ DN Lookup → analytics")
logger.debug("  ✅ Dealer (dashboard/revenue/units/aging/performance/dns) → analytics")
logger.debug("  ✅ Warehouse → analytics")
logger.debug("  ✅ City → analytics")
logger.debug("  ✅ KPI (pending_pgi/pending_pod) → kpi")
logger.debug("  ✅ Ranking → analytics")
logger.debug("  ✅ Executive/Control Tower → groq")
logger.debug("  ✅ Data Quality → analytics")
logger.debug("  ✅ General AI → groq")
logger.debug("=" * 60)
