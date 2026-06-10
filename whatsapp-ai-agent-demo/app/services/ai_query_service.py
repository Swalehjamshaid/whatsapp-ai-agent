# ==========================================================
# FILE: app/services/ai_query_service.py (REFACTORED v32.0)
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
from typing import Dict, Any, Optional, Tuple, List
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
    DEALER_RISK = "dealer_risk"
    
    # Warehouse Operations
    WAREHOUSE_QUERY = "warehouse_query"
    WAREHOUSE_RANKING = "warehouse_ranking"
    TOP_WAREHOUSES = "top_warehouses"
    WAREHOUSE_DELAY = "warehouse_delay"
    
    # City/Region Operations
    CITY_QUERY = "city_query"
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    
    # Product Operations
    TOP_PRODUCTS = "top_products"
    PRODUCT_QUERY = "product_query"
    
    # KPI Operations
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    KPI_DASHBOARD = "kpi_dashboard"
    TOP_RISKS = "top_risks"
    TARGET_ACHIEVEMENT = "target_achievement"
    
    # Control Tower
    CONTROL_TOWER = "control_tower"
    ALERTS = "alerts"
    CRITICAL_DNS = "critical_dns"
    
    # AI Analysis
    ROOT_CAUSE = "root_cause"
    RECOMMENDATIONS = "recommendations"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    TREND_ANALYSIS = "trend_analysis"
    
    # General
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"


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
    
    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}
    
    def is_empty(self) -> bool:
        return not any([self.dn_number, self.dealer, self.warehouse, 
                       self.city, self.region, self.product])


class EntityExtractor:
    """Pure entity extraction - NO business logic"""
    
    # Patterns
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DN_WITH_PREFIX = re.compile(r'DN\s*[:]?\s*(\d{8,15})', re.IGNORECASE)
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    
    # Known entities
    CITIES = ['karachi', 'lahore', 'islamabad', 'rawalpindi', 'faisalabad', 
              'multan', 'peshawar', 'quetta', 'gujranwala', 'sialkot']
    
    WAREHOUSES = ['north', 'south', 'east', 'west', 'central']
    
    @classmethod
    def extract(cls, question: str) -> ExtractedEntities:
        """Extract entities from question"""
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # Extract DN
        dn_match = cls.DN_WITH_PREFIX.search(question)
        if not dn_match:
            dn_match = cls.DN_PATTERN.search(question)
        if dn_match:
            entities.dn_number = dn_match.group(1)
        
        # Extract days
        days_match = cls.DAYS_PATTERN.search(question_lower)
        if days_match:
            entities.days = int(days_match.group(1))
        
        # Extract city
        for city in cls.CITIES:
            if city in question_lower:
                entities.city = city.capitalize()
                break
        
        # Extract warehouse
        for warehouse in cls.WAREHOUSES:
            if warehouse in question_lower:
                entities.warehouse = warehouse.capitalize()
                break
        
        # Extract dealer (simple pattern)
        dealer_match = re.search(r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,|performance)', question_lower)
        if dealer_match:
            entities.dealer = dealer_match.group(1).strip()
        
        # Extract product
        product_match = re.search(r'product\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)', question_lower)
        if product_match:
            entities.product = product_match.group(1).strip()
        
        return entities


# ==========================================================
# PHASE 1: INTENT DETECTION (Pure)
# ==========================================================

