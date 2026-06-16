# ==========================================================
# FILE: app/services/ai_query_service.py (v5.0 - SINGLE SOURCE OF ROUTING TRUTH)
# ==========================================================
# ROLE: AI Routing Engine - Brain of the Platform
# PURPOSE: Deterministic routing with clear priority order
# 
# ARCHITECTURE RULES:
# 1. This file is the SINGLE SOURCE OF ROUTING TRUTH
# 2. Routing is DETERMINISTIC - no guessing
# 3. Priority order is ENFORCED
# 4. SchemaService provides all metadata
# 5. Groq is LAST RESORT
# 6. Context intelligence for follow-ups
# 7. Every query returns standardized QueryPlan
# ==========================================================

import re
from typing import Optional, Dict, Any, List, Tuple
from loguru import logger

# ==========================================================
# LAZY IMPORTS - Break circular dependencies
# ==========================================================

def _get_schema_service():
    from app.schemas.schema_service import get_schema_service, DN_PATTERN
    return get_schema_service(), DN_PATTERN

# ==========================================================
# DN PATTERN (Local copy for immediate detection)
# ==========================================================

DN_PATTERN = re.compile(r'\b(\d{8,12})\b')

# ==========================================================
# QUERY PLAN STANDARD
# ==========================================================

class QueryPlan:
    """
    Standardized routing decision.
    
    Every query returns exactly this structure.
    """
    
    def __init__(
        self,
        intent: str,
        entity: Optional[str] = None,
        entity_type: Optional[str] = None,
        service: str = "analytics",
        confidence: float = 0.0,
        needs_groq: bool = False,
        reason: str = "",
        original_message: str = ""
    ):
        self.intent = intent
        self.entity = entity
        self.entity_type = entity_type
        self.service = service
        self.confidence = confidence
        self.needs_groq = needs_groq
        self.reason = reason
        self.original_message = original_message
    
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
        return f"QueryPlan(intent={self.intent}, entity={self.entity}, service={self.service})"


# ==========================================================
# AI QUERY SERVICE - SINGLE SOURCE OF ROUTING TRUTH
# ==========================================================

