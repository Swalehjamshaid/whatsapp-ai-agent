# ==========================================================
# FILE: app/services/ai_query_service.py (v7.2 - PRODUCTION FIX)
# ==========================================================
# PURPOSE: PURE ROUTING ENGINE - Entity-First, Intent-Second
# ARCHITECTURE: Single Source of Truth for Routing
#
# FIXES APPLIED:
# 1. ✅ Startup Validation - Checks metadata on initialization
# 2. ✅ Router Diagnostics - Full logging of all detection attempts
# 3. ✅ Executive Routing - Analytics first, then Groq enrichment
# 4. ✅ Startup Logging - Shows loaded entities count
# 5. ✅ Metadata Endpoint Support - get_schema_stats()
# 6. ✅ Entity Debug Support - debug_entity() method
# ==========================================================

import re
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from loguru import logger


# ==========================================================
# IMPORT SAFETY - Integrated with SchemaService v7.1
# ==========================================================

try:
    from app.schemas.schema_service import (
        get_schema_service,
        DN_PATTERN,
        resolve_entity,
        find_dealer_debug,
        find_city_debug,
        find_warehouse_debug,
        get_all_entities,
        search_entities,
        generate_metadata_report
    )
    logger.debug("✅ Successfully imported SchemaService v7.1")
except ImportError as e:
    logger.error(f"❌ Failed to import SchemaService: {e}")
    raise


# ==========================================================
# COMPILED REGEX PATTERNS
# ==========================================================

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
    
    # Diagnostic fields - Full routing visibility
    detected_dn: Optional[str] = None
    detected_dealer: Optional[str] = None
    detected_city: Optional[str] = None
    detected_warehouse: Optional[str] = None
    detected_intent: Optional[str] = None
    routing_path: str = ""
    schema_health: Optional[Dict[str, Any]] = None
    
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
            "original_message": self.original_message,
            "detected_dn": self.detected_dn,
            "detected_dealer": self.detected_dealer,
            "detected_city": self.detected_city,
            "detected_warehouse": self.detected_warehouse,
            "detected_intent": self.detected_intent,
            "routing_path": self.routing_path,
            "schema_health": self.schema_health
        }
    
    def __repr__(self) -> str:
        return (f"RoutingDecision(intent={self.intent}, entity={self.entity}, "
                f"service={self.service}, confidence={self.confidence:.2f}, "
                f"path={self.routing_path})")


# ==========================================================
# AI QUERY SERVICE - ENTITY-FIRST ROUTING ENGINE
# ==========================================================

