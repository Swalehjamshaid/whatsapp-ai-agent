# ==========================================================
# FILE: app/services/ai_query_service.py (COMPLETE v33.0)
# ==========================================================
# PURPOSE: PURE ROUTER ONLY - Single Brain for Query Routing
#
# ARCHITECTURE:
# WhatsApp → webhook.py → THIS FILE (Router Only)
#                              ↓
#              ┌───────────────┼───────────────┐
#              ↓               ↓               ↓
#     logistics_service  analytics_service  kpi_service
#              ↓               ↓               ↓
#     business_rules    control_tower     ai_provider
#
# RESPONSIBILITIES (ONLY):
# 1. Detect Intent
# 2. Extract Entities
# 3. Route to Correct Service
# 4. Format Response for WhatsApp
#
# WHAT THIS FILE DOES NOT CONTAIN:
# - No Business Rules (Delivery Aging, Risk Score, etc.)
# - No KPI Logic (Branch Score, Target Achievement, etc.)
# - No Analytics Logic (Top Dealers, Rankings, etc.)
# - No Groq AI Logic (Prompt Building, Insights, etc.)
# - No Database Calculations
# ==========================================================

import re
from typing import Dict, Any, Optional, Tuple, List, Union
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy.orm import Session
from loguru import logger

from app.config import config


# ==========================================================
# PHASE 2: INTENT DETECTION LAYER
# ==========================================================

class Intent(str, Enum):
    """All possible intents - pure detection only, no logic"""
    # DN Operations
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    DN_RISK = "dn_risk"
    
    # POD Operations
    PENDING_POD = "pending_pod"
    PENDING_POD_BY_DAYS = "pending_pod_by_days"
    
    # PGI Operations
    PENDING_PGI = "pending_pgi"
    PENDING_PGI_BY_DAYS = "pending_pgi_by_days"
    
    # Dealer Operations
    DEALER_QUERY = "dealer_query"
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    TOP_DEALERS = "top_dealers"
    BOTTOM_DEALERS = "bottom_dealers"
    DEALER_RISK = "dealer_risk"
    
    # Warehouse Operations
    WAREHOUSE_QUERY = "warehouse_query"
    WAREHOUSE_RANKING = "warehouse_ranking"
    TOP_WAREHOUSES = "top_warehouses"
    WAREHOUSE_DELAY = "warehouse_delay"
    WAREHOUSE_CAPACITY = "warehouse_capacity"
    
    # City/Region Operations
    CITY_QUERY = "city_query"
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    REGION_COMPARISON = "region_comparison"
    
    # Product Operations
    TOP_PRODUCTS = "top_products"
    PRODUCT_QUERY = "product_query"
    
    # KPI Operations
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    KPI_DASHBOARD = "kpi_dashboard"
    TOP_RISKS = "top_risks"
    TARGET_ACHIEVEMENT = "target_achievement"
    BRANCH_PERFORMANCE = "branch_performance"
    REGION_PERFORMANCE = "region_performance"
    
    # Control Tower
    CONTROL_TOWER = "control_tower"
    ALERTS = "alerts"
    CRITICAL_DNS = "critical_dns"
    ESCALATIONS = "escalations"
    
    # Pending Items (Combined)
    PENDING_ITEMS = "pending_items"
    
    # Analytics
    TREND_ANALYSIS = "trend_analysis"
    GROWTH_ANALYSIS = "growth_analysis"
    
    # AI Analysis
    ROOT_CAUSE = "root_cause"
    RECOMMENDATIONS = "recommendations"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    
    # General
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"
    SEARCH = "search"
    UNIVERSAL = "universal"


# ==========================================================
# PHASE 3: ENTITY EXTRACTION LAYER
# ==========================================================

@dataclass
class ExtractedEntities:
    """Pure entity extraction - no validation logic"""
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    warehouse: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    product: Optional[str] = None
    days: Optional[int] = None
    limit: Optional[int] = 10
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    compare_with: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def is_empty(self) -> bool:
        return not any([self.dn_number, self.dealer, self.warehouse, 
                       self.city, self.region, self.product])
    
    def has_any(self) -> bool:
        return not self.is_empty()