class AIQueryService:
    """
    AI Routing Engine - Brain of the Platform
    
    SINGLE SOURCE OF ROUTING TRUTH
    
    Priority Order (DETERMINISTIC):
    1. DN Lookup (8-12 digits) - HIGHEST PRIORITY
    2. Dealer Resolution (SchemaService)
    3. Warehouse Resolution (SchemaService)
    4. City Resolution (SchemaService)
    5. KPI Detection
    6. Ranking Detection
    7. Executive / Root Cause Detection
    8. Intent Detection (General)
    9. Groq (LAST RESORT)
    
    ARCHITECTURE BOUNDARIES:
    - NEVER execute SQL queries
    - NEVER calculate KPIs
    - NEVER perform analytics
    - ALWAYS use SchemaService for metadata
    - ALWAYS return standardized QueryPlan
    """
    
    def __init__(self):
        self._schema = None
        self._dn_pattern = DN_PATTERN
        
        logger.info("=" * 70)
        logger.info("AI Query Service v5.0 - Single Source of Routing Truth")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ROUTING PRIORITY:")
        logger.info("   1️⃣ DN Lookup (8-12 digits)")
        logger.info("   2️⃣ Dealer Resolution")
        logger.info("   3️⃣ Warehouse Resolution")
        logger.info("   4️⃣ City Resolution")
        logger.info("   5️⃣ KPI Detection")
        logger.info("   6️⃣ Ranking Detection")
        logger.info("   7️⃣ Executive / Root Cause")
        logger.info("   8️⃣ Intent Detection")
        logger.info("   9️⃣ Groq (LAST RESORT)")
        logger.info("")
        logger.info("   STATUS: ✅ ENTERPRISE READY")
        logger.info("=" * 70)
    
    # ==========================================================
    # LAZY PROPERTIES
    # ==========================================================
    
    @property
    def schema(self):
        """Lazy load SchemaService."""
        if self._schema is None:
            self._schema, _ = _get_schema_service()
        return self._schema
    
    # ==========================================================
    # MAIN ENTRY POINT
    # ==========================================================
    
    def process_query(self, question: str, context: Optional[Dict] = None) -> QueryPlan:
        """
        Process query and return deterministic routing decision.
        
        PRIORITY ORDER (ENFORCED):
        1. DN Lookup
        2. Dealer Resolution
        3. Warehouse Resolution
        4. City Resolution
        5. KPI Detection
        6. Ranking Detection
        7. Executive / Root Cause
        8. Intent Detection
        9. Groq (LAST RESORT)
        
        Args:
            question: User's question
            context: Optional conversation context
            
        Returns:
            QueryPlan: Standardized routing decision
        """
        if not question or not question.strip():
            return QueryPlan(
                intent="help",
                service="help",
                confidence=0.0,
                reason="Empty query",
                original_message=question or ""
            )
        
        question_clean = question.strip()
        context = context or {}
        
        logger.debug(f"🔍 Processing: {question_clean[:100]}")
        
        # ==========================================================
        # PRIORITY 1: DN Lookup (Highest Priority)
        # ==========================================================
        
        dn_result = self._detect_dn(question_clean)
        if dn_result:
            logger.info(f"📍 DN Detected: {dn_result}")
            return QueryPlan(
                intent="dn_lookup",
                entity=dn_result,
                entity_type="dn",
                service="analytics",
                confidence=1.0,
                needs_groq=False,
                reason=f"DN number matched pattern: {dn_result}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 2: Dealer Resolution
        # ==========================================================
        
        dealer_result = self._resolve_dealer(question_clean, context)
        if dealer_result:
            logger.info(f"📍 Dealer Resolved: {dealer_result}")
            return QueryPlan(
                intent="dealer_dashboard",
                entity=dealer_result,
                entity_type="dealer",
                service="analytics",
                confidence=0.99,
                needs_groq=False,
                reason=f"Dealer matched in SchemaService: {dealer_result}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 3: Warehouse Resolution
        # ==========================================================
        
        warehouse_result = self._resolve_warehouse(question_clean)
        if warehouse_result:
            logger.info(f"📍 Warehouse Resolved: {warehouse_result}")
            return QueryPlan(
                intent="warehouse_dashboard",
                entity=warehouse_result,
                entity_type="warehouse",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"Warehouse matched in SchemaService: {warehouse_result}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 4: City Resolution
        # ==========================================================
        
        city_result = self._resolve_city(question_clean)
        if city_result:
            logger.info(f"📍 City Resolved: {city_result}")
            return QueryPlan(
                intent="city_dashboard",
                entity=city_result,
                entity_type="city",
                service="analytics",
                confidence=0.95,
                needs_groq=False,
                reason=f"City matched in SchemaService: {city_result}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 5: KPI Detection
        # ==========================================================
        
        kpi_result = self._detect_kpi(question_clean)
        if kpi_result:
            intent, entity = kpi_result
            logger.info(f"📊 KPI Detected: {intent} (entity={entity})")
            return QueryPlan(
                intent=intent,
                entity=entity,
                entity_type="kpi",
                service="kpi",
                confidence=0.90,
                needs_groq=False,
                reason=f"KPI pattern matched: {intent}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 6: Ranking Detection
        # ==========================================================
        
        ranking_result = self._detect_ranking(question_clean)
        if ranking_result:
            intent, entity_type = ranking_result
            logger.info(f"📊 Ranking Detected: {intent}")
            return QueryPlan(
                intent=intent,
                entity=None,
                entity_type=entity_type,
                service="analytics",
                confidence=0.85,
                needs_groq=False,
                reason=f"Ranking pattern matched: {intent}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 7: Executive / Root Cause
        # ==========================================================
        
        executive_result = self._detect_executive(question_clean)
        if executive_result:
            intent, needs_groq = executive_result
            logger.info(f"📊 Executive Detected: {intent} (groq={needs_groq})")
            return QueryPlan(
                intent=intent,
                entity=None,
                entity_type="executive",
                service="analytics",
                confidence=0.90,
                needs_groq=needs_groq,
                reason=f"Executive pattern matched: {intent}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 8: Intent Detection (General)
        # ==========================================================
        
        intent_result = self._detect_intent(question_clean)
        if intent_result:
            intent, confidence = intent_result
            logger.info(f"🎯 Intent Detected: {intent} (confidence={confidence:.2f})")
            return QueryPlan(
                intent=intent,
                entity=None,
                entity_type="general",
                service="analytics" if intent != "help" else "help",
                confidence=confidence,
                needs_groq=False,
                reason=f"Intent pattern matched: {intent}",
                original_message=question_clean
            )
        
        # ==========================================================
        # PRIORITY 9: Groq (LAST RESORT)
        # ==========================================================
        
        logger.info(f"🤖 Routing to Groq (last resort): {question_clean[:50]}")
        return QueryPlan(
            intent="general_ai",
            entity=None,
            entity_type="groq",
            service="groq",
            confidence=0.50,
            needs_groq=True,
            reason="No deterministic routing matched - using Groq",
            original_message=question_clean
        )
    
    # ==========================================================
    # DETECTION METHODS
    # ==========================================================
    
    def _detect_dn(self, question: str) -> Optional[str]:
        """Detect DN number (8-12 digits)."""
        match = self._dn_pattern.search(question)
        if match:
            return match.group(1)
        return None
    
    def _resolve_dealer(self, question: str, context: Dict) -> Optional[str]:
        """
        Resolve dealer using SchemaService with context intelligence.
        
        SUPPORTS:
        - "Dubai Electronics" → dealer_dashboard
        - "Rafi Electronics Oghi" → dealer_dashboard
        - "Mian Group of Chakwal Wah" → dealer_dashboard
        - "performance" (with context) → dealer_performance
        """
        # First try: Direct resolution
        dealer = self.schema.resolve_dealer(question)
        if dealer:
            return dealer
        
        # Second try: Context intelligence (follow-up)
        if context and context.get("last_dealer"):
            # Check if this is a follow-up question
            follow_up_patterns = [
                "revenue", "units", "performance", "aging", "pending",
                "pod", "pgi", "dashboard", "summary", "details",
                "show", "view", "get"
            ]
            question_lower = question.lower()
            for pattern in follow_up_patterns:
                if pattern in question_lower:
                    # This is a follow-up about the last dealer
                    return context.get("last_dealer")
        
        # Third try: Check if this is a dealer name with common prefixes removed
        cleaned = question.lower().strip()
        prefixes = ["show ", "display ", "get ", "view ", "tell me about "]
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                dealer = self.schema.resolve_dealer(cleaned)
                if dealer:
                    return dealer
        
        return None
    
    def _resolve_warehouse(self, question: str) -> Optional[str]:
        """Resolve warehouse using SchemaService."""
        # Direct resolution
        warehouse = self.schema.resolve_warehouse(question)
        if warehouse:
            return warehouse
        
        # Check with common prefixes
        cleaned = question.lower().strip()
        prefixes = ["show ", "display ", "get ", "view "]
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                warehouse = self.schema.resolve_warehouse(cleaned)
                if warehouse:
                    return warehouse
        
        return None
    
    def _resolve_city(self, question: str) -> Optional[str]:
        """Resolve city using SchemaService."""
        # Direct resolution
        city = self.schema.resolve_city(question)
        if city:
            return city
        
        # Check with common prefixes
        cleaned = question.lower().strip()
        prefixes = ["show ", "display ", "get ", "view "]
        for prefix in prefixes:
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):].strip()
                city = self.schema.resolve_city(cleaned)
                if city:
                    return city
        
        return None
    
    def _detect_kpi(self, question: str) -> Optional[Tuple[str, Optional[str]]]:
        """
        Detect KPI queries.
        
        Returns:
            Tuple of (intent, entity) or None
        """
        question_lower = question.lower()
        
        # Pending PGI
        if "pending pgi" in question_lower or "pgi pending" in question_lower:
            return ("pending_pgi", self._extract_entity(question))
        
        # Pending POD
        if "pending pod" in question_lower or "pod pending" in question_lower:
            return ("pending_pod", self._extract_entity(question))
        
        # PGI Aging
        if "pgi aging" in question_lower or "aging pgi" in question_lower:
            return ("pgi_aging", self._extract_entity(question))
        
        # POD Aging
        if "pod aging" in question_lower or "aging pod" in question_lower:
            return ("pod_aging", self._extract_entity(question))
        
        # Delivery Aging
        if "delivery aging" in question_lower or "aging delivery" in question_lower:
            return ("delivery_aging", self._extract_entity(question))
        
        # KPI Dashboard
        if "kpi" in question_lower and ("dashboard" in question_lower or "summary" in question_lower):
            return ("kpi_dashboard", None)
        
        return None
    
    def _detect_ranking(self, question: str) -> Optional[Tuple[str, str]]:
        """
        Detect ranking queries.
        
        Returns:
            Tuple of (intent, entity_type) or None
        """
        question_lower = question.lower()
        
        # City Ranking
        city_patterns = [
            "which city", "top city", "highest city", "best city",
            "city with highest", "city ranking", "cities by",
            "top cities", "best cities", "city performance"
        ]
        if any(p in question_lower for p in city_patterns):
            return ("city_ranking", "city")
        
        # Dealer Ranking
        dealer_patterns = [
            "top dealer", "best dealer", "highest dealer",
            "dealer ranking", "dealers by", "top 10 dealer",
            "top dealers", "bottom dealers", "dealer performance"
        ]
        if any(p in question_lower for p in dealer_patterns):
            return ("dealer_ranking", "dealer")
        
        # Warehouse Ranking
        warehouse_patterns = [
            "top warehouse", "best warehouse", "highest warehouse",
            "warehouse ranking", "warehouse performance"
        ]
        if any(p in question_lower for p in warehouse_patterns):
            return ("warehouse_ranking", "warehouse")
        
        return None
    
    def _detect_executive(self, question: str) -> Optional[Tuple[str, bool]]:
        """
        Detect executive/root cause queries.
        
        Returns:
            Tuple of (intent, needs_groq) or None
        """
        question_lower = question.lower()
        
        # Executive Insights
        executive_patterns = [
            "executive insight", "executive summary", "management report",
            "key issue", "critical issue", "top issue", "main issue",
            "biggest bottleneck", "major problem", "critical problem"
        ]
        if any(p in question_lower for p in executive_patterns):
            return ("executive_insight", True)
        
        # Root Cause
        root_cause_patterns = [
            "root cause", "why delayed", "why pending", "why aging",
            "what is the issue", "what's the issue", "what is wrong",
            "bring improvement", "how to improve", "how to fix",
            "what is the key", "what's the key", "key problem"
        ]
        if any(p in question_lower for p in root_cause_patterns):
            return ("root_cause", True)
        
        # Control Tower / Critical Alerts
        control_tower_patterns = [
            "control tower", "critical alert", "urgent alert",
            "critical delivery", "delayed delivery", "at risk"
        ]
        if any(p in question_lower for p in control_tower_patterns):
            return ("control_tower", False)
        
        return None
    
    def _detect_intent(self, question: str) -> Optional[Tuple[str, float]]:
        """
        Detect general intents using SchemaService.
        
        Returns:
            Tuple of (intent, confidence) or None
        """
        intent, confidence = self.schema.detect_intent(question)
        if intent and confidence >= 0.60:
            return (intent, confidence)
        return None
    
    def _extract_entity(self, question: str) -> Optional[str]:
        """Extract entity from question (dealer, warehouse, city)."""
        # Try dealer
        dealer = self.schema.resolve_dealer(question)
        if dealer:
            return dealer
        
        # Try warehouse
        warehouse = self.schema.resolve_warehouse(question)
        if warehouse:
            return warehouse
        
        # Try city
        city = self.schema.resolve_city(question)
        if city:
            return city
        
        return None
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def debug_route(self, question: str) -> Dict[str, Any]:
        """
        Debug routing decision for a question.
        
        Returns:
            Dict with complete routing information
        """
        # Get routing decision
        plan = self.process_query(question, {})
        plan_dict = plan.to_dict()
        
        # Add debug information
        result = {
            "question": question,
            "routing_decision": plan_dict,
            "debug": {
                "dn_detected": bool(self._detect_dn(question)),
                "dealer_resolved": self._resolve_dealer(question, {}),
                "warehouse_resolved": self._resolve_warehouse(question),
                "city_resolved": self._resolve_city(question),
                "kpi_detected": self._detect_kpi(question),
                "ranking_detected": self._detect_ranking(question),
                "executive_detected": self._detect_executive(question),
                "intent_detected": self._detect_intent(question)
            }
        }
        
        return result
    
    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        return {
            "version": "5.0",
            "architecture": "Single Source of Routing Truth",
            "priority_order": [
                "1. DN Lookup",
                "2. Dealer Resolution",
                "3. Warehouse Resolution",
                "4. City Resolution",
                "5. KPI Detection",
                "6. Ranking Detection",
                "7. Executive / Root Cause",
                "8. Intent Detection",
                "9. Groq (LAST RESORT)"
            ],
            "schema_loaded": self._schema is not None
        }


# ==========================================================
# SINGLETON
# ==========================================================

_ai_query_service = None

def get_ai_query_service() -> AIQueryService:
    """Get singleton AIQueryService instance."""
    global _ai_query_service
    if _ai_query_service is None:
        _ai_query_service = AIQueryService()
    return _ai_query_service


# ==========================================================
# WRAPPER FUNCTIONS (PRESERVED SIGNATURE - CRITICAL)
# ==========================================================

def process_query(question: str, context: Optional[Dict] = None) -> Dict[str, Any]:
    """Process query and return routing decision."""
    service = get_ai_query_service()
    plan = service.process_query(question, context)
    return plan.to_dict()


def debug_route(question: str) -> Dict[str, Any]:
    """Debug routing decision for a question."""
    service = get_ai_query_service()
    return service.debug_route(question)


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Query Service v5.0 - Single Source of Routing Truth")
logger.info("=" * 70)
logger.info("")
logger.info("   PRIORITY ORDER (ENFORCED):")
logger.info("   1️⃣ DN Lookup (8-12 digits) - HIGHEST PRIORITY")
logger.info("   2️⃣ Dealer Resolution - SchemaService")
logger.info("   3️⃣ Warehouse Resolution - SchemaService")
logger.info("   4️⃣ City Resolution - SchemaService")
logger.info("   5️⃣ KPI Detection - Pending PGI/POD, Aging")
logger.info("   6️⃣ Ranking Detection - Top/Best Cities/Dealers")
logger.info("   7️⃣ Executive / Root Cause - Analytics First")
logger.info("   8️⃣ Intent Detection - General Intents")
logger.info("   9️⃣ Groq - LAST RESORT")
logger.info("")
logger.info("   ROUTING RULES:")
logger.info("   ✅ Deterministic - No guessing")
logger.info("   ✅ Database FIRST - Analytics SECOND - Groq LAST")
logger.info("   ✅ Context Intelligence - Follow-up support")
logger.info("   ✅ Standardized QueryPlan - Every request")
logger.info("")
logger.info("   STATUS: ✅ ENTERPRISE READY")
logger.info("=" * 70)
