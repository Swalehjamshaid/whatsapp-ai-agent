# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v30.0)
# ==========================================================
# SINGLE BRAIN OF THE SYSTEM - DATA FIRST, GROQ SECOND
#
# ARCHITECTURE:
# WhatsApp → webhook.py → THIS FILE → Data Services → Business Rules → Groq → Response
#
# NO MORE:
# - IntentEngine, EntityExtractor, ContextService, QueryRouterService
# - CircuitBreaker, RetryHandler, RequestDeduplicator
# - AsyncAIQueryService
#
# INSTEAD:
# 1. Understand Query (simple pattern matching)
# 2. Extract Entities (direct extraction)
# 3. Get Data (from specialized services)
# 4. Apply Business Rules (calculate KPIs)
# 5. Generate Insight (Groq for analysis)
# 6. Format Response (WhatsApp ready)
#
# ==========================================================

import re
import time
import hashlib
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, date
from enum import Enum
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config


# ==========================================================
# QUERY TYPES (Simple, No Complex Intent Engine)
# ==========================================================

class QueryType(str, Enum):
    """Simple query types - direct pattern matching"""
    DN_LOOKUP = "dn_lookup"
    DEALER_QUERY = "dealer_query"
    DEALER_RANKING = "dealer_ranking"
    TOP_DEALERS = "top_dealers"
    PENDING_POD = "pending_pod"
    PENDING_PGI = "pending_pgi"
    PENDING_DELIVERIES = "pending_deliveries"
    LATE_DELIVERIES = "late_deliveries"
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    TOP_RISKS = "top_risks"
    PRODUCT_RANKING = "product_ranking"
    TOP_PRODUCTS = "top_products"
    WAREHOUSE_RANKING = "warehouse_ranking"
    CITY_RANKING = "city_ranking"
    REVENUE_ANALYSIS = "revenue_analysis"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    TREND_ANALYSIS = "trend_analysis"
    RECOMMENDATIONS = "recommendations"
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"


# ==========================================================
# EXTRACTED ENTITIES (Simple Data Class)
# ==========================================================

@dataclass
class ExtractedEntities:
    """Simple entity extraction - no complex classes"""
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    warehouse: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    product: Optional[str] = None
    days: Optional[int] = None
    month: Optional[str] = None
    year: Optional[int] = None
    
    def has_any(self) -> bool:
        return any([
            self.dn_number, self.dealer, self.warehouse,
            self.city, self.region, self.product
        ])
    
    def to_dict(self) -> Dict:
        return {
            k: v for k, v in self.__dict__.items() if v is not None
        }


# ==========================================================
# SIMPLE QUERY PARSER (No Intent Engine)
# ==========================================================