class EntityExtractor:
    """Pure entity extraction - NO business logic"""
    
    # Patterns
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DN_WITH_PREFIX = re.compile(r'DN\s*[:]?\s*(\d{8,15})', re.IGNORECASE)
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    LIMIT_PATTERN = re.compile(r'top\s+(\d+)', re.IGNORECASE)
    
    # Known entities
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot',
              'hyderabad', 'sukkur', 'bahawalpur', 'sargodha']
    
    REGIONS = ['north', 'south', 'east', 'west', 'central', 'punjab', 'sindh', 'kpk', 'balochistan']
    
    WAREHOUSES = ['north', 'south', 'east', 'west', 'central', 'main']
    
    @classmethod
    def extract(cls, question: str) -> ExtractedEntities:
        """Extract entities from question"""
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # Extract DN (highest priority)
        dn_match = cls.DN_WITH_PREFIX.search(question)
        if not dn_match:
            dn_match = cls.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(1)
        
        # Extract days
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        # Extract limit
        limit_match = cls.LIMIT_PATTERN.search(question_lower)
        if limit_match:
            limit = int(limit_match.group(1))
            if limit <= 50:
                entities.limit = limit
        
        # Extract city
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        
        # Extract region
        for region in cls.REGIONS:
            if region in question_lower:
                entities.region = region.capitalize()
                break
        
        # Extract warehouse
        for warehouse in cls.WAREHOUSES:
            if warehouse in question_lower:
                entities.warehouse = warehouse.capitalize()
                break
        
        # Extract dealer (multiple patterns)
        dealer_patterns = [
            r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance|dashboard|details|risk)',
            r'show\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'dealer\s+([A-Za-z0-9\s]+?)\s+(?:pod|delivery|pending|ranking)',
            r'for\s+dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)'
        ]
        
        for pattern in dealer_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.dealer = match.group(1).strip()
                break
        
        # Extract product
        product_patterns = [
            r'product\s+([A-Za-z0-9\s]+?)(?:\s+performance|\s+$|\.|\,)',
            r'show\s+product\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)',
            r'product\s+([A-Za-z0-9\s]+?)\s+(?:sales|ranking)'
        ]
        
        for pattern in product_patterns:
            match = re.search(pattern, question_lower)
            if match:
                entities.product = match.group(1).strip()
                break
        
        return entities


# ==========================================================
# PHASE 1: INTENT DETECTION (Pure)
# ==========================================================

class IntentDetector:
    """Pure intent detection - NO business logic"""
    
    # Intent keywords mapping
    INTENT_KEYWORDS = {
        Intent.DN_TIMELINE: ['timeline', 'journey', 'history', 'track', 'progress', 'status history'],
        Intent.DN_PRODUCTS: ['products', 'items', 'materials', 'what products', 'what items'],
        Intent.DN_RISK: ['risk', 'critical', 'problem', 'issue', 'concern'],
        
        Intent.PENDING_POD: ['pending pod', 'pod pending', 'missing pod', 'pod not received', 'pending proof'],
        Intent.PENDING_POD_BY_DAYS: ['pod >', 'pod greater than', 'pod older than', 'pending pod over'],
        
        Intent.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched', 'pending goods'],
        Intent.PENDING_PGI_BY_DAYS: ['pgi >', 'pgi greater than', 'pgi older than'],
        
        Intent.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing', 'leading dealer'],
        Intent.BOTTOM_DEALERS: ['bottom dealer', 'worst dealer', 'lowest performing', 'poor performing'],
        Intent.DEALER_RISK: ['dealer risk', 'risky dealer', 'high risk dealer', 'dealer problem'],
        
        Intent.TOP_WAREHOUSES: ['top warehouse', 'best warehouse', 'warehouse ranking'],
        Intent.WAREHOUSE_DELAY: ['warehouse delay', 'delay at warehouse', 'warehouse backlog'],
        Intent.WAREHOUSE_CAPACITY: ['warehouse capacity', 'capacity', 'utilization', 'space'],
        
        Intent.TOP_PRODUCTS: ['top products', 'best products', 'product ranking', 'top selling'],
        
        Intent.EXECUTIVE_DASHBOARD: ['executive dashboard', 'ceo dashboard', 'leadership', 'board view'],
        Intent.EXECUTIVE_SUMMARY: ['executive summary', 'ceo summary', 'high level', 'overview'],
        Intent.NETWORK_HEALTH: ['network health', 'system health', 'service status', 'health check'],
        Intent.KPI_DASHBOARD: ['kpi', 'key performance', 'metrics', 'dashboard', 'performance metrics'],
        Intent.TOP_RISKS: ['top risks', 'critical risks', 'risk assessment', 'risk report'],
        Intent.TARGET_ACHIEVEMENT: ['target', 'achievement', 'goal', 'vs target', 'target vs actual'],
        Intent.BRANCH_PERFORMANCE: ['branch performance', 'branch score', 'branch ranking'],
        Intent.REGION_PERFORMANCE: ['region performance', 'region score', 'regional performance'],
        
        Intent.CONTROL_TOWER: ['control tower', 'command center', 'overview', 'all alerts', 'mission control'],
        Intent.ALERTS: ['alert', 'warning', 'notification', 'attention needed'],
        Intent.CRITICAL_DNS: ['critical dn', 'emergency dn', 'urgent dn', 'red dn'],
        Intent.ESCALATIONS: ['escalation', 'escalate', 'vp level', 'director level'],
        
        Intent.PENDING_ITEMS: ['pending items', 'all pending', 'everything pending', 'complete pending'],
        
        Intent.TREND_ANALYSIS: ['trend', 'pattern', 'over time', 'monthly trend', 'weekly trend'],
        Intent.GROWTH_ANALYSIS: ['growth', 'growth rate', 'increasing', 'decreasing', 'improving'],
        
        Intent.ROOT_CAUSE: ['why', 'root cause', 'reason', 'cause', 'what caused', 'why is'],
        Intent.RECOMMENDATIONS: ['recommend', 'suggest', 'action', 'improve', 'how to fix', 'solution'],
        Intent.PREDICTIVE_ANALYSIS: ['predict', 'forecast', 'will happen', 'expected', 'future', 'estimate'],
        
        Intent.HELP: ['help', 'menu', 'what can you do', 'commands', 'how to use'],
        Intent.GREETING: ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening', 'greetings'],
    }
    
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Intent:
        """Detect intent from question and entities"""
        question_lower = question.lower().strip()
        
        # Priority 1: DN number present
        if entities.dn_number:
            if 'timeline' in question_lower or 'journey' in question_lower:
                return Intent.DN_TIMELINE
            elif 'product' in question_lower or 'item' in question_lower:
                return Intent.DN_PRODUCTS
            elif 'risk' in question_lower:
                return Intent.DN_RISK
            else:
                return Intent.DN_LOOKUP
        
        # Priority 2: Check for pending items (combined)
        if 'pending items' in question_lower or 'all pending' in question_lower:
            return Intent.PENDING_ITEMS
        
        # Priority 3: Check for comparison
        if 'compare' in question_lower or 'vs' in question_lower or 'versus' in question_lower:
            if 'region' in question_lower:
                return Intent.REGION_COMPARISON
            if 'branch' in question_lower:
                return Intent.BRANCH_PERFORMANCE
        
        # Priority 4: Check by keywords
        for intent, keywords in cls.INTENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in question_lower:
                    # Special handling for days-based intents
                    if intent == Intent.PENDING_POD_BY_DAYS and entities.days:
                        return intent
                    if intent == Intent.PENDING_PGI_BY_DAYS and entities.days:
                        return intent
                    return intent
        
        # Priority 5: Entity-based routing
        if entities.dealer:
            if 'risk' in question_lower:
                return Intent.DEALER_RISK
            elif 'dashboard' in question_lower or 'performance' in question_lower:
                return Intent.DEALER_DASHBOARD
            else:
                return Intent.DEALER_QUERY
        
        if entities.city:
            if 'ranking' in question_lower:
                return Intent.CITY_RANKING
            elif 'dashboard' in question_lower or 'performance' in question_lower:
                return Intent.CITY_DASHBOARD
            else:
                return Intent.CITY_QUERY
        
        if entities.region:
            if 'compare' in question_lower or 'comparison' in question_lower:
                return Intent.REGION_COMPARISON
            else:
                return Intent.REGION_PERFORMANCE
        
        if entities.warehouse:
            if 'delay' in question_lower:
                return Intent.WAREHOUSE_DELAY
            elif 'capacity' in question_lower:
                return Intent.WAREHOUSE_CAPACITY
            else:
                return Intent.WAREHOUSE_QUERY
        
        if entities.product:
            return Intent.PRODUCT_QUERY
        
        # Default to general/universal
        if '?' in question and len(question.split()) > 5:
            return Intent.GENERAL
        
        return Intent.UNIVERSAL