class IntentDetector:
    """Pure intent detection - NO business logic"""
    
    # Intent keywords mapping
    INTENT_KEYWORDS = {
        Intent.DN_TIMELINE: ['timeline', 'journey', 'history', 'track', 'progress'],
        Intent.DN_PRODUCTS: ['products', 'items', 'materials'],
        Intent.DN_RISK: ['risk', 'critical', 'problem', 'issue'],
        
        Intent.PENDING_POD: ['pending pod', 'pod pending', 'missing pod', 'pod not received'],
        Intent.PENDING_POD_BY_DAYS: ['pod >', 'pod greater than', 'pod older than'],
        
        Intent.PENDING_PGI: ['pending pgi', 'pgi pending', 'pending dispatch', 'not dispatched'],
        Intent.PENDING_PGI_BY_DAYS: ['pgi >', 'pgi greater than'],
        
        Intent.TOP_DEALERS: ['top dealer', 'best dealer', 'dealer ranking', 'top performing'],
        Intent.DEALER_RISK: ['dealer risk', 'risky dealer', 'high risk dealer'],
        
        Intent.TOP_WAREHOUSES: ['top warehouse', 'best warehouse', 'warehouse ranking'],
        Intent.WAREHOUSE_DELAY: ['warehouse delay', 'delay at warehouse'],
        
        Intent.TOP_PRODUCTS: ['top products', 'best products', 'product ranking'],
        
        Intent.EXECUTIVE_DASHBOARD: ['executive dashboard', 'ceo dashboard', 'leadership'],
        Intent.NETWORK_HEALTH: ['network health', 'system health', 'service status'],
        Intent.KPI_DASHBOARD: ['kpi', 'key performance', 'metrics', 'dashboard'],
        Intent.TOP_RISKS: ['top risks', 'critical risks', 'risk assessment'],
        Intent.TARGET_ACHIEVEMENT: ['target', 'achievement', 'goal', 'vs target'],
        
        Intent.CONTROL_TOWER: ['control tower', 'command center', 'overview'],
        Intent.ALERTS: ['alert', 'warning', 'notification'],
        Intent.CRITICAL_DNS: ['critical dn', 'emergency dn', 'urgent dn'],
        
        Intent.ROOT_CAUSE: ['why', 'root cause', 'reason', 'what caused'],
        Intent.RECOMMENDATIONS: ['recommend', 'suggest', 'action', 'improve'],
        Intent.PREDICTIVE_ANALYSIS: ['predict', 'forecast', 'will happen', 'expected'],
        Intent.TREND_ANALYSIS: ['trend', 'pattern', 'over time'],
        
        Intent.HELP: ['help', 'menu', 'what can you do', 'commands'],
        Intent.GREETING: ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening'],
    }
    
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Intent:
        """Detect intent from question and entities"""
        question_lower = question.lower().strip()
        
        # Priority: DN number present
        if entities.dn_number:
            if 'timeline' in question_lower or 'journey' in question_lower:
                return Intent.DN_TIMELINE
            elif 'product' in question_lower or 'item' in question_lower:
                return Intent.DN_PRODUCTS
            elif 'risk' in question_lower:
                return Intent.DN_RISK
            else:
                return Intent.DN_LOOKUP
        
        # Check by keywords
        for intent, keywords in cls.INTENT_KEYWORDS.items():
            for keyword in keywords:
                if keyword in question_lower:
                    # Special handling for days-based intents
                    if intent == Intent.PENDING_POD_BY_DAYS and entities.days:
                        return intent
                    if intent == Intent.PENDING_PGI_BY_DAYS and entities.days:
                        return intent
                    return intent
        
        # Entity-based routing
        if entities.dealer:
            if 'risk' in question_lower:
                return Intent.DEALER_RISK
            elif 'dashboard' in question_lower:
                return Intent.DEALER_DASHBOARD
            else:
                return Intent.DEALER_QUERY
        
        if entities.city:
            if 'ranking' in question_lower:
                return Intent.CITY_RANKING
            elif 'dashboard' in question_lower:
                return Intent.CITY_DASHBOARD
            else:
                return Intent.CITY_QUERY
        
        if entities.warehouse:
            if 'delay' in question_lower:
                return Intent.WAREHOUSE_DELAY
            else:
                return Intent.WAREHOUSE_QUERY
        
        if entities.product:
            return Intent.PRODUCT_QUERY
        
        # Default to general
        return Intent.GENERAL


# ==========================================================
# PHASE 4: RESPONSE FORMATTER (Pure)
# ==========================================================