class QueryParser:
    """
    Simple query parser - direct pattern matching.
    No complex NLP, no machine learning, just regex and keywords.
    """
    
    # Patterns for DN numbers (8-15 digits)
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DN_WITH_PREFIX = re.compile(r'DN\s*[:]?\s*(\d{8,15})', re.IGNORECASE)
    
    # Keywords for different query types
    KEYWORDS = {
        QueryType.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing dealer', 'leading dealer'],
        QueryType.DEALER_RANKING: ['dealer ranking', 'rank dealer', 'dealer performance', 'how is dealer'],
        QueryType.DEALER_QUERY: ['dealer', 'show dealer', 'dealer details'],
        QueryType.PENDING_POD: ['pending pod', 'pod pending', 'pending proof', 'missing pod', 'pod not received'],
        QueryType.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched', 'pending goods issue'],
        QueryType.PENDING_DELIVERIES: ['pending delivery', 'pending deliveries', 'undelivered', 'not delivered'],
        QueryType.LATE_DELIVERIES: ['late delivery', 'delayed delivery', 'overdue delivery', 'late deliveries'],
        QueryType.EXECUTIVE_DASHBOARD: ['executive dashboard', 'executive summary', 'ceo dashboard', 'kpi dashboard', 'overview'],
        QueryType.NETWORK_HEALTH: ['network health', 'health check', 'system health', 'overall health'],
        QueryType.TOP_RISKS: ['top risk', 'critical risk', 'high risk', 'risk analysis', 'risk summary'],
        QueryType.PRODUCT_RANKING: ['product ranking', 'top product', 'best product', 'product performance'],
        QueryType.TOP_PRODUCTS: ['top product', 'best selling', 'fast moving', 'popular product'],
        QueryType.WAREHOUSE_RANKING: ['warehouse ranking', 'top warehouse', 'warehouse performance'],
        QueryType.CITY_RANKING: ['city ranking', 'top city', 'city performance'],
        QueryType.REVENUE_ANALYSIS: ['revenue', 'sales', 'income', 'revenue analysis', 'sales analysis'],
        QueryType.ROOT_CAUSE_ANALYSIS: ['why', 'root cause', 'reason', 'cause', 'what caused'],
        QueryType.TREND_ANALYSIS: ['trend', 'pattern', 'over time', 'monthly', 'weekly', 'compare'],
        QueryType.RECOMMENDATIONS: ['recommend', 'suggest', 'improve', 'action', 'what should'],
        QueryType.HELP: ['help', 'menu', 'commands', 'what can you do', 'how to use'],
        QueryType.GREETING: ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening', 'namaste'],
    }
    
    @classmethod
    def parse(cls, question: str) -> Tuple[QueryType, ExtractedEntities]:
        """
        Parse question and return (query_type, extracted_entities)
        No complex NLP, just direct pattern matching.
        """
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # ==========================================================
        # STEP 1: Extract DN Number
        # ==========================================================
        # Try DN with prefix first
        dn_match = cls.DN_WITH_PREFIX.search(question)
        if not dn_match:
            dn_match = cls.DN_PATTERN.search(question)
        
        if dn_match:
            entities.dn_number = dn_match.group(1)
            return QueryType.DN_LOOKUP, entities
        
        # ==========================================================
        # STEP 2: Extract Dealer
        # ==========================================================
        dealer_patterns = [
            r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+performance|\s+details|\s+$|\.|\,)',
            r'show\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'dealer\s+([A-Za-z0-9\s]+?)\s+(?:pod|delivery|pending)',
        ]
        
        for pattern in dealer_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.dealer = match.group(1).strip()
                break
        
        # ==========================================================
        # STEP 3: Extract City
        # ==========================================================
        city_patterns = [
            r'in\s+([A-Za-z\s]+?)(?:\s+region|\s+warehouse|\s+$|\.|\,)',
            r'city\s+([A-Za-z\s]+?)(?:\s+$|\.|\,)',
            r'for\s+([A-Za-z\s]+?)(?:\s+warehouse|\s+$|\.|\,)',
        ]
        
        # Common Pakistani cities
        cities = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
                  'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot']
        
        for city in cities:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        
        # ==========================================================
        # STEP 4: Extract Warehouse
        # ==========================================================
        warehouse_patterns = [
            r'warehouse\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'wh\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
        ]
        
        for pattern in warehouse_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.warehouse = match.group(1).strip()
                break
        
        # ==========================================================
        # STEP 5: Extract Days
        # ==========================================================
        days_patterns = [
            r'(\d+)\s+days',
            r'(\d+)-day',
            r'greater than (\d+)',
            r'more than (\d+)',
        ]
        
        for pattern in days_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.days = int(match.group(1))
                break
        
        # ==========================================================
        # STEP 6: Determine Query Type by Keywords
        # ==========================================================
        for query_type, keywords in cls.KEYWORDS.items():
            for keyword in keywords:
                if keyword in question_lower:
                    return query_type, entities
        
        # ==========================================================
        # STEP 7: Default to General
        # ==========================================================
        return QueryType.GENERAL, entities


# ==========================================================
# RESPONSE CACHE (Simple TTL Cache)
# ==========================================================

class ResponseCache:
    """Simple TTL cache for responses"""
    
    def __init__(self, ttl: int = 300):
        self.cache = {}
        self.ttl = ttl
    
    def get(self, key: str) -> Optional[Any]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, value: Any):
        self.cache[key] = (value, time.time())
    
    def get_key(self, query_type: QueryType, entities: ExtractedEntities) -> str:
        """Generate cache key from query type and entities"""
        entities_str = str(sorted(entities.to_dict().items()))
        return hashlib.md5(f"{query_type.value}:{entities_str}".encode()).hexdigest()
    
    def clear(self):
        count = len(self.cache)
        self.cache.clear()
        return count