# ==========================================================
# PHASE 4: RESPONSE FORMATTER (Pure)
# ==========================================================

class ResponseFormatter:
    """Pure response formatting - NO business logic"""
    
    @staticmethod
    def format_success(data: Any, summary: str = None, recommendations: List[str] = None) -> Dict:
        return {
            "success": True,
            "data": data,
            "summary": summary or "",
            "recommendations": recommendations or [],
            "source": "service"
        }
    
    @staticmethod
    def format_error(message: str, code: str = "unknown") -> Dict:
        return {
            "success": False,
            "data": {},
            "summary": message,
            "error_code": code,
            "source": "error"
        }
    
    @staticmethod
    def to_whatsapp(response: Dict) -> str:
        """Convert response to WhatsApp message"""
        if not response.get("success"):
            return f"❌ {response.get('summary', 'Unable to process request')}"
        
        summary = response.get("summary", "")
        data = response.get("data", {})
        
        # If data has a pre-formatted message
        if isinstance(data, dict):
            if data.get("whatsapp_message"):
                return data["whatsapp_message"]
            if data.get("response"):
                return data["response"]
        
        # If summary exists, use it
        if summary:
            # Add recommendations if available
            recommendations = response.get("recommendations", [])
            if recommendations:
                summary += "\n\n💡 *Recommendations:*\n"
                for rec in recommendations[:3]:
                    summary += f"• {rec}\n"
            return summary
        
        # Fallback
        return "✅ Request processed successfully"
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• `6243612278` - Check DN status
• `DN timeline` - Journey history
• `DN products` - Items in DN
• `DN risk` - Risk assessment

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches
• `Pending items` - All pending items

🏪 *Dealer Analytics*
• `Top dealers` - Dealer rankings
• `Bottom dealers` - Lowest performers
• `Dealer ABC` - Specific dealer details
• `Dealer risk` - Risk assessment