class ResponseFormatter:
    """Pure response formatting - NO business logic"""
    
    @staticmethod
    def format_success(data: Any, summary: str = None) -> Dict:
        return {
            "success": True,
            "data": data,
            "summary": summary or "",
            "source": "service"
        }
    
    @staticmethod
    def format_error(message: str) -> Dict:
        return {
            "success": False,
            "data": {},
            "summary": message,
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
        if isinstance(data, dict) and data.get("whatsapp_message"):
            return data["whatsapp_message"]
        
        # If summary exists, use it
        if summary:
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

🏪 *Dealer Analytics*
• `Top dealers` - Dealer rankings
• `Dealer ABC` - Specific dealer details

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status
• `Top risks` - Critical issues

🚨 *Control Tower*
• `Control tower` - Critical alerts

❓ *General*
• `Help` - This menu

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
        
        logger.info("✅ AI Query Service v32.0 - Pure Router Mode")
    
    @property
    def logistics_service(self):
        if self._logistics_service is None:
            try:
                from app.services.logistics_query_service import LogisticsQueryService
                self._logistics_service = LogisticsQueryService(self.db)
            except Exception as e:
                logger.error(f"Failed to load LogisticsQueryService: {e}")
        return self._logistics_service
    
    @property
    def analytics_service(self):
        if self._analytics_service is None:
            try:
                from app.services.analytics_service import AnalyticsService
                self._analytics_service = AnalyticsService(self.db)
            except Exception as e:
                logger.error(f"Failed to load AnalyticsService: {e}")
        return self._analytics_service
    
    @property
    def kpi_service(self):
        if self._kpi_service is None:
            try:
                from app.services.kpi_service import KPIService
                self._kpi_service = KPIService(self.db)
            except Exception as e:
                logger.error(f"Failed to load KPIService: {e}")
        return self._kpi_service
    
    @property
    def ai_provider(self):
        if self._ai_provider is None:
            try:
                from app.services.ai_provider_service import get_ai_provider
                self._ai_provider = get_ai_provider()
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
        
        return {
            "success": result.get("success", True),
            "response": whatsapp_message,
            "intent": intent.value,
            "entities": entities.to_dict()
        }
    
    # ==========================================================
    # PHASE 4: SERVICE ROUTER
    # ==========================================================
    
    def _route(self, intent: Intent, entities: ExtractedEntities, question: str) -> Dict:
        """Route to appropriate service based on intent"""
        
        # DN Routes
        if intent == Intent.DN_LOOKUP:
            return self._call_logistics("get_dn_details", entities.dn_number)
        
        if intent == Intent.DN_TIMELINE:
            return self._call_logistics("get_dn_timeline", entities.dn_number)
        
        if intent == Intent.DN_PRODUCTS:
            return self._call_logistics("get_dn_products", entities.dn_number)
        
        if intent == Intent.DN_RISK:
            return self._call_logistics("get_dn_risk", entities.dn_number)
        
        # POD Routes
        if intent == Intent.PENDING_POD:
            return self._call_logistics("get_pending_pods", entities.days)
        
        if intent == Intent.PENDING_POD_BY_DAYS:
            return self._call_logistics("get_pending_pods_by_days", entities.days)
        
        # PGI Routes
        if intent == Intent.PENDING_PGI:
            return self._call_logistics("get_pending_pgi", entities.days)
        
        if intent == Intent.PENDING_PGI_BY_DAYS:
            return self._call_logistics("get_pending_pgi_by_days", entities.days)
        
        # Dealer Routes
        if intent == Intent.DEALER_QUERY:
            return self._call_analytics("get_dealer_details", entities.dealer)
        
        if intent == Intent.DEALER_DASHBOARD:
            return self._call_analytics("get_dealer_dashboard", entities.dealer)
        
        if intent == Intent.TOP_DEALERS:
            return self._call_analytics("get_top_dealers", entities.limit)
        
        if intent == Intent.DEALER_RISK:
            return self._call_analytics("get_dealer_risk", entities.dealer)
        
        # Warehouse Routes
        if intent == Intent.WAREHOUSE_QUERY:
            return self._call_analytics("get_warehouse_details", entities.warehouse)
        
        if intent == Intent.TOP_WAREHOUSES:
            return self._call_analytics("get_top_warehouses", entities.limit)
        
        if intent == Intent.WAREHOUSE_DELAY:
            return self._call_analytics("get_warehouse_delays", entities.warehouse)
        
        # Product Routes
        if intent == Intent.TOP_PRODUCTS:
            return self._call_analytics("get_top_products", entities.limit)
        
        # City Routes
        if intent == Intent.CITY_DASHBOARD:
            return self._call_analytics("get_city_dashboard", entities.city)
        
        # KPI Routes
        if intent == Intent.EXECUTIVE_DASHBOARD:
            return self._call_kpi("get_executive_dashboard")
        
        if intent == Intent.NETWORK_HEALTH:
            return self._call_kpi("get_network_health")
        
        if intent == Intent.KPI_DASHBOARD:
            return self._call_kpi("get_kpi_dashboard")
        
        if intent == Intent.TOP_RISKS:
            return self._call_kpi("get_top_risks")
        
        if intent == Intent.TARGET_ACHIEVEMENT:
            return self._call_kpi("get_target_achievement")
        
        # Control Tower Routes
        if intent == Intent.CONTROL_TOWER:
            return self._call_kpi("get_control_tower")
        
        if intent == Intent.ALERTS:
            return self._call_kpi("get_alerts")
        
        if intent == Intent.CRITICAL_DNS:
            return self._call_kpi("get_critical_dns")
        
        # AI Analysis Routes
        if intent == Intent.ROOT_CAUSE:
            return self._call_ai("root_cause", question)
        
        if intent == Intent.RECOMMENDATIONS:
            return self._call_ai("recommendations", question)
        
        if intent == Intent.PREDICTIVE_ANALYSIS:
            return self._call_ai("predictive", question)
        
        if intent == Intent.TREND_ANALYSIS:
            return self._call_ai("trend", question)
        
        # General Routes
        if intent == Intent.HELP:
            return self.formatter.format_success({}, self.formatter.format_help())
        
        if intent == Intent.GREETING:
            return self.formatter.format_success({}, self.formatter.format_greeting())
        
        # Default: General AI
        return self._call_ai("general", question)
    
    # ==========================================================
    # SERVICE CALL WRAPPERS
    # ==========================================================
    
    def _call_logistics(self, method: str, *args, **kwargs) -> Dict:
        """Call logistics service with error handling"""
        if not self.logistics_service:
            return self.formatter.format_error("Logistics service unavailable")
        
        try:
            service_method = getattr(self.logistics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Unknown method: {method}")
            
            result = service_method(*args, **kwargs)
            
            # Handle different return types
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            elif isinstance(result, list):
                if not result:
                    return self.formatter.format_error("No data found")
                return self.formatter.format_success(result)
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"Logistics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_analytics(self, method: str, *args, **kwargs) -> Dict:
        """Call analytics service with error handling"""
        if not self.analytics_service:
            return self.formatter.format_error("Analytics service unavailable")
        
        try:
            service_method = getattr(self.analytics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Unknown method: {method}")
            
            result = service_method(*args, **kwargs)
            
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            elif isinstance(result, list):
                if not result:
                    return self.formatter.format_error("No data found")
                return self.formatter.format_success(result)
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"Analytics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_kpi(self, method: str, *args, **kwargs) -> Dict:
        """Call KPI service with error handling"""
        if not self.kpi_service:
            return self.formatter.format_error("KPI service unavailable")
        
        try:
            service_method = getattr(self.kpi_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Unknown method: {method}")
            
            result = service_method(*args, **kwargs)
            
            if isinstance(result, dict):
                if result.get("error"):
                    return self.formatter.format_error(result["error"])
                return self.formatter.format_success(result)
            else:
                return self.formatter.format_success(result)
                
        except Exception as e:
            logger.error(f"KPI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_ai(self, analysis_type: str, question: str) -> Dict:
        """Call AI provider for analysis"""
        if not self.ai_provider:
            return self.formatter.format_error("AI service unavailable")
        
        try:
            if analysis_type == "root_cause":
                result = self.ai_provider.generate_root_cause_analysis(question, {})
            elif analysis_type == "recommendations":
                result = self.ai_provider.generate_recommendations([], {})
            elif analysis_type == "general":
                result = self.ai_provider.chat(question, "guest")
            else:
                result = self.ai_provider.chat(question, "guest")
            
            return self.formatter.format_success({"insight": result}, result)
            
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict:
        """Health check for monitoring"""
        return {
            "service": "ai_query_service",
            "version": "32.0",
            "mode": "pure_router",
            "status": "healthy",
            "services": {
                "logistics": self._logistics_service is not None,
                "analytics": self._analytics_service is not None,
                "kpi": self._kpi_service is not None,
                "ai": self._ai_provider is not None
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
    """
    try:
        service = AIQueryService(db)
        result = service.process_query(question, phone_number)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("🧠 AI QUERY SERVICE v32.0 - PURE ROUTER MODE")
logger.info("   Responsibilities: Intent → Extract → Route → Format")
logger.info("   NO Business Rules | NO KPI Logic | NO Analytics | NO AI Prompts")
logger.info("=" * 60)
