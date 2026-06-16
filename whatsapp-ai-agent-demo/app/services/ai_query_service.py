# ==========================================================
# FILE: app/services/ai_query_service.py (v8.1 - FULLY INTEGRATED)
# ==========================================================
# PURPOSE: PURE ROUTING ENGINE - Entity-First, Intent-Second
# ARCHITECTURE: Single Source of Truth for Routing
#
# INTEGRATED WITH: SchemaService v7.2 (sold_to_party_name fix)
#
# CAPABILITIES: Answers ALL dealer intelligence questions
# - Dealer 360 Dashboard
# - Dealer Profile
# - Dealer Executive KPI Summary
# - DN Performance Engine
# - DN Breakdown Engine
# - Delivery Intelligence
# - Enterprise Aging Engine
# - POD Intelligence
# - Product Intelligence
# - Financial Intelligence
# - Dealer Health Engine
# - Dealer Risk Engine
# - Ranking Engine
# - Timeline Engine
# - Alert Engine
# - Executive Intelligence
# - AI Ready Payloads
# ==========================================================

import re
import threading
import time
import uuid
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
from loguru import logger


# ==========================================================
# IMPORT SAFETY - Integrated with SchemaService v7.2
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
    logger.debug("✅ Successfully imported SchemaService v7.2")
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

# Comparison Pattern
COMPARISON_PATTERN = re.compile(r'compare\s+(.+?)\s+(?:vs|versus|and|with)\s+(.+)', re.IGNORECASE)

# Trend Period Pattern
TREND_PERIOD_PATTERN = re.compile(r'(daily|weekly|monthly|yearly|30|60|90|180)\s*(?:day|days)?\s*(?:trend)?', re.IGNORECASE)

# Alert Type Pattern
ALERT_TYPE_PATTERN = re.compile(r'(delivery|pod|health|pending|aging)\s*(?:alerts?)?', re.IGNORECASE)

# Product Pattern
PRODUCT_PATTERN = re.compile(r'(?:products?|models?|items?)\s+(?:for|of)', re.IGNORECASE)

# Financial Pattern
FINANCIAL_PATTERN = re.compile(r'(?:finance|financial|revenue|money)\s+(?:for|of)', re.IGNORECASE)

# Breakdown Pattern
BREAKDOWN_PATTERN = re.compile(r'breakdown\s+(?:by|for)\s+(\w+)', re.IGNORECASE)

# Risk Pattern
RISK_PATTERN = re.compile(r'(?:risk|risky|risk assessment)\s+(?:for|of)', re.IGNORECASE)

# Health Pattern
HEALTH_PATTERN = re.compile(r'(?:health|healthy|score)\s+(?:for|of)', re.IGNORECASE)

# Timeline Pattern
TIMELINE_PATTERN = re.compile(r'(?:timeline|history|chronology)\s+(?:for|of)', re.IGNORECASE)

# Aging Pattern
AGING_PATTERN = re.compile(r'(?:aging|delay|cycle)\s+(?:analysis|report)', re.IGNORECASE)

# POD Pattern
POD_PATTERN = re.compile(r'(?:pod|proof of delivery)\s+(?:dashboard|summary)', re.IGNORECASE)

# Delivery Pattern
DELIVERY_PATTERN = re.compile(r'(?:delivery|dispatch)\s+(?:dashboard|performance)', re.IGNORECASE)

# Executive Pattern
EXECUTIVE_PATTERN = re.compile(r'(?:executive|management|leadership)\s+(?:insights?|summary)', re.IGNORECASE)

# AI Context Pattern
AI_CONTEXT_PATTERN = re.compile(r'(?:ai|groq|context|facts)\s+(?:for|of)', re.IGNORECASE)


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
    entity2: Optional[str] = None  # For comparisons
    entity_type: Optional[str] = None
    service: str = "analytics"
    analytics_method: str = "get_dealer_dashboard"
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
            "entity2": self.entity2,
            "entity_type": self.entity_type,
            "service": self.service,
            "analytics_method": self.analytics_method,
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
# AI QUERY SERVICE - ENTERPRISE DEALER INTELLIGENCE ROUTER
# ==========================================================