🏭 *Warehouse Analytics*
• `Top warehouses` - Warehouse rankings
• `Warehouse delay` - Delays by warehouse
• `Warehouse capacity` - Capacity utilization

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Top risks` - Critical issues
• `Target achievement` - Goal tracking

🌍 *Region & Branch*
• `Region performance` - Regional metrics
• `Branch performance` - Branch scores
• `Compare regions` - Region comparison

📈 *Trends & Analysis*
• `Trend analysis` - Performance trends
• `Growth analysis` - Growth metrics
• `Root cause` - Why issues happen
• `Recommendations` - Action items

🚨 *Control Tower*
• `Control tower` - Critical alerts
• `Alerts` - System warnings
• `Critical DNs` - Urgent deliveries
• `Escalations` - Items needing attention

❓ *General*
• `Help` - This menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
*Powered by Enterprise Logistics Intelligence v33.0*
"""
    
    @staticmethod
    def format_greeting() -> str:
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
• `Control tower` - Critical alerts
• `Executive dashboard` - KPI overview
"""


# ==========================================================
# PHASE 5: MAIN AI QUERY SERVICE (PURE ROUTER ONLY)
# ==========================================================

class AIQueryService:
    """
    PURE ROUTER ONLY - Single Brain for Query Routing
    
    Responsibilities (ONLY):
    1. Detect Intent
    2. Extract Entities
    3. Route to Correct Service
    4. Format Response for WhatsApp
    
    NO business logic, NO KPI calculations, NO analytics, NO Groq prompts
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.intent_detector = IntentDetector()
        self.entity_extractor = EntityExtractor()
        self.formatter = ResponseFormatter()
        
        # Lazy-loaded services
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        self._ai_provider = None
        
        # Request tracking
        self.start_time = None
        self.request_id = None
        
        logger.info("✅ AI Query Service v33.0 - Pure Router Mode")
    
    @property
    def logistics_service(self):
        if self._logistics_service is None:
            try:
                from app.services.logistics_query_service import LogisticsQueryService
                self._logistics_service = LogisticsQueryService(self.db)
                logger.debug("LogisticsQueryService loaded")
            except Exception as e:
                logger.error(f"Failed to load LogisticsQueryService: {e}")
        return self._logistics_service
    
    @property
    def analytics_service(self):
        if self._analytics_service is None:
            try:
                from app.services.analytics_service import AnalyticsService
                self._analytics_service = AnalyticsService(self.db)
                logger.debug("AnalyticsService loaded")
            except Exception as e:
                logger.error(f"Failed to load AnalyticsService: {e}")
        return self._analytics_service
    
    @property
    def kpi_service(self):
        if self._kpi_service is None:
            try:
                from app.services.kpi_service import KPIService
                self._kpi_service = KPIService(self.db)
                logger.debug("KPIService loaded")
            except Exception as e:
                logger.error(f"Failed to load KPIService: {e}")
        return self._kpi_service
    
    @property
    def ai_provider(self):
        if self._ai_provider is None:
            try:
                from app.services.ai_provider_service import get_ai_provider
                self._ai_provider = get_ai_provider()
                logger.debug("AI Provider loaded")
            except Exception as e:
                logger.error(f"Failed to load AI Provider: {e}")
        return self._ai_provider
    
    # ==========================================================
    # MAIN PROCESSING PIPELINE
    # ==========================================================
    
    def process_query(self, question: str, user_phone: str = None) -> Dict:
        """
        Main entry point - Pure routing pipeline
        
        Pipeline:
        1. Extract Entities
        2. Detect Intent
        3. Route to Service
        4. Format Response
        """
        import time
        self.start_time = time.time()
        
        logger.info(f"Processing: {question[:100]}")
        
        # Step 1: Extract entities
        entities = self.entity_extractor.extract(question)
        logger.debug(f"Entities: {entities.to_dict()}")
        
        # Step 2: Detect intent
        intent = self.intent_detector.detect(question, entities)
        logger.info(f"Intent: {intent.value}")
        
        # Step 3: Route to appropriate service
        result = self._route(intent, entities, question)
        
        # Step 4: Format response for WhatsApp
        whatsapp_message = self.formatter.to_whatsapp(result)
        
        elapsed_ms = (time.time() - self.start_time) * 1000
        logger.info(f"Response generated in {elapsed_ms:.0f}ms")
        
        return {
            "success": result.get("success", True),
            "response": whatsapp_message,
            "intent": intent.value,
            "entities": entities.to_dict(),
            "processing_time_ms": round(elapsed_ms, 2)
        }
    
    # ==========================================================
    # PHASE 4: SERVICE ROUTER (Corrected Method Names)
    # ==========================================================
    
    def _route(self, intent: Intent, entities: ExtractedEntities, question: str) -> Dict:
        """Route to appropriate service based on intent"""
        
        # ========== DN Routes ==========
        if intent == Intent.DN_LOOKUP:
            return self._call_logistics("get_complete_dn_intelligence", entities.dn_number)
        
        if intent == Intent.DN_TIMELINE:
            return self._call_logistics("get_dn_timeline", entities.dn_number)
        
        if intent == Intent.DN_PRODUCTS:
            return self._call_logistics("get_dn_products", entities.dn_number)
        
        if intent == Intent.DN_RISK:
            return self._call_logistics_with_risk("get_complete_dn_intelligence", entities.dn_number)
        
        # ========== POD Routes ==========
        if intent == Intent.PENDING_POD:
            return self._call_logistics("get_pod_status", None)
        
        if intent == Intent.PENDING_POD_BY_DAYS:
            return self._call_logistics("get_pending_pods", entities.days)
        
        # ========== PGI Routes ==========
        if intent == Intent.PENDING_PGI:
            return self._call_logistics("get_pending_pgi", None)
        
        if intent == Intent.PENDING_PGI_BY_DAYS:
            return self._call_logistics("get_pending_pgi", entities.days)
        
        # ========== Pending Items (Combined) ==========
        if intent == Intent.PENDING_ITEMS:
            return self._call_logistics("get_pending_items", entities.region)
        
        # ========== Dealer Routes ==========
        if intent == Intent.DEALER_QUERY or intent == Intent.DEALER_DASHBOARD:
            return self._call_analytics("get_dealer_performance", entities.dealer)
        
        if intent == Intent.TOP_DEALERS:
            return self._call_analytics("get_top_dealers", entities.limit)
        
        if intent == Intent.BOTTOM_DEALERS:
            return self._call_analytics("get_top_dealers", entities.limit)
        
        if intent == Intent.DEALER_RISK:
            return self._call_analytics_with_risk("get_dealer_performance", entities.dealer)
        
        if intent == Intent.DEALER_RANKING:
            return self._call_analytics("get_top_dealers", entities.limit)
        
        # ========== Warehouse Routes ==========
        if intent == Intent.WAREHOUSE_QUERY:
            return self._call_analytics("get_warehouse_status", entities.warehouse)
        
        if intent == Intent.TOP_WAREHOUSES:
            return self._call_analytics("get_top_warehouses", entities.limit)
        
        if intent == Intent.WAREHOUSE_DELAY:
            return self._call_analytics("get_warehouse_status", entities.warehouse)
        
        if intent == Intent.WAREHOUSE_CAPACITY:
            return self._call_analytics("get_warehouse_status", entities.warehouse)
        
        if intent == Intent.WAREHOUSE_RANKING:
            return self._call_analytics("get_top_warehouses", entities.limit)
        
        # ========== Product Routes ==========
        if intent == Intent.TOP_PRODUCTS:
            return self._call_analytics("get_top_products", entities.limit)
        
        if intent == Intent.PRODUCT_QUERY:
            return self._call_analytics("get_top_products", entities.limit)
        
        # ========== City/Region Routes ==========
        if intent == Intent.CITY_DASHBOARD:
            return self._call_logistics("get_region_performance", entities.city)
        
        if intent == Intent.CITY_QUERY:
            return self._call_logistics("get_region_performance", entities.city)
        
        if intent == Intent.REGION_PERFORMANCE:
            return self._call_logistics("get_region_performance", entities.region)
        
        if intent == Intent.REGION_COMPARISON:
            return self._call_analytics("get_region_comparison", 90)
        
        if intent == Intent.BRANCH_PERFORMANCE:
            return self._call_kpi("get_branch_performance", 30)
        
        # ========== KPI Routes ==========
        if intent == Intent.EXECUTIVE_DASHBOARD:
            return self._call_kpi("get_executive_dashboard", 30)
        
        if intent == Intent.EXECUTIVE_SUMMARY:
            return self._call_kpi("get_executive_dashboard", 30)
        
        if intent == Intent.NETWORK_HEALTH:
            return self._call_kpi("get_network_health", 30)
        
        if intent == Intent.KPI_DASHBOARD:
            return self._call_kpi("get_all_kpis", {"type": "month"})
        
        if intent == Intent.TOP_RISKS:
            return self._call_kpi("get_risk_alerts")
        
        if intent == Intent.TARGET_ACHIEVEMENT:
            return self._call_kpi("get_target_vs_actual", 30)
        
        # ========== Control Tower Routes ==========
        if intent == Intent.CONTROL_TOWER:
            return self._call_control_tower()
        
        if intent == Intent.ALERTS:
            return self._call_kpi("get_risk_alerts")
        
        if intent == Intent.CRITICAL_DNS:
            return self._call_kpi("get_critical_delays", 7, 20)
        
        if intent == Intent.ESCALATIONS:
            return self._call_kpi("get_escalations", 7, 30)
        
        # ========== Analytics Routes ==========
        if intent == Intent.TREND_ANALYSIS:
            return self._call_analytics("get_trend_analysis", "monthly", 6)
        
        if intent == Intent.GROWTH_ANALYSIS:
            return self._call_analytics("get_growth_analysis", 6)
        
        # ========== AI Analysis Routes ==========
        if intent == Intent.ROOT_CAUSE:
            return self._call_ai("root_cause", question)
        
        if intent == Intent.RECOMMENDATIONS:
            return self._call_ai("recommendations", question)
        
        if intent == Intent.PREDICTIVE_ANALYSIS:
            return self._call_ai("predictive", question)
        
        # ========== General Routes ==========
        if intent == Intent.HELP:
            return self.formatter.format_success({}, self.formatter.format_help())
        
        if intent == Intent.GREETING:
            return self.formatter.format_success({}, self.formatter.format_greeting())
        
        # ========== Universal/Search/General ==========
        if intent == Intent.UNIVERSAL or intent == Intent.SEARCH or intent == Intent.GENERAL:
            # Try to extract DN from question if not already
            if not entities.dn_number:
                dn_match = EntityExtractor.DN_PATTERN.search(question)
                if dn_match:
                    entities.dn_number = dn_match.group(1)
                    return self._call_logistics("get_complete_dn_intelligence", entities.dn_number)
            
            # Try to extract dealer
            if not entities.dealer:
                dealer_match = re.search(r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)', question)
                if dealer_match and len(dealer_match.group(1)) > 3:
                    entities.dealer = dealer_match.group(1).strip()
                    return self._call_analytics("get_dealer_performance", entities.dealer)
            
            # Default to AI chat
            return self._call_ai("general", question)
        
        # Ultimate fallback
        return self.formatter.format_error("Unable to process your request. Please try a different query or type 'Help'.")
    
    # ==========================================================
    # SERVICE CALL WRAPPERS (Corrected for existing methods)
    # ==========================================================
    
    def _call_logistics(self, method: str, *args, **kwargs) -> Dict:
        """Call logistics service with error handling"""
        if not self.logistics_service:
            return self.formatter.format_error("Logistics service unavailable")
        
        try:
            service_method = getattr(self.logistics_service, method, None)
            if not service_method:
                # Try alternative method names
                alternatives = {
                    "get_pod_status": ["get_pending_pods", "get_pod_performance"],
                    "get_region_performance": ["get_region_information", "get_region_metrics"],
                    "get_pending_pods": ["get_pending_pods_by_days"],
                    "get_pending_items": ["get_pending_pods", "get_pending_pgi", "get_pending_deliveries"],
                }
                
                if method in alternatives:
                    for alt in alternatives[method]:
                        alt_method = getattr(self.logistics_service, alt, None)
                        if alt_method:
                            service_method = alt_method
                            logger.debug(f"Using alternative method: {alt} for {method}")
                            break
                
                if not service_method:
                    return self.formatter.format_error(f"Method '{method}' not available in logistics service")
            
            result = service_method(*args, **kwargs)
            
            # Handle different return types
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            elif isinstance(result, list):
                if not result:
                    return self.formatter.format_error("No data found")
                return self.formatter.format_success({"items": result, "count": len(result)})
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"Logistics call failed: {method} - {e}")
            return self.formatter.format_error(str(e))
    
    def _call_logistics_with_risk(self, method: str, dn_number: str) -> Dict:
        """Get DN data and calculate risk score"""
        result = self._call_logistics(method, dn_number)
        
        if result.get("success") and result.get("data"):
            data = result["data"]
            
            # Extract aging days and amount
            aging_days = data.get("aging_days", 0)
            if isinstance(data.get("aging"), dict):
                aging_days = data.get("aging", {}).get("max_delay", aging_days)
            
            amount = data.get("amount", 0)
            if isinstance(data.get("total_value"), (int, float)):
                amount = data.get("total_value", amount)
            
            # Risk calculation
            if aging_days > 7 and amount > 1000000:
                risk = "Critical"
                icon = "🔴"
                action = "Immediate escalation required"
            elif aging_days > 3 and amount > 500000:
                risk = "High"
                icon = "🟠"
                action = "Review within 24 hours"
            elif aging_days > 1:
                risk = "Medium"
                icon = "🟡"
                action = "Monitor closely"
            else:
                risk = "Low"
                icon = "🟢"
                action = "No action needed"
            
            result["data"]["risk_level"] = risk
            result["data"]["risk_icon"] = icon
            result["data"]["risk_action"] = action
            result["summary"] = f"⚠️ Risk Level: {risk} {icon}\nDays: {aging_days} | Amount: ₹{amount:,.0f}\nAction: {action}"
        
        return result
    
    def _call_analytics(self, method: str, *args, **kwargs) -> Dict:
        """Call analytics service with error handling"""
        if not self.analytics_service:
            return self.formatter.format_error("Analytics service unavailable")
        
        try:
            service_method = getattr(self.analytics_service, method, None)
            if not service_method:
                # Try alternative method names
                alternatives = {
                    "get_dealer_performance": ["get_dealer_details", "get_dealer_info", "get_dealer_dashboard"],
                    "get_warehouse_status": ["get_warehouse_info", "get_warehouse_details", "get_warehouse_performance"],
                    "get_top_dealers": ["get_dealer_ranking", "get_top_performing_dealers"],
                    "get_top_warehouses": ["get_warehouse_ranking", "get_top_performing_warehouses"],
                    "get_top_products": ["get_product_ranking", "get_top_selling_products"],
                    "get_region_comparison": ["compare_regions", "region_vs_region"],
                    "get_trend_analysis": ["get_trends", "analyze_trends"],
                    "get_growth_analysis": ["get_growth", "analyze_growth"],
                }
                
                if method in alternatives:
                    for alt in alternatives[method]:
                        alt_method = getattr(self.analytics_service, alt, None)
                        if alt_method:
                            service_method = alt_method
                            logger.debug(f"Using alternative method: {alt} for {method}")
                            break
                
                if not service_method:
                    return self.formatter.format_error(f"Method '{method}' not available in analytics service")
            
            result = service_method(*args, **kwargs)
            
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            elif isinstance(result, list):
                if not result:
                    return self.formatter.format_error("No data found")
                return self.formatter.format_success({"items": result, "count": len(result)})
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"Analytics call failed: {method} - {e}")
            return self.formatter.format_error(str(e))
    
    def _call_analytics_with_risk(self, method: str, dealer: str) -> Dict:
        """Get dealer data and calculate risk"""
        result = self._call_analytics(method, dealer)
        
        if result.get("success") and result.get("data"):
            data = result["data"]
            pending = data.get("pending_count", 0)
            if isinstance(data.get("pending_dns"), int):
                pending = data.get("pending_dns", pending)
            
            total_dns = data.get("total_dns", 0)
            completion_rate = data.get("completion_rate", 100)
            if isinstance(data.get("success_rate"), (int, float)):
                completion_rate = data.get("success_rate", completion_rate)
            
            if pending > 10 or completion_rate < 70:
                risk = "High"
                icon = "🔴"
                reason = f"High pending ({pending}) or low completion ({completion_rate}%)"
            elif pending > 5 or completion_rate < 85:
                risk = "Medium"
                icon = "🟡"
                reason = f"Moderate pending ({pending})"
            else:
                risk = "Low"
                icon = "🟢"
                reason = f"Healthy performance"
            
            result["data"]["risk_level"] = risk
            result["data"]["risk_icon"] = icon
            result["data"]["risk_reason"] = reason
            result["summary"] = f"📊 Dealer Risk: {risk} {icon}\n{reason}"
        
        return result
    
    def _call_kpi(self, method: str, *args, **kwargs) -> Dict:
        """Call KPI service with error handling"""
        if not self.kpi_service:
            return self.formatter.format_error("KPI service unavailable")
        
        try:
            service_method = getattr(self.kpi_service, method, None)
            if not service_method:
                # Try alternative method names
                alternatives = {
                    "get_all_kpis": ["get_kpi_dashboard", "get_kpis", "get_performance_metrics"],
                    "get_risk_alerts": ["get_alerts", "get_risks", "get_active_alerts"],
                    "get_critical_delays": ["get_critical_items", "get_delays", "get_pending_delays"],
                    "get_escalations": ["get_escalation_items", "get_urgent_items"],
                    "get_branch_performance": ["get_branch_scores", "get_branch_metrics"],
                    "get_target_vs_actual": ["get_target_achievement", "get_goal_metrics"],
                    "get_network_health": ["get_system_health", "get_service_status"],
                    "get_executive_dashboard": ["get_dashboard", "get_executive_metrics"],
                }
                
                if method in alternatives:
                    for alt in alternatives[method]:
                        alt_method = getattr(self.kpi_service, alt, None)
                        if alt_method:
                            service_method = alt_method
                            logger.debug(f"Using alternative method: {alt} for {method}")
                            break
                
                if not service_method:
                    return self.formatter.format_error(f"Method '{method}' not available in KPI service")
            
            result = service_method(*args, **kwargs)
            
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"KPI call failed: {method} - {e}")
            return self.formatter.format_error(str(e))
    
    def _call_control_tower(self) -> Dict:
        """Combine multiple services for control tower view"""
        alerts = {}
        
        # Get pending PODs
        if self.logistics_service:
            try:
                pod_result = self.logistics_service.get_pod_status()
                if pod_result and not isinstance(pod_result, dict) or not pod_result.get("error"):
                    alerts["pending_pods"] = pod_result
            except Exception as e:
                logger.error(f"Failed to get POD status: {e}")
        
        # Get pending PGI
        if self.logistics_service:
            try:
                pgi_result = self.logistics_service.get_pending_pgi()
                if pgi_result and (isinstance(pgi_result, list) or not pgi_result.get("error")):
                    alerts["pending_pgi"] = pgi_result
            except Exception as e:
                logger.error(f"Failed to get PGI status: {e}")
        
        # Get critical delays
        if self.kpi_service:
            try:
                delays = self.kpi_service.get_critical_delays(7, 20)
                if delays and not delays.get("error"):
                    alerts["critical_delays"] = delays
            except Exception as e:
                logger.error(f"Failed to get delays: {e}")
        
        # Get risk alerts
        if self.kpi_service:
            try:
                risks = self.kpi_service.get_risk_alerts()
                if risks and not risks.get("error"):
                    alerts["risk_alerts"] = risks
            except Exception as e:
                logger.error(f"Failed to get risk alerts: {e}")
        
        # Create summary
        pending_pod_count = 0
        if isinstance(alerts.get("pending_pods"), dict):
            pending_pod_count = alerts["pending_pods"].get("pending_count", 0)
        
        pending_pgi_count = len(alerts.get("pending_pgi", [])) if isinstance(alerts.get("pending_pgi"), list) else 0
        
        critical_count = 0
        if isinstance(alerts.get("critical_delays"), dict):
            critical_count = alerts["critical_delays"].get("critical_count", 0)
        
        alert_count = 0
        if isinstance(alerts.get("risk_alerts"), dict):
            alert_count = alerts["risk_alerts"].get("total_alerts", 0)
        
        summary = f"🚨 *CONTROL TOWER REPORT*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        summary += f"📋 Pending PODs: {pending_pod_count}\n"
        summary += f"🚚 Pending PGI: {pending_pgi_count}\n"
        summary += f"⚠️ Critical Delays: {critical_count}\n"
        summary += f"🔔 Active Alerts: {alert_count}\n"
        
        if critical_count > 0 or alert_count > 0:
            summary += f"\n🔴 *IMMEDIATE ATTENTION REQUIRED* 🔴\n"
            summary += f"Please review critical delays and alerts."
        elif pending_pod_count > 0 or pending_pgi_count > 0:
            summary += f"\n🟡 *Action Needed*\n"
            summary += f"Pending items require follow-up."
        else:
            summary += f"\n✅ *All Systems Operational*\n"
            summary += f"No critical issues detected."
        
        return self.formatter.format_success(alerts, summary)
    
    def _call_ai(self, analysis_type: str, question: str) -> Dict:
        """Call AI provider for analysis"""
        if not self.ai_provider:
            return self.formatter.format_error("AI service unavailable. Please try again later.")
        
        try:
            # Map analysis type to provider method
            if analysis_type == "root_cause":
                # Gather context for root cause analysis
                context = {"question": question, "type": "root_cause"}
                result = self.ai_provider.generate_root_cause_analysis(question, context)
            elif analysis_type == "recommendations":
                context = {"question": question, "type": "recommendations"}
                result = self.ai_provider.generate_recommendations([], context)
            elif analysis_type == "predictive":
                context = {"question": question, "type": "predictive"}
                result = self.ai_provider.generate_predictive_analysis(question, context)
            else:
                # General chat
                result = self.ai_provider.chat(question, "guest")
            
            # Extract response text
            response_text = result
            if isinstance(result, dict):
                response_text = result.get("response") or result.get("insight") or result.get("answer") or str(result)
            elif not isinstance(result, str):
                response_text = str(result)
            
            if not response_text or len(response_text) < 10:
                response_text = "I understand your question, but I need more context to provide a meaningful answer. Could you please provide more details?"
            
            return self.formatter.format_success({"insight": response_text}, response_text)
            
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self.formatter.format_error(f"AI service error: {str(e)[:100]}")
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict:
        """Health check for monitoring"""
        return {
            "service": "ai_query_service",
            "version": "33.0",
            "mode": "pure_router",
            "status": "healthy",
            "services": {
                "logistics": self._logistics_service is not None,
                "analytics": self._analytics_service is not None,
                "kpi": self._kpi_service is not None,
                "ai": self._ai_provider is not None
            }
        }
    
    def get_status(self) -> Dict:
        """Get service status"""
        return {
            "service": "AI Query Service",
            "version": "33.0",
            "status": "running",
            "pure_router_mode": True,
            "capabilities": {
                "intent_detection": True,
                "entity_extraction": True,
                "routing": True,
                "response_formatting": True
            }
        }


# ==========================================================
# FACTORY FUNCTION (Entry point for webhook)
# ==========================================================

def process_whatsapp_query(
    question: str,
    db: Session,
    phone_number: str = None,
    user_id: str = None
) -> str:
    """
    Main entry point for WhatsApp queries.
    
    This is the ONLY function called from webhook.py
    
    Args:
        question: User's question text
        db: Database session
        phone_number: User's phone number
        user_id: User identifier
    
    Returns:
        Formatted response string for WhatsApp
    """
    try:
        service = AIQueryService(db)
        result = service.process_query(question, phone_number or user_id)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


def health_check(db: Session) -> Dict:
    """Health check for monitoring"""
    try:
        service = AIQueryService(db)
        return service.health_check()
    except Exception as e:
        logger.exception(f"Health check failed: {e}")
        return {
            "service": "ai_query_service",
            "status": "unhealthy",
            "error": str(e),
            "version": "33.0"
        }


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("🧠 AI QUERY SERVICE v33.0 - PURE ROUTER MODE")
logger.info("   Responsibilities: Intent → Extract → Route → Format")
logger.info("   NO Business Rules | NO KPI Logic | NO Analytics | NO AI Prompts")
logger.info("   Integrated with: Logistics | Analytics | KPI | AI Provider")
logger.info("=" * 70)