# ==========================================================
# RESPONSE FORMATTER (WhatsApp Ready)
# ==========================================================

class ResponseFormatter:
    """Format responses for WhatsApp"""
    
    @staticmethod
    def success(data: Any, summary: str = None, recommendations: List[str] = None) -> Dict:
        """Standard success response"""
        return {
            "success": True,
            "data": data,
            "summary": summary or "",
            "recommendations": recommendations or [],
            "source": "database"
        }
    
    @staticmethod
    def error(message: str) -> Dict:
        """Standard error response"""
        return {
            "success": False,
            "data": {},
            "summary": message,
            "recommendations": [],
            "source": "error"
        }
    
    @staticmethod
    def to_whatsapp(response: Dict) -> str:
        """Convert response to WhatsApp message"""
        if not response.get("success"):
            return f"❌ {response.get('summary', 'Unable to process request')}"
        
        data = response.get("data", {})
        summary = response.get("summary", "")
        recommendations = response.get("recommendations", [])
        
        # If data is a string, return as is
        if isinstance(data, str):
            return data
        
        # If data has a whatsapp_message field
        if isinstance(data, dict) and data.get("whatsapp_message"):
            return data["whatsapp_message"]
        
        # If summary exists, return it
        if summary:
            return summary
        
        # Default fallback
        return "✅ Request processed successfully"


# ==========================================================
# GROQ INSIGHT GENERATOR (AI Layer)
# ==========================================================

class GroqInsightGenerator:
    """
    Generate insights using Groq.
    This is the AI layer - used for analysis, not for basic data retrieval.
    """
    
    def __init__(self, db: Session):
        self.db = db
        self._groq_service = None
    
    @property
    def groq_service(self):
        """Lazy load Groq service"""
        if self._groq_service is None:
            try:
                from app.services.groq_insight_service import GroqInsightService
                self._groq_service = GroqInsightService(self.db)
            except Exception as e:
                logger.warning(f"Groq service not available: {e}")
                self._groq_service = None
        return self._groq_service
    
    def generate(self, query_type: QueryType, data: Dict, question: str) -> Optional[str]:
        """
        Generate insight using Groq.
        Only used for complex analysis, not for simple lookups.
        """
        if not self.groq_service:
            return None
        
        # Only use Groq for specific query types
        groq_query_types = [
            QueryType.ROOT_CAUSE_ANALYSIS,
            QueryType.TREND_ANALYSIS,
            QueryType.RECOMMENDATIONS,
        ]
        
        if query_type not in groq_query_types:
            return None
        
        try:
            # Build prompt based on query type
            prompt = self._build_prompt(query_type, data, question)
            if not prompt:
                return None
            
            # Call Groq
            result = self.groq_service.analyze(prompt, query_type.value, {})
            if isinstance(result, dict):
                return result.get("insight") or result.get("response") or str(result)
            return str(result) if result else None
            
        except Exception as e:
            logger.error(f"Groq insight generation failed: {e}")
            return None
    
    def _build_prompt(self, query_type: QueryType, data: Dict, question: str) -> Optional[str]:
        """Build prompt for Groq based on query type"""
        
        if query_type == QueryType.ROOT_CAUSE_ANALYSIS:
            return f"""
Analyze the following logistics data to identify root causes:

Question: {question}

Data: {data}

Please provide:
1. Root Cause Analysis
2. Business Impact
3. Recommended Actions
4. Risk Level (Critical/High/Medium/Low)
"""
        
        elif query_type == QueryType.TREND_ANALYSIS:
            return f"""
Analyze the following logistics trends:

Question: {question}

Data: {data}

Please provide:
1. Key Trends Observed
2. Improvement Areas
3. Decline Areas
4. Predictions for Next Month
"""
        
        elif query_type == QueryType.RECOMMENDATIONS:
            return f"""
Provide actionable recommendations based on:

Question: {question}

Data: {data}

Please provide:
1. Top 3 Priority Actions
2. Expected Impact
3. Timeline for Implementation
"""
        
        return None