class AIQueryService:
    """
    ENTITY-FIRST ROUTING ENGINE - Single Source of Truth for Routing
    
    FULLY INTEGRATED WITH: SchemaService v7.1
    
    ROUTING PRIORITY (ENFORCED):
    1. DN Detection (8-12 digits) → analytics
    2. Dealer Resolution → analytics
    3. City Resolution → analytics
    4. Warehouse Resolution → analytics
    5. Intent Detection → analytics/kpi/groq
    6. Groq (LAST RESORT) → groq
    7. Help → help
    
    GROQ GOVERNANCE:
    - Groq ONLY when all routing fails
    - Groq NEVER for DN/Dealer/City/Warehouse/Intent
    - Executive intents use analytics data + Groq enrichment (NOT Groq alone)
    
    SCHEMASERVICE INTEGRATION:
    - Uses schema.resolve_dealer() for dealer resolution
    - Uses schema.resolve_city() for city resolution
    - Uses schema.resolve_warehouse() for warehouse resolution
    - Uses schema.is_dn_number() for DN validation
    - Uses schema.detect_intent() for intent detection
    - Uses schema.detect_metric() for metric detection
    - Uses schema.is_logistics_keyword() for Groq governance
    - Uses schema.get_health_report() for diagnostics
    
    This service ONLY does routing. It does NOT:
    - Execute database queries
    - Calculate analytics
    - Call Groq directly
    - Format responses
    - Send WhatsApp messages
    """
    
    def __init__(self):
        """Initialize AIQueryService with SchemaService v7.1."""
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("Loading SchemaService v7.1 for AIQueryService...")
            logger.info("=" * 70)
            
            # Load SchemaService v7.1
            self.schema = get_schema_service()
            logger.info("✅ SchemaService v7.1 loaded successfully")
            
            # ==========================================================
            # PRIORITY 1: STARTUP VALIDATION & DIAGNOSTICS
            # ==========================================================
            
            # Log loaded entities count
            dealer_count = len(self.schema.dealers)
            city_count = len(self.schema.cities)
            warehouse_count = len(self.schema.warehouses)
            
            logger.info("")
            logger.info("📊 *METADATA LOAD STATUS:*")
            logger.info(f"   🏪 Dealers: {dealer_count}")
            logger.info(f"   🏙️ Cities: {city_count}")
            logger.info(f"   🏭 Warehouses: {warehouse_count}")
            logger.info("")
            
            # Startup Validation - Check if metadata loaded
            if dealer_count == 0:
                logger.error("❌ CRITICAL: No dealers loaded from database!")
                logger.error("   Check SchemaService connection to database.")
                logger.error("   Check that DeliveryReport table has data.")
                logger.error("   Check column names: customer_name, ship_to_city, warehouse")
                
                # In production, raise error if no metadata
                raise RuntimeError(
                    "No dealers loaded from database. "
                    "Please check database connection and data import."
                )
            
            if city_count == 0:
                logger.warning("⚠️ No cities loaded from database")
                logger.warning("   City resolution will not work.")
            
            if warehouse_count == 0:
                logger.warning("⚠️ No warehouses loaded from database")
                logger.warning("   Warehouse resolution will not work.")
            
            # Log health report
            health = self.schema.get_health_report()
            logger.info(f"   📊 Health Score: {health.get('health_score', 0)}/100")
            logger.info(f"   📋 Status: {health.get('status', 'unknown')}")
            
            # Cache for performance
            self._logistics_keywords_cache = self.schema.logistics_keywords
            logger.debug(f"✅ Cached {len(self._logistics_keywords_cache)} logistics keywords")
            
            # Routing statistics
            self._routing_stats = {
                "dn_lookups": 0,
                "dealer_resolutions": 0,
                "city_resolutions": 0,
                "warehouse_resolutions": 0,
                "intent_detections": 0,
                "groq_fallbacks": 0,
                "help_requests": 0
            }
            
            init_duration = (time.time() - start_time) * 1000
            logger.info("")
            logger.info("=" * 70)
            logger.info("AIQueryService v7.2 initialized successfully")
            logger.info("=" * 70)
            logger.info("")
            logger.info("   ROUTING PRIORITY (ENFORCED):")
            logger.info("   1️⃣ DN Lookup → analytics")
            logger.info("   2️⃣ Dealer Resolution → analytics")
            logger.info("   3️⃣ City Resolution → analytics")
            logger.info("   4️⃣ Warehouse Resolution → analytics")
            logger.info("   5️⃣ Intent Detection → analytics/kpi/groq")
            logger.info("   6️⃣ Groq (LAST RESORT) → groq")
            logger.info("   7️⃣ Help → help")
            logger.info("")
            logger.info("   GROQ GOVERNANCE:")
            logger.info("   ✅ Groq ONLY when all routing fails")
            logger.info("   ✅ Groq NEVER for DN/Dealer/City/Warehouse")
            logger.info("   ✅ Executive: Analytics data + Groq enrichment")
            logger.info("")
            logger.info("   SCHEMASERVICE INTEGRATION:")
            logger.info("   ✅ resolve_dealer() → Dealer resolution")
            logger.info("   ✅ resolve_city() → City resolution")
            logger.info("   ✅ resolve_warehouse() → Warehouse resolution")
            logger.info("   ✅ detect_intent() → Intent detection")
            logger.info("   ✅ is_dn_number() → DN validation")
            logger.info("   ✅ get_health_report() → Diagnostics")
            logger.info("")
            logger.info("   STATUS: ✅ PRODUCTION READY")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize AIQueryService: {str(e)}")
            raise RuntimeError(f"AIQueryService initialization failed: {str(e)}") from e
    
    # ==========================================================
    # MAIN ROUTING METHOD
    # ==========================================================
    
    async def process_query(self, question: Optional[str], context: Optional[Dict] = None) -> RoutingDecision:
        """
        Process query and return routing decision.
        
        ROUTING PRIORITY (ENFORCED):
        1. DN Detection (8-12 digits) → analytics
        2. Dealer Resolution → analytics
        3. City Resolution → analytics
        4. Warehouse Resolution → analytics
        5. Intent Detection → analytics/kpi/groq
        6. Groq (LAST RESORT) → groq
        7. Help → help
        
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
                original_message=question or "",
                routing_path="empty_query"
            )
        
        cleaned_question = question.strip()
        normalized = self._normalize(cleaned_question)
        
        logger.info(f"Query {query_id}: Processing: '{cleaned_question[:100]}'")
        
        # ==========================================================
        # PRIORITY 2: ROUTER DIAGNOSTICS
        # ==========================================================
        
        logger.info(f"🔍 ROUTER DIAGNOSTIC - QUESTION: '{cleaned_question}'")
        
        # ==========================================================
        # ROUTING DIAGNOSTICS - Track all detection attempts
        # ==========================================================
        
        detected_dn = None
        detected_dealer = None
        detected_city = None
        detected_warehouse = None
        detected_intent = None
        routing_path = ""
        
        # Get SchemaService health for diagnostics
        schema_health = self.schema.get_health_report()
        
        # ==========================================================
        # PRIORITY 1: DN DETECTION (Highest Priority)
        # Uses SchemaService v7.1 is_dn_number()
        # ==========================================================
        
        if self.schema.is_dn_number(cleaned_question):
            dn_number = cleaned_question
            detected_dn = dn_number
            routing_path = "dn_lookup"
            self._routing_stats["dn_lookups"] += 1
            
            logger.info(f"Query {query_id}: ✅ DN Detected: {dn_number} → dn_lookup (analytics)")
            logger.info(f"🔍 ROUTER DIAGNOSTIC - DN: '{dn_number}'")
            
            return RoutingDecision(
                intent="dn_lookup",
                entity=dn_number,
                entity_type="dn",
                service="analytics",
                confidence=1.0,
                needs_groq=False,
                reason=f"DN number detected: {dn_number}",
                original_message=cleaned_question,
                detected_dn=dn_number,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # Also check using DN_PATTERN for extraction
        dn_match = DN_PATTERN.search(cleaned_question)
        if dn_match:
            dn_number = dn_match.group(1)
            detected_dn = dn_number
            routing_path = "dn_lookup"
            self._routing_stats["dn_lookups"] += 1
            
            logger.info(f"Query {query_id}: ✅ DN Extracted: {dn_number} → dn_lookup (analytics)")
            logger.info(f"🔍 ROUTER DIAGNOSTIC - DN: '{dn_number}'")
            
            return RoutingDecision(
                intent="dn_lookup",
                entity=dn_number,
                entity_type="dn",
                service="analytics",
                confidence=1.0,
                needs_groq=False,
                reason=f"DN number extracted: {dn_number}",
                original_message=cleaned_question,
                detected_dn=dn_number,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 2: DEALER RESOLUTION
        # Uses SchemaService v7.1 resolve_dealer()
        # ==========================================================
        
        dealer_result = self._detect_dealer(cleaned_question, normalized, context)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - DEALER: '{dealer_result}'")
        
        if dealer_result:
            dealer_name = dealer_result
            detected_dealer = dealer_name
            routing_path = "dealer_resolution"
            self._routing_stats["dealer_resolutions"] += 1
            intent = self._determine_dealer_intent(normalized)
            
            # Get confidence from SchemaService
            confidence = 0.95
            try:
                debug_info = self.schema.find_dealer_debug(dealer_name)
                confidence = debug_info.get('confidence', 0.95)
            except:
                pass
            
            logger.info(f"Query {query_id}: ✅ Dealer Detected: '{dealer_name}' → {intent} (analytics, confidence={confidence:.2f})")
            
            return RoutingDecision(
                intent=intent,
                entity=dealer_name,
                entity_type="dealer",
                service="analytics",
                confidence=confidence,
                needs_groq=False,
                reason=f"Dealer resolved: {dealer_name}",
                original_message=cleaned_question,
                detected_dealer=dealer_name,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 3: CITY RESOLUTION
        # Uses SchemaService v7.1 resolve_city()
        # ==========================================================
        
        city_result = self._detect_city(cleaned_question, normalized)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - CITY: '{city_result}'")
        
        if city_result:
            city_name = city_result
            detected_city = city_name
            routing_path = "city_resolution"
            self._routing_stats["city_resolutions"] += 1
            
            logger.info(f"Query {query_id}: ✅ City Detected: '{city_name}' → city_dashboard (analytics)")
            
            return RoutingDecision(
                intent="city_dashboard",
                entity=city_name,
                entity_type="city",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"City resolved: {city_name}",
                original_message=cleaned_question,
                detected_city=city_name,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 4: WAREHOUSE RESOLUTION
        # Uses SchemaService v7.1 resolve_warehouse()
        # ==========================================================
        
        warehouse_result = self._detect_warehouse(cleaned_question, normalized)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - WAREHOUSE: '{warehouse_result}'")
        
        if warehouse_result:
            warehouse_name = warehouse_result
            detected_warehouse = warehouse_name
            routing_path = "warehouse_resolution"
            self._routing_stats["warehouse_resolutions"] += 1
            
            logger.info(f"Query {query_id}: ✅ Warehouse Detected: '{warehouse_name}' → warehouse_dashboard (analytics)")
            
            return RoutingDecision(
                intent="warehouse_dashboard",
                entity=warehouse_name,
                entity_type="warehouse",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"Warehouse resolved: {warehouse_name}",
                original_message=cleaned_question,
                detected_warehouse=warehouse_name,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 5: INTENT DETECTION
        # Uses SchemaService v7.1 detect_intent() and detect_metric()
        # ==========================================================
        
        intent_result = self._detect_intent(normalized, cleaned_question)
        
        if intent_result:
            intent, confidence, needs_groq = intent_result
            detected_intent = intent
            routing_path = "intent_detection"
            self._routing_stats["intent_detections"] += 1
            
            # ==========================================================
            # FIX: Executive Routing - Analytics First, Groq Enrichment
            # ==========================================================
            
            # Determine service based on intent
            # Executive intents go to analytics for data, then Groq for enrichment
            executive_intents = ['executive_insight', 'root_cause', 'control_tower', 'comparison', 'trend']
            
            if intent in executive_intents:
                service = "analytics"  # ← FIX: Analytics first, not Groq
                needs_groq = True      # ← FIX: Groq enriches after analytics
            else:
                service = self._determine_service_for_intent(intent)
            
            logger.info(f"Query {query_id}: 🎯 Intent Detected: {intent} (confidence={confidence:.2f}, service={service}, needs_groq={needs_groq})")
            
            return RoutingDecision(
                intent=intent,
                entity=None,
                entity_type=None,
                service=service,
                confidence=confidence,
                needs_groq=needs_groq,
                reason=f"Intent detected: {intent}",
                original_message=cleaned_question,
                detected_intent=intent,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 6: HELP DETECTION (Explicit Help)
        # ==========================================================
        
        if self._is_help_query(normalized):
            routing_path = "help"
            self._routing_stats["help_requests"] += 1
            
            logger.info(f"Query {query_id}: ❓ Help Detected → help")
            
            return RoutingDecision(
                intent="help",
                entity=None,
                entity_type=None,
                service="help",
                confidence=0.95,
                needs_groq=True,
                reason="Help request",
                original_message=cleaned_question,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 7: GROQ (LAST RESORT)
        # Only reached if all routing fails
        # ==========================================================
        
        routing_path = "groq_fallback"
        self._routing_stats["groq_fallbacks"] += 1
        
        logger.info(f"Query {query_id}: 🤖 Groq Fallback → general_ai (groq)")
        logger.info(f"🔍 ROUTER DIAGNOSTIC - FINAL ROUTE: groq_fallback")
        
        return RoutingDecision(
            intent="general_ai",
            entity=None,
            entity_type=None,
            service="groq",
            confidence=0.30,
            needs_groq=True,
            reason="No specific pattern matched - using Groq",
            original_message=cleaned_question,
            routing_path=routing_path,
            schema_health=schema_health
        )
    
    # ==========================================================
    # DETECTION METHODS
    # ==========================================================
    
    def _detect_dealer(self, original: str, normalized: str, context: Optional[Dict]) -> Optional[str]:
        """
        Detect dealer from query with multiple strategies.
        
        Uses SchemaService v7.1 resolve_dealer() as primary strategy.
        
        Strategies:
        1. Direct SchemaService resolution
        2. Pattern-based extraction
        3. Word combinations
        4. Single word matching
        5. Context-based
        6. SchemaService search_entities()
        """
        logger.debug(f"Detecting dealer in: '{original}'")
        
        # Strategy 1: Direct SchemaService resolution
        dealer = self.schema.resolve_dealer(original)
        if dealer:
            logger.debug(f"✅ Dealer via direct resolution: {dealer}")
            return dealer
        
        dealer = self.schema.resolve_dealer(normalized)
        if dealer:
            logger.debug(f"✅ Dealer via normalized: {dealer}")
            return dealer
        
        # Strategy 2: Pattern extraction
        dealer_match = DEALER_PATTERN.search(original)
        if dealer_match:
            candidate = dealer_match.group(1).strip()
            resolved = self.schema.resolve_dealer(candidate)
            if resolved:
                logger.debug(f"✅ Dealer via pattern '{candidate}': {resolved}")
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
                            logger.debug(f"✅ Dealer via word combo '{candidate}': {resolved}")
                            return resolved
        
        # Strategy 4: Single words
        for word in words:
            if len(word) >= 3:
                resolved = self.schema.resolve_dealer(word)
                if resolved:
                    logger.debug(f"✅ Dealer via word '{word}': {resolved}")
                    return resolved
        
        # Strategy 5: Context
        if context and context.get('last_dealer'):
            follow_up = ['revenue', 'units', 'performance', 'aging', 'pending', 'pod', 'pgi']
            if any(kw in normalized for kw in follow_up):
                logger.debug(f"✅ Dealer via context: {context['last_dealer']}")
                return context['last_dealer']
        
        # Strategy 6: SchemaService search_entities() - NEW
        try:
            search_results = self.schema.search_entities(original)
            if search_results.get('matching_dealers'):
                matched = search_results['matching_dealers'][0]
                logger.debug(f"✅ Dealer via search_entities: {matched}")
                return matched
        except Exception as e:
            logger.debug(f"Search entities failed: {e}")
        
        logger.debug("❌ No dealer detected")
        return None
    
    def _detect_city(self, original: str, normalized: str) -> Optional[str]:
        """
        Detect city from query.
        
        Uses SchemaService v7.1 resolve_city() as primary strategy.
        """
        logger.debug(f"Detecting city in: '{original}'")
        
        # Direct SchemaService resolution
        city = self.schema.resolve_city(original)
        if city:
            logger.debug(f"✅ City via direct: {city}")
            return city
        
        city = self.schema.resolve_city(normalized)
        if city:
            logger.debug(f"✅ City via normalized: {city}")
            return city
        
        # Word matching
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_city(word)
                if resolved:
                    logger.debug(f"✅ City via word '{word}': {resolved}")
                    return resolved
        
        # Search entities fallback
        try:
            search_results = self.schema.search_entities(original)
            if search_results.get('matching_cities'):
                matched = search_results['matching_cities'][0]
                logger.debug(f"✅ City via search_entities: {matched}")
                return matched
        except Exception as e:
            logger.debug(f"Search entities failed: {e}")
        
        logger.debug("❌ No city detected")
        return None
    
    def _detect_warehouse(self, original: str, normalized: str) -> Optional[str]:
        """
        Detect warehouse from query.
        
        Uses SchemaService v7.1 resolve_warehouse() as primary strategy.
        """
        logger.debug(f"Detecting warehouse in: '{original}'")
        
        # Direct SchemaService resolution
        warehouse = self.schema.resolve_warehouse(original)
        if warehouse:
            logger.debug(f"✅ Warehouse via direct: {warehouse}")
            return warehouse
        
        warehouse = self.schema.resolve_warehouse(normalized)
        if warehouse:
            logger.debug(f"✅ Warehouse via normalized: {warehouse}")
            return warehouse
        
        # Word matching
        words = normalized.split()
        for word in words:
            if len(word) >= 2:
                resolved = self.schema.resolve_warehouse(word)
                if resolved:
                    logger.debug(f"✅ Warehouse via word '{word}': {resolved}")
                    return resolved
        
        # Search entities fallback
        try:
            search_results = self.schema.search_entities(original)
            if search_results.get('matching_warehouses'):
                matched = search_results['matching_warehouses'][0]
                logger.debug(f"✅ Warehouse via search_entities: {matched}")
                return matched
        except Exception as e:
            logger.debug(f"Search entities failed: {e}")
        
        logger.debug("❌ No warehouse detected")
        return None
    
    def _detect_intent(self, normalized: str, original: str) -> Optional[Tuple[str, float, bool]]:
        """
        Detect intent from query.
        
        Uses SchemaService v7.1 detect_intent() and detect_metric().
        
        Returns:
            Tuple of (intent, confidence, needs_groq)
        """
        logger.debug(f"Detecting intent in: '{normalized}'")
        
        # ==========================================================
        # Use SchemaService v7.1 detect_intent()
        # ==========================================================
        
        schema_intent, schema_confidence = self.schema.detect_intent(original)
        if schema_intent and schema_confidence >= 0.60:
            logger.debug(f"✅ SchemaService intent: {schema_intent} (confidence={schema_confidence:.2f})")
            # FIX: Executive intents need analytics first, then Groq enrichment
            executive_intents = ['executive_insight', 'root_cause', 'control_tower']
            if schema_intent in executive_intents:
                return (schema_intent, schema_confidence, True)  # needs_groq=True for enrichment
            return (schema_intent, schema_confidence, False)
        
        # ==========================================================
        # KPI INTENTS
        # ==========================================================
        
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
                    logger.debug(f"✅ KPI intent: {intent}")
                    return (intent, 0.95, False)
        
        # ==========================================================
        # RANKING INTENTS
        # ==========================================================
        
        if 'top dealer' in normalized or 'top dealers' in normalized:
            if 'revenue' in normalized or 'sales' in normalized:
                return ("top_dealers_revenue", 0.90, False)
            if 'unit' in normalized or 'quantity' in normalized:
                return ("top_dealers_units", 0.90, False)
            return ("top_dealers", 0.85, False)
        
        if 'bottom dealer' in normalized or 'worst dealer' in normalized:
            return ("bottom_dealers", 0.85, False)
        
        if 'top city' in normalized or 'best city' in normalized:
            return ("top_cities", 0.85, False)
        
        if 'top warehouse' in normalized or 'best warehouse' in normalized:
            return ("top_warehouses", 0.85, False)
        
        # ==========================================================
        # EXECUTIVE INTENTS (FIX: needs_groq=True for enrichment)
        # ==========================================================
        
        executive_patterns = {
            'executive_insight': ['executive insight', 'executive summary', 'management report'],
            'root_cause': ['root cause', 'why delayed', 'why aging', 'what is the issue'],
            'control_tower': ['control tower', 'critical alert', 'critical delivery'],
            'delivery_performance': ['delivery performance', 'delivery kpi', 'delivery rate']
        }
        
        for intent, patterns in executive_patterns.items():
            for pattern in patterns:
                if pattern in normalized:
                    logger.debug(f"✅ Executive intent: {intent}")
                    return (intent, 0.90, True)  # ← FIX: needs_groq=True for enrichment
        
        # ==========================================================
        # COMPARISON & TREND
        # ==========================================================
        
        if 'compare' in normalized or 'vs' in normalized or 'versus' in normalized:
            return ("comparison", 0.80, True)
        
        if 'trend' in normalized or 'over time' in normalized or 'historical' in normalized:
            return ("trend", 0.80, True)
        
        # ==========================================================
        # HELPER INTENT
        # ==========================================================
        
        if 'help' in normalized or 'menu' in normalized or 'commands' in normalized:
            return ("help", 0.95, True)
        
        logger.debug("❌ No intent detected")
        return None
    
    def _determine_dealer_intent(self, normalized: str) -> str:
        """Determine dealer intent based on query."""
        if 'dns' in normalized or 'orders' in normalized:
            return "dealer_dns"
        if 'revenue' in normalized or 'sales' in normalized:
            return "dealer_revenue"
        if 'units' in normalized or 'quantity' in normalized:
            return "dealer_units"
        if 'performance' in normalized or 'kpi' in normalized:
            return "dealer_performance"
        if 'aging' in normalized or 'delay' in normalized or 'pending' in normalized:
            return "dealer_aging"
        return "dealer_dashboard"
    
    def _determine_service_for_intent(self, intent: str) -> str:
        """Determine service based on intent."""
        kpi_intents = ['pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging', 'delivery_aging']
        
        if intent in kpi_intents:
            return "kpi"
        return "analytics"
    
    def _is_help_query(self, normalized: str) -> bool:
        """Check if query is a help request."""
        patterns = ['help', 'menu', 'commands', 'what can you do', 'available commands']
        return any(pattern in normalized for pattern in patterns)
    
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
        
        # Return detailed diagnostic
        result = decision.to_dict()
        
        # Add additional debug info from SchemaService
        result["debug"] = {
            "dealer_check": self.schema.resolve_dealer(question),
            "city_check": self.schema.resolve_city(question),
            "warehouse_check": self.schema.resolve_warehouse(question),
            "dn_check": self.schema.is_dn_number(question),
            "normalized": self._normalize(question),
            "schema_health": self.schema.get_health_report(),
            "all_dealers": list(self.schema.dealers.values())[:10],
            "all_cities": list(self.schema.cities.values())[:10],
            "all_warehouses": list(self.schema.warehouses.values())[:10]
        }
        
        return result
    
    def debug_entity(self, name: str) -> Dict[str, Any]:
        """
        Debug entity resolution for a given name.
        
        Args:
            name: Entity name to debug
            
        Returns:
            Dict with entity resolution results
        """
        return {
            "name": name,
            "dealer": self.schema.resolve_dealer(name),
            "city": self.schema.resolve_city(name),
            "warehouse": self.schema.resolve_warehouse(name),
            "unified": self.schema.resolve_entity(name),
            "dealer_debug": self.schema.find_dealer_debug(name),
            "city_debug": self.schema.find_city_debug(name),
            "warehouse_debug": self.schema.find_warehouse_debug(name),
            "schema_health": self.schema.get_health_report()
        }
    
    def get_schema_stats(self) -> Dict[str, Any]:
        """
        Get SchemaService statistics for metadata endpoint.
        
        Returns:
            Dict with metadata statistics
        """
        return {
            "dealers": len(self.schema.dealers),
            "cities": len(self.schema.cities),
            "warehouses": len(self.schema.warehouses),
            "health_score": self.schema._health_score,
            "initialized": self.schema._initialized,
            "database_connected": self.schema._db_connected,
            "last_refresh": self.schema._last_refresh.isoformat() if self.schema._last_refresh else None,
            "status": "healthy" if self.schema._health_score >= 70 else "warning" if self.schema._health_score >= 50 else "critical"
        }
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        total = sum(self._routing_stats.values())
        return {
            "total_routing_attempts": total,
            "dn_lookups": self._routing_stats["dn_lookups"],
            "dealer_resolutions": self._routing_stats["dealer_resolutions"],
            "city_resolutions": self._routing_stats["city_resolutions"],
            "warehouse_resolutions": self._routing_stats["warehouse_resolutions"],
            "intent_detections": self._routing_stats["intent_detections"],
            "groq_fallbacks": self._routing_stats["groq_fallbacks"],
            "help_requests": self._routing_stats["help_requests"],
            "success_rate": (self._routing_stats["dn_lookups"] + 
                           self._routing_stats["dealer_resolutions"] + 
                           self._routing_stats["city_resolutions"] + 
                           self._routing_stats["warehouse_resolutions"] + 
                           self._routing_stats["intent_detections"]) / max(1, total) * 100,
            "version": "7.2",
            "schema_version": "7.1",
            "schema_health": self.schema.get_health_report()
        }
    
    def get_schema_health(self) -> Dict[str, Any]:
        """Get SchemaService health report."""
        return self.schema.get_health_report()


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
                    logger.info("✅ AIQueryService singleton initialized successfully")
                except Exception as e:
                    logger.exception(f"❌ AIQueryService singleton initialization failed: {e}")
                    raise
    
    return _ai_query_service


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'AIQueryService',
    'RoutingDecision',
    'get_ai_query_service',
    'DEALER_PATTERN',
    'RANKING_LIMIT_PATTERN',
    'WHITESPACE_PATTERN',
    'SPECIAL_CHARS_PATTERN'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.debug("=" * 70)
logger.debug("AIQueryService v7.2 - Production Fix")
logger.debug("=" * 70)
logger.debug("")
logger.debug("   FIXES APPLIED:")
logger.debug("   ✅ Startup Validation - Checks metadata on init")
logger.debug("   ✅ Router Diagnostics - Full logging of all detections")
logger.debug("   ✅ Executive Routing - Analytics first, Groq enrichment")
logger.debug("   ✅ Startup Logging - Shows loaded entities count")
logger.debug("   ✅ Metadata Endpoint Support - get_schema_stats()")
logger.debug("   ✅ Entity Debug Support - debug_entity() method")
logger.debug("")
logger.debug("   ROUTING PRIORITY:")
logger.debug("   1️⃣ DN Lookup → analytics")
logger.debug("   2️⃣ Dealer Resolution → analytics")
logger.debug("   3️⃣ City Resolution → analytics")
logger.debug("   4️⃣ Warehouse Resolution → analytics")
logger.debug("   5️⃣ Intent Detection → analytics/kpi/groq")
logger.debug("   6️⃣ Groq (LAST RESORT) → groq")
logger.debug("   7️⃣ Help → help")
logger.debug("")
logger.debug("   STATUS: ✅ PRODUCTION READY")
logger.debug("=" * 70)