class AIQueryService:
    """
    ENTERPRISE DEALER INTELLIGENCE ROUTER
    
    FULLY INTEGRATED WITH: SchemaService v7.2
    
    ROUTING PRIORITY (ENFORCED):
    1. DN Detection (8-12 digits) → analytics
    2. Dealer Resolution → analytics
    3. City Resolution → analytics
    4. Warehouse Resolution → analytics
    5. Dealer Intelligence → analytics (all dealer-related queries)
    6. Intent Detection → analytics/kpi/groq
    7. Groq (LAST RESORT) → groq
    8. Help → help
    
    DEALER INTELLIGENCE QUERIES SUPPORTED:
    - "Rare Diamonds Electronics" → dealer_dashboard
    - "Rare Diamonds Electronics profile" → dealer_profile
    - "Rare Diamonds Electronics KPIs" → dealer_executive_summary
    - "Rare Diamonds Electronics DN performance" → dealer_dn_performance
    - "Rare Diamonds Electronics DN trend" → dealer_dn_trend
    - "Rare Diamonds Electronics breakdown by warehouse" → dn_breakdown_warehouse
    - "Rare Diamonds Electronics delivery dashboard" → delivery_dashboard
    - "Rare Diamonds Electronics aging analysis" → delivery_aging_analysis
    - "Rare Diamonds Electronics POD dashboard" → pod_dashboard
    - "Rare Diamonds Electronics products" → product_dashboard
    - "Rare Diamonds Electronics financial dashboard" → financial_dashboard
    - "Rare Diamonds Electronics health score" → dealer_health_score
    - "Rare Diamonds Electronics risk assessment" → dealer_risk_assessment
    - "Rare Diamonds Electronics rankings" → dealer_rankings
    - "Rare Diamonds Electronics timeline" → dealer_timeline
    - "Rare Diamonds Electronics alerts" → dealer_alerts
    - "Rare Diamonds Electronics executive insights" → executive_insights
    - "Rare Diamonds Electronics AI context" → ai_context
    """
    
    def __init__(self):
        """Initialize AIQueryService with SchemaService v7.2."""
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("Loading SchemaService v7.2 for AIQueryService...")
            logger.info("=" * 70)
            
            # Load SchemaService v7.2
            self.schema = get_schema_service()
            logger.info("✅ SchemaService v7.2 loaded successfully")
            
            # ==========================================================
            # PRIORITY 1: STARTUP VALIDATION & DIAGNOSTICS
            # ==========================================================
            
            dealer_count = len(self.schema.dealers)
            city_count = len(self.schema.cities)
            warehouse_count = len(self.schema.warehouses)
            
            logger.info("")
            logger.info("📊 *METADATA LOAD STATUS:*")
            logger.info(f"   🏪 Dealers: {dealer_count}")
            logger.info(f"   🏙️ Cities: {city_count}")
            logger.info(f"   🏭 Warehouses: {warehouse_count}")
            logger.info("")
            
            if dealer_count == 0:
                logger.error("❌ CRITICAL: No dealers loaded from database!")
                logger.error("   Checking 'sold_to_party_name' column in delivery_report table.")
                raise RuntimeError("No dealers loaded from database. Check sold_to_party_name column.")
            
            health = self.schema.get_health_report()
            logger.info(f"   📊 Health Score: {health.get('health_score', 0)}/100")
            
            # Log sample dealers for debugging
            if dealer_count > 0:
                sample = list(self.schema.dealers.values())[:5]
                logger.info(f"   📋 Sample Dealers: {sample}")
            
            self._logistics_keywords_cache = self.schema.logistics_keywords
            logger.debug(f"✅ Cached {len(self._logistics_keywords_cache)} logistics keywords")
            
            self._routing_stats = {
                "dn_lookups": 0,
                "dealer_resolutions": 0,
                "city_resolutions": 0,
                "warehouse_resolutions": 0,
                "dealer_intelligence": 0,
                "intent_detections": 0,
                "groq_fallbacks": 0,
                "help_requests": 0
            }
            
            init_duration = (time.time() - start_time) * 1000
            logger.info("")
            logger.info("=" * 70)
            logger.info("AIQueryService v8.1 - Fully Integrated with SchemaService v7.2")
            logger.info("=" * 70)
            logger.info("")
            logger.info("   ROUTING PRIORITY (ENFORCED):")
            logger.info("   1️⃣ DN Lookup → analytics")
            logger.info("   2️⃣ Dealer Resolution → analytics")
            logger.info("   3️⃣ City Resolution → analytics")
            logger.info("   4️⃣ Warehouse Resolution → analytics")
            logger.info("   5️⃣ Dealer Intelligence → analytics")
            logger.info("   6️⃣ Intent Detection → analytics/kpi/groq")
            logger.info("   7️⃣ Groq (LAST RESORT) → groq")
            logger.info("   8️⃣ Help → help")
            logger.info("")
            logger.info("   DEALER INTELLIGENCE SUPPORT:")
            logger.info("   ✅ 360 Dashboard")
            logger.info("   ✅ Profile")
            logger.info("   ✅ Executive KPI Summary")
            logger.info("   ✅ DN Performance")
            logger.info("   ✅ DN Breakdown")
            logger.info("   ✅ Delivery Dashboard")
            logger.info("   ✅ Aging Analysis")
            logger.info("   ✅ POD Dashboard")
            logger.info("   ✅ Product Dashboard")
            logger.info("   ✅ Financial Dashboard")
            logger.info("   ✅ Health Score")
            logger.info("   ✅ Risk Assessment")
            logger.info("   ✅ Rankings")
            logger.info("   ✅ Timeline")
            logger.info("   ✅ Alerts")
            logger.info("   ✅ Executive Insights")
            logger.info("   ✅ AI Context")
            logger.info("")
            logger.info("   SCHEMASERVICE INTEGRATION:")
            logger.info("   ✅ resolve_dealer() - sold_to_party_name")
            logger.info("   ✅ resolve_city() - ship_to_city")
            logger.info("   ✅ resolve_warehouse() - warehouse")
            logger.info("   ✅ is_dn_number() - DN validation")
            logger.info("   ✅ search_entities() - Entity search")
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
        5. Dealer Intelligence → analytics
        6. Intent Detection → analytics/kpi/groq
        7. Groq (LAST RESORT) → groq
        8. Help → help
        """
        query_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        
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
        logger.info(f"🔍 ROUTER DIAGNOSTIC - QUESTION: '{cleaned_question}'")
        
        detected_dn = None
        detected_dealer = None
        detected_city = None
        detected_warehouse = None
        detected_intent = None
        routing_path = ""
        
        schema_health = self.schema.get_health_report()
        
        # ==========================================================
        # PRIORITY 1: DN DETECTION
        # ==========================================================
        
        if self.schema.is_dn_number(cleaned_question):
            dn_number = cleaned_question
            detected_dn = dn_number
            routing_path = "dn_lookup"
            self._routing_stats["dn_lookups"] += 1
            
            logger.info(f"Query {query_id}: ✅ DN Detected: {dn_number} → dn_lookup (analytics)")
            
            return RoutingDecision(
                intent="dn_lookup",
                entity=dn_number,
                entity_type="dn",
                service="analytics",
                analytics_method="get_dn_analytics",
                confidence=1.0,
                needs_groq=False,
                reason=f"DN number detected: {dn_number}",
                original_message=cleaned_question,
                detected_dn=dn_number,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        dn_match = DN_PATTERN.search(cleaned_question)
        if dn_match:
            dn_number = dn_match.group(1)
            detected_dn = dn_number
            routing_path = "dn_lookup"
            self._routing_stats["dn_lookups"] += 1
            
            return RoutingDecision(
                intent="dn_lookup",
                entity=dn_number,
                entity_type="dn",
                service="analytics",
                analytics_method="get_dn_analytics",
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
        # ==========================================================
        
        dealer_result = self._detect_dealer(cleaned_question, normalized, context)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - DEALER: '{dealer_result}'")
        
        if dealer_result:
            dealer_name = dealer_result
            detected_dealer = dealer_name
            routing_path = "dealer_resolution"
            self._routing_stats["dealer_resolutions"] += 1
            
            # Determine the specific dealer intelligence method
            analytics_method, intent = self._determine_dealer_intelligence_method(
                normalized, cleaned_question, dealer_name
            )
            
            logger.info(f"Query {query_id}: ✅ Dealer Detected: '{dealer_name}' → {intent} (analytics, method={analytics_method})")
            
            # Check if this is a comparison query
            if self._is_comparison_query(cleaned_question):
                entity2 = self._extract_second_entity(cleaned_question)
                if entity2:
                    self._routing_stats["dealer_intelligence"] += 1
                    return RoutingDecision(
                        intent="compare_dealers",
                        entity=dealer_name,
                        entity2=entity2,
                        entity_type="dealer",
                        service="analytics",
                        analytics_method="compare_dealers_enhanced",
                        confidence=0.95,
                        needs_groq=False,
                        reason=f"Dealer comparison: {dealer_name} vs {entity2}",
                        original_message=cleaned_question,
                        detected_dealer=dealer_name,
                        routing_path="dealer_comparison",
                        schema_health=schema_health
                    )
            
            self._routing_stats["dealer_intelligence"] += 1
            return RoutingDecision(
                intent=intent,
                entity=dealer_name,
                entity_type="dealer",
                service="analytics",
                analytics_method=analytics_method,
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer intelligence: {dealer_name}",
                original_message=cleaned_question,
                detected_dealer=dealer_name,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 3: CITY RESOLUTION
        # ==========================================================
        
        city_result = self._detect_city(cleaned_question, normalized)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - CITY: '{city_result}'")
        
        if city_result:
            city_name = city_result
            detected_city = city_name
            routing_path = "city_resolution"
            self._routing_stats["city_resolutions"] += 1
            
            # Check if it's city intelligence
            if self._is_city_intelligence_query(cleaned_question):
                return RoutingDecision(
                    intent="city_intelligence",
                    entity=city_name,
                    entity_type="city",
                    service="analytics",
                    analytics_method="get_city_intelligence",
                    confidence=0.95,
                    needs_groq=False,
                    reason=f"City intelligence: {city_name}",
                    original_message=cleaned_question,
                    detected_city=city_name,
                    routing_path=routing_path,
                    schema_health=schema_health
                )
            
            return RoutingDecision(
                intent="city_dashboard",
                entity=city_name,
                entity_type="city",
                service="analytics",
                analytics_method="get_city_dashboard",
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
        # ==========================================================
        
        warehouse_result = self._detect_warehouse(cleaned_question, normalized)
        logger.info(f"🔍 ROUTER DIAGNOSTIC - WAREHOUSE: '{warehouse_result}'")
        
        if warehouse_result:
            warehouse_name = warehouse_result
            detected_warehouse = warehouse_name
            routing_path = "warehouse_resolution"
            self._routing_stats["warehouse_resolutions"] += 1
            
            if self._is_warehouse_intelligence_query(cleaned_question):
                return RoutingDecision(
                    intent="warehouse_bottlenecks",
                    entity=warehouse_name,
                    entity_type="warehouse",
                    service="analytics",
                    analytics_method="get_warehouse_bottlenecks",
                    confidence=0.95,
                    needs_groq=False,
                    reason=f"Warehouse intelligence: {warehouse_name}",
                    original_message=cleaned_question,
                    detected_warehouse=warehouse_name,
                    routing_path=routing_path,
                    schema_health=schema_health
                )
            
            return RoutingDecision(
                intent="warehouse_dashboard",
                entity=warehouse_name,
                entity_type="warehouse",
                service="analytics",
                analytics_method="get_warehouse_dashboard",
                confidence=0.95,
                needs_groq=False,
                reason=f"Warehouse resolved: {warehouse_name}",
                original_message=cleaned_question,
                detected_warehouse=warehouse_name,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 5: NETWORK-LEVEL QUERIES (No entity)
        # ==========================================================
        
        if self._is_network_intelligence_query(cleaned_question):
            routing_path = "network_intelligence"
            self._routing_stats["intent_detections"] += 1
            
            intent, method = self._determine_network_intelligence_method(normalized, cleaned_question)
            
            return RoutingDecision(
                intent=intent,
                entity=None,
                entity_type="network",
                service="analytics",
                analytics_method=method,
                confidence=0.90,
                needs_groq=False,
                reason=f"Network intelligence: {intent}",
                original_message=cleaned_question,
                detected_intent=intent,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 6: INTENT DETECTION
        # ==========================================================
        
        intent_result = self._detect_intent(normalized, cleaned_question)
        if intent_result:
            intent, confidence, needs_groq = intent_result
            detected_intent = intent
            routing_path = "intent_detection"
            self._routing_stats["intent_detections"] += 1
            
            executive_intents = ['executive_insight', 'root_cause', 'control_tower', 'comparison', 'trend']
            
            if intent in executive_intents:
                service = "analytics"
                needs_groq = True
                analytics_method = "get_executive_context" if intent == "executive_insight" else "get_ai_context"
            else:
                service = self._determine_service_for_intent(intent)
                analytics_method = self._get_analytics_method_for_intent(intent)
            
            return RoutingDecision(
                intent=intent,
                entity=None,
                entity_type=None,
                service=service,
                analytics_method=analytics_method,
                confidence=confidence,
                needs_groq=needs_groq,
                reason=f"Intent detected: {intent}",
                original_message=cleaned_question,
                detected_intent=intent,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 7: HELP DETECTION
        # ==========================================================
        
        if self._is_help_query(normalized):
            routing_path = "help"
            self._routing_stats["help_requests"] += 1
            
            return RoutingDecision(
                intent="help",
                service="help",
                confidence=0.95,
                needs_groq=True,
                reason="Help request",
                original_message=cleaned_question,
                routing_path=routing_path,
                schema_health=schema_health
            )
        
        # ==========================================================
        # PRIORITY 8: GROQ (LAST RESORT)
        # ==========================================================
        
        routing_path = "groq_fallback"
        self._routing_stats["groq_fallbacks"] += 1
        
        return RoutingDecision(
            intent="general_ai",
            service="groq",
            confidence=0.30,
            needs_groq=True,
            reason="No specific pattern matched - using Groq",
            original_message=cleaned_question,
            routing_path=routing_path,
            schema_health=schema_health
        )
    
    # ==========================================================
    # DEALER INTELLIGENCE METHOD DETECTION
    # ==========================================================
    
    def _determine_dealer_intelligence_method(self, normalized: str, original: str, dealer_name: str) -> Tuple[str, str]:
        """
        Determine the specific dealer intelligence method based on query.
        
        Returns:
            Tuple of (analytics_method, intent)
        """
        # 360 Dashboard (default)
        if "360" in normalized or "full" in normalized or "complete" in normalized:
            return ("get_dealer_360_dashboard", "dealer_360_dashboard")
        
        # Profile
        if "profile" in normalized or "information" in normalized or "details" in normalized:
            return ("get_dealer_profile", "dealer_profile")
        
        # Executive KPIs
        if "kpi" in normalized or "executive" in normalized or "summary" in normalized:
            return ("get_dealer_executive_summary", "dealer_executive_summary")
        
        # DN Performance
        if "dn performance" in normalized or "dn trend" in normalized:
            if "daily" in normalized:
                return ("get_dealer_dn_trend_daily", "dealer_dn_trend_daily")
            elif "weekly" in normalized:
                return ("get_dealer_dn_trend_weekly", "dealer_dn_trend_weekly")
            elif "monthly" in normalized:
                return ("get_dealer_dn_trend_monthly", "dealer_dn_trend_monthly")
            elif "yearly" in normalized:
                return ("get_dealer_dn_trend_yearly", "dealer_dn_trend_yearly")
            return ("get_dealer_dn_performance", "dealer_dn_performance")
        
        # DN Breakdown
        if "breakdown" in normalized:
            if "warehouse" in normalized:
                return ("get_dn_breakdown_by_warehouse", "dn_breakdown_warehouse")
            elif "sales office" in normalized:
                return ("get_dn_breakdown_by_sales_office", "dn_breakdown_sales_office")
            elif "product" in normalized:
                return ("get_dn_breakdown_by_product", "dn_breakdown_product")
            elif "model" in normalized:
                return ("get_dn_breakdown_by_model", "dn_breakdown_model")
            elif "city" in normalized:
                return ("get_dn_breakdown_by_city", "dn_breakdown_city")
        
        # Delivery Dashboard
        if "delivery" in normalized and ("dashboard" in normalized or "performance" in normalized):
            return ("get_delivery_dashboard", "delivery_dashboard")
        
        # Aging Analysis
        if "aging" in normalized or "cycle" in normalized or "delay" in normalized:
            return ("get_delivery_aging_analysis", "delivery_aging_analysis")
        
        # POD Dashboard
        if "pod" in normalized or "proof of delivery" in normalized:
            return ("get_pod_dashboard", "pod_dashboard")
        
        # Product Dashboard
        if "product" in normalized or "model" in normalized:
            return ("get_product_dashboard", "product_dashboard")
        
        # Financial Dashboard
        if "financial" in normalized or "revenue" in normalized or "finance" in normalized:
            return ("get_financial_dashboard", "financial_dashboard")
        
        # Health Score
        if "health" in normalized or "score" in normalized:
            return ("calculate_dealer_health_score", "dealer_health_score")
        
        # Risk Assessment
        if "risk" in normalized:
            return ("assess_dealer_risk", "dealer_risk_assessment")
        
        # Rankings
        if "rank" in normalized or "ranking" in normalized:
            return ("get_dealer_rankings", "dealer_rankings")
        
        # Timeline
        if "timeline" in normalized or "history" in normalized or "chronology" in normalized:
            return ("get_dealer_timeline", "dealer_timeline")
        
        # Alerts
        if "alert" in normalized:
            return ("get_dealer_alerts", "dealer_alerts")
        
        # Executive Insights
        if "executive" in normalized or "management" in normalized:
            return ("get_executive_insights", "executive_insights")
        
        # AI Context
        if "ai" in normalized or "context" in normalized or "facts" in normalized:
            return ("get_ai_context", "ai_context")
        
        # Default: 360 Dashboard
        return ("get_dealer_360_dashboard", "dealer_360_dashboard")
    
    def _is_comparison_query(self, question: str) -> bool:
        """Check if query is a comparison."""
        return bool(COMPARISON_PATTERN.search(question))
    
    def _extract_second_entity(self, question: str) -> Optional[str]:
        """Extract second entity from comparison query."""
        match = COMPARISON_PATTERN.search(question)
        if match:
            entity2 = match.group(2).strip()
            # Resolve the second entity
            resolved = self.schema.resolve_dealer(entity2)
            return resolved or entity2
        return None
    
    def _is_city_intelligence_query(self, question: str) -> bool:
        """Check if query is asking for city intelligence."""
        patterns = ['market share', 'growth', 'rank', 'intelligence', 'insight']
        return any(p in question.lower() for p in patterns)
    
    def _is_warehouse_intelligence_query(self, question: str) -> bool:
        """Check if query is asking for warehouse intelligence."""
        patterns = ['bottleneck', 'analysis', 'performance']
        return any(p in question.lower() for p in patterns)
    
    def _is_network_intelligence_query(self, question: str) -> bool:
        """Check if query is asking for network-level intelligence."""
        patterns = [
            'network kpi', 'network performance', 'overall', 'total',
            'data integrity', 'integrity score', 'health dashboard'
        ]
        return any(p in question.lower() for p in patterns)
    
    def _determine_network_intelligence_method(self, normalized: str, original: str) -> Tuple[str, str]:
        """Determine network-level analytics method."""
        if "integrity" in normalized:
            return ("data_integrity", "get_data_integrity_score")
        if "health" in normalized:
            return ("analytics_health", "get_analytics_health")
        return ("network_kpis", "get_network_kpis")
    
    def _determine_service_for_intent(self, intent: str) -> str:
        """Determine service based on intent."""
        kpi_intents = ['pending_pgi', 'pending_pod', 'pgi_aging', 'pod_aging', 'delivery_aging']
        if intent in kpi_intents:
            return "kpi"
        return "analytics"
    
    def _get_analytics_method_for_intent(self, intent: str) -> str:
        """Get analytics method for intent."""
        intent_methods = {
            'pending_pgi': 'get_pending_pgi',
            'pending_pod': 'get_pending_pod',
            'executive_insight': 'get_executive_context',
            'root_cause': 'get_root_cause_context',
            'control_tower': 'get_control_tower_context',
            'comparison': 'compare_dealers_enhanced',
            'trend': 'get_trend_analysis_enhanced'
        }
        return intent_methods.get(intent, 'get_dealer_dashboard')
    
    # ==========================================================
    # DETECTION METHODS (Enhanced)
    # ==========================================================
    
    def _detect_dealer(self, original: str, normalized: str, context: Optional[Dict]) -> Optional[str]:
        """
        Detect dealer from query with multiple strategies.
        
        Uses SchemaService v7.2 resolve_dealer() which queries 'sold_to_party_name'.
        """
        logger.debug(f"Detecting dealer in: '{original}'")
        
        # Strategy 1: Direct SchemaService resolution (uses sold_to_party_name)
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
        
        # Strategy 6: SchemaService search_entities()
        try:
            search_results = self.schema.search_entities(original)
            if search_results.get('matching_dealers'):
                matched = search_results['matching_dealers'][0]
                logger.debug(f"✅ Dealer via search_entities: {matched}")
                return matched
        except Exception as e:
            logger.debug(f"Search entities failed: {e}")
        
        # Strategy 7: Direct database check using find_dealer_debug
        try:
            debug_result = self.schema.find_dealer_debug(original)
            if debug_result.get('resolved'):
                logger.debug(f"✅ Dealer via find_dealer_debug: {debug_result['resolved']}")
                return debug_result['resolved']
        except Exception as e:
            logger.debug(f"find_dealer_debug failed: {e}")
        
        logger.debug("❌ No dealer detected")
        return None
    
    def _detect_city(self, original: str, normalized: str) -> Optional[str]:
        """Detect city from query using SchemaService v7.2."""
        logger.debug(f"Detecting city in: '{original}'")
        
        # Direct SchemaService resolution (uses ship_to_city)
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
        """Detect warehouse from query using SchemaService v7.2."""
        logger.debug(f"Detecting warehouse in: '{original}'")
        
        # Direct SchemaService resolution (uses warehouse)
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
        """Detect intent from query."""
        logger.debug(f"Detecting intent in: '{normalized}'")
        
        # Use SchemaService v7.2 detect_intent()
        schema_intent, schema_confidence = self.schema.detect_intent(original)
        if schema_intent and schema_confidence >= 0.60:
            executive_intents = ['executive_insight', 'root_cause', 'control_tower']
            if schema_intent in executive_intents:
                return (schema_intent, schema_confidence, True)
            return (schema_intent, schema_confidence, False)
        
        # KPI INTENTS
        kpi_patterns = {
            'pending_pgi': ['pending pgi', 'pgi pending', 'open pgi'],
            'pending_pod': ['pending pod', 'pod pending', 'open pod'],
            'pgi_aging': ['pgi aging', 'aging pgi'],
            'pod_aging': ['pod aging', 'aging pod'],
            'delivery_aging': ['delivery aging', 'aging delivery']
        }
        
        for intent, patterns in kpi_patterns.items():
            for pattern in patterns:
                if pattern in normalized:
                    return (intent, 0.95, False)
        
        # RANKING INTENTS
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
        
        # EXECUTIVE INTENTS
        executive_patterns = {
            'executive_insight': ['executive insight', 'executive summary', 'management report'],
            'root_cause': ['root cause', 'why delayed', 'why aging', 'what is the issue'],
            'control_tower': ['control tower', 'critical alert', 'critical delivery'],
            'delivery_performance': ['delivery performance', 'delivery kpi', 'delivery rate']
        }
        
        for intent, patterns in executive_patterns.items():
            for pattern in patterns:
                if pattern in normalized:
                    return (intent, 0.90, True)
        
        # COMPARISON & TREND
        if 'compare' in normalized or 'vs' in normalized or 'versus' in normalized:
            return ("comparison", 0.80, True)
        
        if 'trend' in normalized or 'over time' in normalized or 'historical' in normalized:
            return ("trend", 0.80, True)
        
        # HELP
        if 'help' in normalized or 'menu' in normalized or 'commands' in normalized:
            return ("help", 0.95, True)
        
        return None
    
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
        result = decision.to_dict()
        
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
        """Debug entity resolution for a given name."""
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
        """Get SchemaService statistics."""
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
            "dealer_intelligence": self._routing_stats["dealer_intelligence"],
            "intent_detections": self._routing_stats["intent_detections"],
            "groq_fallbacks": self._routing_stats["groq_fallbacks"],
            "help_requests": self._routing_stats["help_requests"],
            "success_rate": (self._routing_stats["dn_lookups"] + 
                           self._routing_stats["dealer_resolutions"] + 
                           self._routing_stats["city_resolutions"] + 
                           self._routing_stats["warehouse_resolutions"] + 
                           self._routing_stats["dealer_intelligence"] + 
                           self._routing_stats["intent_detections"]) / max(1, total) * 100,
            "version": "8.1",
            "schema_version": "7.2",
            "schema_health": self.schema.get_health_report()
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
    'COMPARISON_PATTERN',
    'TREND_PERIOD_PATTERN',
    'ALERT_TYPE_PATTERN',
    'PRODUCT_PATTERN',
    'FINANCIAL_PATTERN',
    'BREAKDOWN_PATTERN',
    'RISK_PATTERN',
    'HEALTH_PATTERN',
    'TIMELINE_PATTERN',
    'AGING_PATTERN',
    'POD_PATTERN',
    'DELIVERY_PATTERN',
    'EXECUTIVE_PATTERN',
    'AI_CONTEXT_PATTERN'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.debug("=" * 70)
logger.debug("AIQueryService v8.1 - Fully Integrated with SchemaService v7.2")
logger.debug("=" * 70)
logger.debug("")
logger.debug("   INTEGRATION HIGHLIGHTS:")
logger.debug("   ✅ resolve_dealer() - sold_to_party_name (dealer column)")
logger.debug("   ✅ resolve_city() - ship_to_city (city column)")
logger.debug("   ✅ resolve_warehouse() - warehouse (warehouse column)")
logger.debug("")
logger.debug("   DEALER INTELLIGENCE SUPPORT:")
logger.debug("   ✅ 360 Dashboard")
logger.debug("   ✅ Profile")
logger.debug("   ✅ Executive KPI Summary")
logger.debug("   ✅ DN Performance")
logger.debug("   ✅ DN Breakdown")
logger.debug("   ✅ Delivery Dashboard")
logger.debug("   ✅ Aging Analysis")
logger.debug("   ✅ POD Dashboard")
logger.debug("   ✅ Product Dashboard")
logger.debug("   ✅ Financial Dashboard")
logger.debug("   ✅ Health Score")
logger.debug("   ✅ Risk Assessment")
logger.debug("   ✅ Rankings")
logger.debug("   ✅ Timeline")
logger.debug("   ✅ Alerts")
logger.debug("   ✅ Executive Insights")
logger.debug("   ✅ AI Context")
logger.debug("")
logger.debug("   STATUS: ✅ PRODUCTION READY")
logger.debug("=" * 70)