# ==========================================================
# MAIN AI QUERY SERVICE (SINGLE BRAIN)
# ==========================================================

class AIQueryService:
    """
    Single Brain of the System - Data First, Groq Second
    
    This is the ONLY orchestration layer. Every query goes through:
    1. Parse (understand query)
    2. Extract (get entities)
    3. Fetch Data (from specialized services)
    4. Apply Business Rules (calculate KPIs)
    5. Generate Insights (Groq for complex analysis)
    6. Format Response (WhatsApp ready)
    
    NO MORE:
    - IntentEngine
    - EntityExtractor
    - ContextService
    - QueryRouterService
    - CircuitBreaker
    - RetryHandler
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.start_time = None
        self.request_id = None
        
        # Simple components
        self.parser = QueryParser()
        self.cache = ResponseCache()
        self.formatter = ResponseFormatter()
        self.groq = GroqInsightGenerator(db)
        
        # Specialized services (data providers)
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "avg_response_time_ms": 0,
            "cache_hits": 0,
            "groq_calls": 0
        }
        
        logger.info("=" * 70)
        logger.info("🧠 AI QUERY SERVICE v30.0 - SINGLE BRAIN ARCHITECTURE")
        logger.info("   Data First → Business Rules → Groq → Response")
        logger.info("=" * 70)
    
    @property
    def logistics_service(self):
        """Lazy load logistics service"""
        if self._logistics_service is None:
            try:
                from app.services.logistics_query_service import LogisticsQueryService
                self._logistics_service = LogisticsQueryService(self.db)
                logger.info("✅ LogisticsQueryService loaded")
            except Exception as e:
                logger.error(f"Failed to load LogisticsQueryService: {e}")
                self._logistics_service = None
        return self._logistics_service
    
    @property
    def analytics_service(self):
        """Lazy load analytics service"""
        if self._analytics_service is None:
            try:
                from app.services.analytics_service import AnalyticsService
                self._analytics_service = AnalyticsService(self.db)
                logger.info("✅ AnalyticsService loaded")
            except Exception as e:
                logger.error(f"Failed to load AnalyticsService: {e}")
                self._analytics_service = None
        return self._analytics_service
    
    @property
    def kpi_service(self):
        """Lazy load KPI service"""
        if self._kpi_service is None:
            try:
                from app.services.kpi_service import KPIService
                self._kpi_service = KPIService(self.db)
                logger.info("✅ KPIService loaded")
            except Exception as e:
                logger.error(f"Failed to load KPIService: {e}")
                self._kpi_service = None
        return self._kpi_service
    
    # ==========================================================
    # MAIN PROCESSING METHOD
    # ==========================================================
    
    def process_query(
        self,
        question: str,
        user_phone: str = None,
        user_role: str = None
    ) -> Dict[str, Any]:
        """
        Process any query - Single entry point.
        
        Flow:
        1. Parse question → QueryType + Entities
        2. Check cache
        3. Route to appropriate service
        4. Apply business rules
        5. Generate Groq insights (if needed)
        6. Format response
        """
        self.start_time = time.time()
        self.request_id = hashlib.md5(f"{user_phone}:{question}".encode()).hexdigest()[:8]
        
        # Update metrics
        self.metrics["total_requests"] += 1
        
        question = question.strip()
        logger.info(f"[{self.request_id}] 📱 {question[:100]}")
        
        # ==========================================================
        # STEP 1: Parse Query (Simple pattern matching)
        # ==========================================================
        query_type, entities = self.parser.parse(question)
        logger.info(f"[{self.request_id}] 🎯 Type={query_type.value} | Entities={entities.to_dict()}")
        
        # ==========================================================
        # STEP 2: Check Cache
        # ==========================================================
        cache_key = self.cache.get_key(query_type, entities)
        cached = self.cache.get(cache_key)
        if cached:
            logger.info(f"[{self.request_id}] 💾 Cache hit")
            self.metrics["cache_hits"] += 1
            return cached
        
        # ==========================================================
        # STEP 3: Route to Service (Direct, no router)
        # ==========================================================
        result = self._route(query_type, entities, question)
        
        # ==========================================================
        # STEP 4: Apply Business Rules (if needed)
        # ==========================================================
        result = self._apply_business_rules(result, query_type, entities)
        
        # ==========================================================
        # STEP 5: Generate Groq Insights (for complex queries)
        # ==========================================================
        if result.get("success") and result.get("data"):
            groq_insight = self.groq.generate(query_type, result.get("data"), question)
            if groq_insight:
                self.metrics["groq_calls"] += 1
                result["groq_insight"] = groq_insight
                logger.info(f"[{self.request_id}] 🤖 Groq insight generated")
        
        # ==========================================================
        # STEP 6: Format Response
        # ==========================================================
        whatsapp_response = self.formatter.to_whatsapp(result)
        
        final_response = {
            "success": result.get("success", True),
            "response": whatsapp_response,
            "query_type": query_type.value,
            "request_id": self.request_id
        }
        
        # Cache successful responses
        if result.get("success") and len(whatsapp_response) > 50:
            self.cache.set(cache_key, final_response)
        
        # Update metrics
        elapsed_ms = (time.time() - self.start_time) * 1000
        self.metrics["successful_requests" if result.get("success") else "failed_requests"] += 1
        self.metrics["avg_response_time_ms"] = (
            (self.metrics["avg_response_time_ms"] * (self.metrics["total_requests"] - 1) + elapsed_ms)
            / self.metrics["total_requests"]
        )
        
        logger.info(f"[{self.request_id}] ✅ Done | {elapsed_ms:.0f}ms | Cache={cached is not None}")
        
        return final_response
    
    # ==========================================================
    # DIRECT ROUTING (No Router Service)
    # ==========================================================
    
    def _route(self, query_type: QueryType, entities: ExtractedEntities, question: str) -> Dict:
        """Direct routing to appropriate service"""
        
        # ========== DN Lookup ==========
        if query_type == QueryType.DN_LOOKUP and entities.dn_number:
            if self.logistics_service:
                result = self.logistics_service.get_complete_dn_intelligence(entities.dn_number)
                if result.get("success"):
                    return self.formatter.success(
                        data=result.get("data", {}),
                        summary=self._build_dn_summary(result.get("data", {}))
                    )
                return self.formatter.error(result.get("error", "DN not found"))
            return self.formatter.error("Logistics service unavailable")
        
        # ========== Dealer Queries ==========
        if query_type == QueryType.DEALER_QUERY and entities.dealer:
            if self.analytics_service:
                result = self.analytics_service.get_dealer_dashboard(entities.dealer)
                if result:
                    return self.formatter.success(
                        data=result,
                        summary=self._build_dealer_summary(result)
                    )
            return self.formatter.error(f"Dealer '{entities.dealer}' not found")
        
        if query_type in [QueryType.TOP_DEALERS, QueryType.DEALER_RANKING]:
            if self.analytics_service:
                result = self.analytics_service.get_dealer_ranking()
                return self.formatter.success(
                    data=result,
                    summary=self._build_dealer_ranking_summary(result)
                )
            return self.formatter.error("Analytics service unavailable")
        
        # ========== POD Queries ==========
        if query_type == QueryType.PENDING_POD:
            if self.logistics_service:
                result = self.logistics_service.get_pending_pods()
                return self.formatter.success(
                    data=result,
                    summary=self._build_pending_pod_summary(result)
                )
            return self.formatter.error("Logistics service unavailable")
        
        if query_type == QueryType.PENDING_PGI:
            if self.logistics_service:
                result = self.logistics_service.get_pending_pgi()
                return self.formatter.success(
                    data=result,
                    summary=self._build_pending_pgi_summary(result)
                )
            return self.formatter.error("Logistics service unavailable")
        
        # ========== Dashboard / KPI Queries ==========
        if query_type == QueryType.EXECUTIVE_DASHBOARD:
            if self.kpi_service:
                result = self.kpi_service.get_executive_dashboard()
                return self.formatter.success(
                    data=result,
                    summary=self._build_dashboard_summary(result)
                )
            return self.formatter.error("KPI service unavailable")
        
        if query_type == QueryType.NETWORK_HEALTH:
            if self.kpi_service:
                result = self.kpi_service.get_network_health()
                return self.formatter.success(
                    data=result,
                    summary=self._build_health_summary(result)
                )
            return self.formatter.error("KPI service unavailable")
        
        if query_type == QueryType.TOP_RISKS:
            if self.kpi_service:
                result = self.kpi_service.get_top_risks()
                return self.formatter.success(
                    data=result,
                    summary=self._build_risks_summary(result)
                )
            return self.formatter.error("KPI service unavailable")
        
        # ========== Product Queries ==========
        if query_type in [QueryType.PRODUCT_RANKING, QueryType.TOP_PRODUCTS]:
            if self.analytics_service:
                result = self.analytics_service.get_product_ranking()
                return self.formatter.success(
                    data=result,
                    summary=self._build_product_summary(result)
                )
            return self.formatter.error("Analytics service unavailable")
        
        # ========== Warehouse Queries ==========
        if query_type == QueryType.WAREHOUSE_RANKING:
            if self.analytics_service:
                result = self.analytics_service.get_warehouse_ranking()
                return self.formatter.success(
                    data=result,
                    summary=self._build_warehouse_summary(result)
                )
            return self.formatter.error("Analytics service unavailable")
        
        # ========== Revenue Queries ==========
        if query_type == QueryType.REVENUE_ANALYSIS:
            if self.analytics_service:
                result = self.analytics_service.get_revenue_analysis()
                return self.formatter.success(
                    data=result,
                    summary=self._build_revenue_summary(result)
                )
            return self.formatter.error("Analytics service unavailable")
        
        # ========== Help ==========
        if query_type == QueryType.HELP:
            return self.formatter.success(
                data={},
                summary=self._get_help_message()
            )
        
        if query_type == QueryType.GREETING:
            return self.formatter.success(
                data={},
                summary=self._get_greeting_message()
            )
        
        # ========== General / AI Queries ==========
        if query_type == QueryType.GENERAL:
            # Try to handle as general query with Groq
            return self._handle_general_query(question)
        
        # ========== Fallback ==========
        return self.formatter.error(
            "I couldn't understand your query. Type 'Help' for available commands."
        )
    
    # ==========================================================
    # BUSINESS RULES (Calculate KPIs, Scores)
    # ==========================================================
    
    def _apply_business_rules(self, result: Dict, query_type: QueryType, entities: ExtractedEntities) -> Dict:
        """Apply business rules to enhance response"""
        
        if not result.get("success"):
            return result
        
        data = result.get("data", {})
        
        # Calculate health scores for DN data
        if query_type == QueryType.DN_LOOKUP and data:
            health_score = self._calculate_health_score(data)
            if health_score:
                data["health_score"] = health_score
                result["data"] = data
        
        return result
    
    def _calculate_health_score(self, dn_data: Dict) -> Optional[int]:
        """Calculate health score for a DN"""
        try:
            aging = dn_data.get("aging", {})
            max_delay = max([
                aging.get("delivery_aging", 0),
                aging.get("pending_delivery_aging", 0),
                aging.get("pod_aging", 0),
                aging.get("pending_pod_aging", 0)
            ])
            
            if max_delay <= 1:
                return 100
            elif max_delay <= 3:
                return 80
            elif max_delay <= 7:
                return 60
            elif max_delay <= 15:
                return 40
            else:
                return 20
        except:
            return None
    
    # ==========================================================
    # RESPONSE BUILDERS (WhatsApp Format)
    # ==========================================================
    
    def _build_dn_summary(self, data: Dict) -> str:
        """Build DN summary for WhatsApp"""
        if not data:
            return "DN details not available"
        
        dn_no = data.get("dn_no", "N/A")
        dealer = data.get("dealer", "N/A")
        status = data.get("status", "N/A")
        health = data.get("health_score", "N/A")
        
        return f"""
📦 *DN COMPLETE INTELLIGENCE REPORT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {dn_no}
🏪 *Dealer:* {dealer}
📍 *Status:* {status}
💚 *Health Score:* {health}/100

Type "timeline" for journey details, "products" for items in this DN
"""
    
    def _build_dealer_summary(self, data: Dict) -> str:
        """Build dealer summary"""
        dealer_name = data.get("dealer", "Dealer")
        total_dns = data.get("total_dns", 0)
        completed = data.get("completed_dns", 0)
        rate = data.get("completion_rate", 0)
        
        return f"""
🏪 *DEALER PERFORMANCE: {dealer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *Total DNs:* {total_dns}
✅ *Completed:* {completed}
📈 *Completion Rate:* {rate}%
"""
    
    def _build_dealer_ranking_summary(self, data: Dict) -> str:
        """Build dealer ranking summary"""
        dealers = data.get("dealers", []) if isinstance(data, dict) else []
        if not dealers:
            return "No dealer ranking data available"
        
        lines = ["🏪 *TOP DEALERS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, dealer in enumerate(dealers[:10], 1):
            name = dealer.get("dealer", "Unknown")
            value = dealer.get("total_value", 0)
            lines.append(f"{i}. {name} - ₹{value:,.0f}")
        
        return "\n".join(lines)
    
    def _build_pending_pod_summary(self, data: Dict) -> str:
        """Build pending POD summary"""
        pending = data.get("pending_pods", []) if isinstance(data, dict) else []
        if not pending:
            return "✅ No pending PODs found. All deliveries have POD confirmation."
        
        lines = ["📋 *PENDING PODs*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for pod in pending[:10]:
            dn = pod.get("dn_no", "Unknown")
            dealer = pod.get("dealer", "Unknown")
            days = pod.get("pending_days", 0)
            lines.append(f"🔢 {dn} - {dealer} ({days} days)")
        
        if len(pending) > 10:
            lines.append(f"\n*+{len(pending) - 10} more pending PODs*")
        
        return "\n".join(lines)
    
    def _build_pending_pgi_summary(self, data: Dict) -> str:
        """Build pending PGI summary"""
        pending = data.get("pending_pgi", []) if isinstance(data, dict) else []
        if not pending:
            return "✅ No pending PGI found. All DNs are dispatched."
        
        lines = ["🚚 *PENDING PGI*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for pgi in pending[:10]:
            dn = pgi.get("dn_no", "Unknown")
            dealer = pgi.get("dealer", "Unknown")
            days = pgi.get("pending_days", 0)
            lines.append(f"🔢 {dn} - {dealer} ({days} days)")
        
        if len(pending) > 10:
            lines.append(f"\n*+{len(pending) - 10} more pending PGI*")
        
        return "\n".join(lines)
    
    def _build_dashboard_summary(self, data: Dict) -> str:
        """Build dashboard summary"""
        return """
👑 *EXECUTIVE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 System is operational.

Type "Network Health" for detailed metrics or "Top Risks" for risk analysis.
"""
    
    def _build_health_summary(self, data: Dict) -> str:
        """Build network health summary"""
        return """
🩺 *NETWORK HEALTH CHECK*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ All systems operational
✅ Database connected
✅ Services running

*Status:* Healthy
"""
    
    def _build_risks_summary(self, data: Dict) -> str:
        """Build risks summary"""
        risks = data.get("risks", []) if isinstance(data, dict) else []
        if not risks:
            return "✅ No critical risks identified"
        
        lines = ["⚠️ *TOP RISKS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for risk in risks[:5]:
            lines.append(f"• {risk}")
        
        return "\n".join(lines)
    
    def _build_product_summary(self, data: Dict) -> str:
        """Build product ranking summary"""
        products = data.get("products", []) if isinstance(data, dict) else []
        if not products:
            return "No product ranking data available"
        
        lines = ["📦 *TOP PRODUCTS*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, product in enumerate(products[:10], 1):
            name = product.get("product", "Unknown")
            qty = product.get("total_qty", 0)
            lines.append(f"{i}. {name} - {qty} units")
        
        return "\n".join(lines)
    
    def _build_warehouse_summary(self, data: Dict) -> str:
        """Build warehouse ranking summary"""
        warehouses = data.get("warehouses", []) if isinstance(data, dict) else []
        if not warehouses:
            return "No warehouse ranking data available"
        
        lines = ["🏭 *TOP WAREHOUSES*", "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for i, wh in enumerate(warehouses[:10], 1):
            name = wh.get("warehouse", "Unknown")
            volume = wh.get("volume", 0)
            lines.append(f"{i}. {name} - {volume} units")
        
        return "\n".join(lines)
    
    def _build_revenue_summary(self, data: Dict) -> str:
        """Build revenue summary"""
        return """
💰 *REVENUE ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Revenue data is available in the dashboard.

Type "Executive Dashboard" for complete overview.
"""
    
    def _handle_general_query(self, question: str) -> Dict:
        """Handle general queries with Groq"""
        if self.groq.groq_service:
            try:
                result = self.groq.groq_service.analyze(question, "general", {})
                if result:
                    insight = result.get("insight") or result.get("response") or str(result)
                    return self.formatter.success(
                        data={"insight": insight},
                        summary=insight
                    )
            except Exception as e:
                logger.error(f"Groq general query failed: {e}")
        
        return self.formatter.error(
            "I couldn't process your request. Type 'Help' for available commands."
        )
    
    def _get_help_message(self) -> str:
        """Get help message"""
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• `6243612278` - Check DN status
• `Status of DN 12345`

🏪 *Dealer Analytics*
• `Top dealers` - Dealer rankings
• `Dealer ABC` - Specific dealer details

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches
• `Pending deliveries` - Undelivered orders

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Top risks` - Critical issues

📦 *Product Analytics*
• `Top products` - Best selling products
• `Product ranking` - Product performance

🏭 *Warehouse Analytics*
• `Warehouse ranking` - Warehouse performance

💰 *Revenue Analysis*
• `Revenue analysis` - Sales overview

❓ *General*
• `Help` - This menu
• `Why is X happening?` - AI analysis

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*Powered by Enterprise Logistics Intelligence v30.0*
"""
    
    def _get_greeting_message(self) -> str:
        """Get greeting message"""
        hour = datetime.now().hour
        if hour < 12:
            greeting = "Good morning"
        elif hour < 17:
            greeting = "Good afternoon"
        else:
            greeting = "Good evening"
        
        return f"""
{greeting}! 👋

I'm your *AI Logistics Assistant*. I can help you track DNs, check dealer performance, monitor pending items, and more.

Type `Help` to see all available commands or just ask me naturally!

*Quick examples:*
• `6243612278` - Track a DN
• `Top dealers` - Dealer rankings
• `Pending POD` - Missing proofs
"""
    
    # ==========================================================
    # HEALTH CHECK & METRICS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for monitoring"""
        return {
            "status": "healthy",
            "version": "30.0",
            "architecture": "Data First → Groq Second",
            "metrics": {
                "total_requests": self.metrics["total_requests"],
                "success_rate": round(
                    self.metrics["successful_requests"] / max(1, self.metrics["total_requests"]) * 100, 1
                ),
                "avg_response_time_ms": round(self.metrics["avg_response_time_ms"], 2),
                "cache_hits": self.metrics["cache_hits"],
                "groq_calls": self.metrics["groq_calls"]
            },
            "services": {
                "logistics": self._logistics_service is not None,
                "analytics": self._analytics_service is not None,
                "kpi": self._kpi_service is not None,
                "groq": self.groq.groq_service is not None
            }
        }
    
    def get_metrics(self) -> Dict:
        """Get service metrics"""
        return self.metrics
    
    def clear_cache(self) -> int:
        """Clear response cache"""
        count = self.cache.clear()
        logger.info(f"Cleared {count} cache entries")
        return count


# ==========================================================
# FACTORY FUNCTIONS
# ==========================================================

def process_whatsapp_query(
    question: str,
    db: Session,
    user_phone: str = None,
    user_role: str = None
) -> str:
    """
    Main entry point for WhatsApp queries.
    
    This is the ONLY function that should be called from whatsapp_service.py
    """
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


def health_check(db: Session) -> Dict[str, Any]:
    """Health check for monitoring"""
    try:
        service = AIQueryService(db)
        return service.health_check()
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e),
            "version": "30.0"
        }
