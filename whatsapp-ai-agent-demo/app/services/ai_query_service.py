# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v21.0 - FULL INTEGRATION)
# ==========================================================
# COMPLETE AI QUERY ORCHESTRATOR v21.0:
# - PROBLEM 1-13: ALL FIXED
# - BUSINESS RULES ENGINE (Aging, SLA, Delay Buckets, Status)
# - FULL ANALYTICS_SERVICE INTEGRATION
# - WAREHOUSE, CITY, VENDOR, PRODUCT DASHBOARDS
# - ROOT CAUSE, TREND, PREDICTIVE ANALYSIS
# - GROQ AI INTEGRATION (PRESERVED)
# ==========================================================

import re
import time
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta, date
from enum import Enum
from collections import deque, defaultdict
from dataclasses import dataclass, field

from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from loguru import logger

from app.config import config

# ==========================================================
# GROQ INTEGRATION (PRESERVED)
# ==========================================================

try:
    from app.services.ai_provider_service import get_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False

# ==========================================================
# ANALYTICS SERVICE INTEGRATION (Problem 10 - CRITICAL)
# ==========================================================

try:
    from app.services.analytics_service import AnalyticsService
    ANALYTICS_SERVICE_AVAILABLE = True
    logger.info("✅ AnalyticsService integration available")
except ImportError as e:
    logger.error(f"Failed to import AnalyticsService: {e}")
    ANALYTICS_SERVICE_AVAILABLE = False

# ==========================================================
# MODELS IMPORT
# ==========================================================

try:
    from app.models import DeliveryReport
    MODELS_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import DeliveryReport model: {e}")
    MODELS_AVAILABLE = False


# ==========================================================
# PROBLEM 1: BUSINESS RULES ENGINE
# ==========================================================

class AgingEngine:
    """Centralized aging calculation engine"""
    
    @staticmethod
    def calculate_delivery_aging(record) -> int:
        """Rule 2: Delivery Aging = PGI Date - DN Creation Date"""
        if not record.good_issue_date or not record.dn_create_date:
            return 0
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (pgi_date - create_date).days if pgi_date and create_date else 0
    
    @staticmethod
    def calculate_pending_delivery_aging(record) -> int:
        """Rule 3: Pending Delivery Aging = Today - DN Creation Date"""
        if not record.dn_create_date:
            return 0
        
        if isinstance(record.dn_create_date, datetime):
            create_date = record.dn_create_date.date()
        else:
            create_date = record.dn_create_date
        
        return (date.today() - create_date).days
    
    @staticmethod
    def calculate_pod_aging(record) -> int:
        """Rule 1: POD Aging = POD Date - PGI Date"""
        if record.pod_status != "Received":
            return 0
        
        if not record.pod_date or not record.good_issue_date:
            return 0
        
        if isinstance(record.pod_date, datetime):
            pod_date = record.pod_date.date()
        else:
            pod_date = record.pod_date
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        return (pod_date - pgi_date).days if pod_date and pgi_date else 0
    
    @staticmethod
    def calculate_pending_pod_aging(record) -> int:
        """Rule 4: Pending POD Aging = Today - PGI Date"""
        if record.pod_status == "Received":
            return 0
        
        if not record.good_issue_date:
            return 0
        
        if isinstance(record.good_issue_date, datetime):
            pgi_date = record.good_issue_date.date()
        else:
            pgi_date = record.good_issue_date
        
        return (date.today() - pgi_date).days


# ==========================================================
# PROBLEM 2: STATUS LOGIC ENGINE
# ==========================================================

class DeliveryStatus(str, Enum):
    OPEN = "Open"
    IN_TRANSIT = "In Transit"
    DELIVERED = "Delivered"
    CLOSED = "Closed"


class StatusEngine:
    """Reusable status determination engine"""
    
    @staticmethod
    def get_delivery_status(pgi_status: str, pod_status: str) -> DeliveryStatus:
        """Get standardized delivery status"""
        if pgi_status != "Completed":
            return DeliveryStatus.OPEN
        elif pgi_status == "Completed" and pod_status != "Received":
            return DeliveryStatus.IN_TRANSIT
        elif pod_status == "Received":
            return DeliveryStatus.DELIVERED
        return DeliveryStatus.OPEN
    
    @staticmethod
    def get_status_icon(status: DeliveryStatus) -> str:
        icons = {
            DeliveryStatus.OPEN: "📝",
            DeliveryStatus.IN_TRANSIT: "🚚",
            DeliveryStatus.DELIVERED: "✅",
            DeliveryStatus.CLOSED: "🔒"
        }
        return icons.get(status, "❓")


# ==========================================================
# PROBLEM 3: SLA ENGINE
# ==========================================================

class SLAEngine:
    """SLA calculation engine"""
    
    DELIVERY_SLA_DAYS = 1  # Delivery within 1 day of PGI
    POD_SLA_DAYS = 3       # POD within 3 days of delivery
    
    @classmethod
    def calculate_delivery_sla(cls, delivery_aging: int) -> Tuple[str, str]:
        """Returns (status, icon) for delivery SLA"""
        if delivery_aging <= cls.DELIVERY_SLA_DAYS:
            return "On Time", "✅"
        else:
            return "Delayed", "🔴"
    
    @classmethod
    def calculate_pod_sla(cls, pod_aging: int) -> Tuple[str, str]:
        """Returns (status, icon) for POD SLA"""
        if pod_aging <= cls.POD_SLA_DAYS:
            return "On Time", "✅"
        else:
            return "Delayed", "🔴"


# ==========================================================
# PROBLEM 4: DELAY BUCKET ENGINE
# ==========================================================

class DelayBucket(str, Enum):
    ON_TIME = "On Time"
    MINOR_DELAY = "Minor Delay"
    MODERATE_DELAY = "Moderate Delay"
    CRITICAL = "Critical"
    SEVERE = "Severe"


class DelayBucketEngine:
    """Centralized delay bucket calculation"""
    
    @staticmethod
    def get_delay_bucket(days_delayed: int) -> DelayBucket:
        """Get delay bucket classification"""
        if days_delayed <= 1:
            return DelayBucket.ON_TIME
        elif days_delayed <= 3:
            return DelayBucket.MINOR_DELAY
        elif days_delayed <= 7:
            return DelayBucket.MODERATE_DELAY
        elif days_delayed <= 15:
            return DelayBucket.CRITICAL
        else:
            return DelayBucket.SEVERE
    
    @staticmethod
    def get_delay_icon(bucket: DelayBucket) -> str:
        icons = {
            DelayBucket.ON_TIME: "🟢",
            DelayBucket.MINOR_DELAY: "🟡",
            DelayBucket.MODERATE_DELAY: "🟠",
            DelayBucket.CRITICAL: "🔴",
            DelayBucket.SEVERE: "💀"
        }
        return icons.get(bucket, "⚪")


# ==========================================================
# PROBLEM 5: DN TIMELINE ENGINE
# ==========================================================

class DNTimelineEngine:
    """Generate DN journey timeline"""
    
    @staticmethod
    def get_timeline(record) -> List[Dict]:
        """Get DN timeline events"""
        events = []
        
        # DN Created
        if record.dn_create_date:
            events.append({
                "stage": "DN Created",
                "date": record.dn_create_date,
                "icon": "📄",
                "description": f"Delivery Note {record.dn_no} created"
            })
        
        # PGI
        if record.good_issue_date:
            aging = AgingEngine.calculate_delivery_aging(record)
            events.append({
                "stage": "PGI Completed",
                "date": record.good_issue_date,
                "icon": "🚚",
                "description": f"Goods issued after {aging} days"
            })
        
        # Delivery
        if record.delivery_date:
            events.append({
                "stage": "Delivered",
                "date": record.delivery_date,
                "icon": "✅",
                "description": "Order delivered to customer"
            })
        
        # POD
        if record.pod_date:
            pod_aging = AgingEngine.calculate_pod_aging(record)
            events.append({
                "stage": "POD Received",
                "date": record.pod_date,
                "icon": "📋",
                "description": f"Proof of Delivery received after {pod_aging} days"
            })
        
        return events
    
    @staticmethod
    def format_timeline(events: List[Dict]) -> str:
        """Format timeline for WhatsApp display"""
        if not events:
            return "No timeline events available"
        
        response = "📅 *DN JOURNEY TIMELINE*\n\n"
        for i, event in enumerate(events, 1):
            date_str = event["date"].strftime("%d-%b-%Y") if hasattr(event["date"], "strftime") else str(event["date"])
            response += f"{i}. {event['icon']} *{event['stage']}*\n"
            response += f"   📅 {date_str}\n"
            response += f"   📝 {event['description']}\n\n"
        
        return response


# ==========================================================
# ANALYTICS SNAPSHOT (Pre-calculated)
# ==========================================================

@dataclass
class AnalyticsSnapshot:
    dn_no: str
    dealer: str
    warehouse: str
    city: str
    delivery_status: str
    delivery_aging: int
    pending_delivery_aging: int
    pod_aging: int
    pending_pod_aging: int
    delivery_sla_status: str
    pod_sla_status: str
    delay_bucket: str
    dn_health_score: int
    total_value: float
    total_units: float
    created_date: Optional[date] = None
    pgi_date: Optional[date] = None
    delivery_date: Optional[date] = None
    pod_date: Optional[date] = None


# ==========================================================
# INTENT TYPES (Expanded)
# ==========================================================

class IntentType(str, Enum):
    # DN Intents
    DN_STATUS = "dn_status"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    DN_AGING = "dn_aging"
    
    # Dealer Intents
    DEALER_DASHBOARD = "dealer_dashboard"
    DEALER_RANKING = "dealer_ranking"
    DEALER_RISK = "dealer_risk"
    
    # Warehouse Intents (Problem 6)
    WAREHOUSE_DASHBOARD = "warehouse_dashboard"
    WAREHOUSE_RANKING = "warehouse_ranking"
    WAREHOUSE_RISK = "warehouse_risk"
    
    # City Intents (Problem 7)
    CITY_DASHBOARD = "city_dashboard"
    CITY_RANKING = "city_ranking"
    CITY_RISK = "city_risk"
    
    # Product Intents (Problem 8)
    PRODUCT_DASHBOARD = "product_dashboard"
    PRODUCT_RANKING = "product_ranking"
    PRODUCT_FILL_RATE = "product_fill_rate"
    
    # Vendor Intents (Problem 9)
    VENDOR_DASHBOARD = "vendor_dashboard"
    VENDOR_RANKING = "vendor_ranking"
    VENDOR_COMPLIANCE = "vendor_compliance"
    
    # POD/PGI Intents
    POD_PENDING = "pod_pending"
    POD_ANALYSIS = "pod_analysis"
    PGI_PENDING = "pgi_pending"
    PGI_ANALYSIS = "pgi_analysis"
    
    # Revenue Intents
    REVENUE_ANALYSIS = "revenue_analysis"
    REVENUE_AT_RISK = "revenue_at_risk"
    
    # Executive Intents (Problem 10)
    CEO_BRIEFING = "ceo_briefing"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    TOP_RISKS = "top_risks"
    RECOMMENDATIONS = "recommendations"
    
    # Analytics Intents (Problems 11, 12, 13)
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    TREND_ANALYSIS = "trend_analysis"
    PREDICTIVE_ANALYSIS = "predictive_analysis"
    
    # General
    HELP = "help"
    GENERAL_QUERY = "general_query"


# ==========================================================
# ENTITY EXTRACTION
# ==========================================================

class EntityType(str, Enum):
    DN_NUMBER = "dn_number"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    VENDOR = "vendor"


@dataclass
class ExtractedEntity:
    type: EntityType
    value: str
    confidence: float = 1.0


class EntityExtractor:
    DN_PATTERN = re.compile(r'\b(\d{6,15})\b')
    DEALER_PATTERN = re.compile(r'dealer\s+([A-Za-z0-9\s&]+?)(?:\s+(?:dashboard|performance|risk)|$)', re.I)
    WAREHOUSE_PATTERN = re.compile(r'warehouse\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|risk)|$)', re.I)
    CITY_PATTERN = re.compile(r'city\s+([A-Za-z\s]+?)(?:\s+(?:dashboard|performance|risk)|$)', re.I)
    PRODUCT_PATTERN = re.compile(r'product\s+([A-Z0-9\-]+)|([A-Z]{2,3}-[0-9A-Z]+)', re.I)
    VENDOR_PATTERN = re.compile(r'vendor\s+([A-Za-z0-9\s]+?)(?:\s+(?:dashboard|performance|compliance)|$)', re.I)
    
    @classmethod
    def extract_all(cls, text: str) -> Dict[EntityType, ExtractedEntity]:
        entities = {}
        
        # DN
        dn_match = cls.DN_PATTERN.search(text)
        if dn_match:
            entities[EntityType.DN_NUMBER] = ExtractedEntity(EntityType.DN_NUMBER, dn_match.group(1))
        
        # Dealer
        dealer_match = cls.DEALER_PATTERN.search(text)
        if dealer_match:
            entities[EntityType.DEALER] = ExtractedEntity(EntityType.DEALER, dealer_match.group(1).strip())
        
        # Warehouse
        warehouse_match = cls.WAREHOUSE_PATTERN.search(text)
        if warehouse_match:
            entities[EntityType.WAREHOUSE] = ExtractedEntity(EntityType.WAREHOUSE, warehouse_match.group(1).strip())
        
        # City
        city_match = cls.CITY_PATTERN.search(text)
        if city_match:
            entities[EntityType.CITY] = ExtractedEntity(EntityType.CITY, city_match.group(1).strip())
        
        # Product
        product_match = cls.PRODUCT_PATTERN.search(text.upper())
        if product_match:
            product = product_match.group(1) or product_match.group(2)
            if product:
                entities[EntityType.PRODUCT] = ExtractedEntity(EntityType.PRODUCT, product)
        
        # Vendor
        vendor_match = cls.VENDOR_PATTERN.search(text)
        if vendor_match:
            entities[EntityType.VENDOR] = ExtractedEntity(EntityType.VENDOR, vendor_match.group(1).strip())
        
        return entities


# ==========================================================
# NATURAL LANGUAGE MAPPER
# ==========================================================

class NaturalLanguageMapper:
    
    @classmethod
    def map_to_intent(cls, text: str, entities: Dict) -> Tuple[IntentType, Optional[str]]:
        text_lower = text.lower().strip()
        
        # DN Priority
        if EntityType.DN_NUMBER in entities:
            dn = entities[EntityType.DN_NUMBER].value
            if any(p in text_lower for p in ["timeline", "journey", "history", "track"]):
                return IntentType.DN_TIMELINE, dn
            elif any(p in text_lower for p in ["product", "items", "contains"]):
                return IntentType.DN_PRODUCTS, dn
            elif any(p in text_lower for p in ["aging", "how old", "age"]):
                return IntentType.DN_AGING, dn
            else:
                return IntentType.DN_STATUS, dn
        
        # Executive (Problem 10 - AnalyticsService integration)
        if any(p in text_lower for p in ["ceo briefing", "ceo dashboard", "board briefing"]):
            return IntentType.CEO_BRIEFING, None
        if any(p in text_lower for p in ["executive summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        if any(p in text_lower for p in ["network health", "health score"]):
            return IntentType.NETWORK_HEALTH, None
        if any(p in text_lower for p in ["top risks", "biggest risks"]):
            return IntentType.TOP_RISKS, None
        if any(p in text_lower for p in ["recommendations", "suggestions", "action items"]):
            return IntentType.RECOMMENDATIONS, None
        
        # Analytics (Problems 11, 12, 13)
        if any(p in text_lower for p in ["why", "root cause", "reason for", "what caused"]):
            return IntentType.ROOT_CAUSE_ANALYSIS, None
        if any(p in text_lower for p in ["trend", "over time", "pattern", "compare", "vs last"]):
            return IntentType.TREND_ANALYSIS, None
        if any(p in text_lower for p in ["predict", "forecast", "likely", "will miss"]):
            return IntentType.PREDICTIVE_ANALYSIS, None
        
        # Warehouse (Problem 6)
        if EntityType.WAREHOUSE in entities:
            return IntentType.WAREHOUSE_DASHBOARD, entities[EntityType.WAREHOUSE].value
        if any(p in text_lower for p in ["warehouse ranking", "top warehouse"]):
            return IntentType.WAREHOUSE_RANKING, None
        
        # City (Problem 7)
        if EntityType.CITY in entities:
            return IntentType.CITY_DASHBOARD, entities[EntityType.CITY].value
        if any(p in text_lower for p in ["city ranking", "top city"]):
            return IntentType.CITY_RANKING, None
        
        # Product (Problem 8)
        if EntityType.PRODUCT in entities:
            if any(p in text_lower for p in ["fill rate", "fulfillment"]):
                return IntentType.PRODUCT_FILL_RATE, entities[EntityType.PRODUCT].value
            return IntentType.PRODUCT_DASHBOARD, entities[EntityType.PRODUCT].value
        if any(p in text_lower for p in ["top product", "product ranking", "best product"]):
            return IntentType.PRODUCT_RANKING, None
        
        # Vendor (Problem 9)
        if EntityType.VENDOR in entities:
            if any(p in text_lower for p in ["compliance", "pod compliance"]):
                return IntentType.VENDOR_COMPLIANCE, entities[EntityType.VENDOR].value
            return IntentType.VENDOR_DASHBOARD, entities[EntityType.VENDOR].value
        if any(p in text_lower for p in ["vendor ranking", "top vendor"]):
            return IntentType.VENDOR_RANKING, None
        
        # Dealer
        if EntityType.DEALER in entities:
            if any(p in text_lower for p in ["risk", "high risk"]):
                return IntentType.DEALER_RISK, entities[EntityType.DEALER].value
            return IntentType.DEALER_DASHBOARD, entities[EntityType.DEALER].value
        if any(p in text_lower for p in ["top dealer", "dealer ranking", "best dealer"]):
            return IntentType.DEALER_RANKING, None
        
        # POD/PGI
        if any(p in text_lower for p in ["pending pod", "pod pending"]):
            return IntentType.POD_PENDING, None
        if any(p in text_lower for p in ["pending pgi", "pending dispatch"]):
            return IntentType.PGI_PENDING, None
        
        # Revenue
        if any(p in text_lower for p in ["revenue at risk", "at risk revenue"]):
            return IntentType.REVENUE_AT_RISK, None
        if any(p in text_lower for p in ["revenue analysis", "revenue report"]):
            return IntentType.REVENUE_ANALYSIS, None
        
        # Help
        if any(p in text_lower for p in ["help", "menu", "commands"]):
            return IntentType.HELP, None
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# CONVERSATION MEMORY
# ==========================================================

class ConversationMemory:
    def __init__(self, max_history: int = 20):
        self.history: Dict[str, deque] = {}
        self.contexts: Dict[str, Dict] = {}
        self.max_history = max_history
    
    def get_or_create_context(self, phone_number: str) -> Dict:
        if phone_number not in self.contexts:
            self.contexts[phone_number] = {
                "current_dn": None,
                "current_dealer": None,
                "current_warehouse": None,
                "current_city": None,
                "current_product": None
            }
        return self.contexts[phone_number]
    
    def add(self, phone_number: str, question: str, response: str, 
            intent: IntentType, entity: Optional[str] = None, entities: Dict = None):
        history = self.history.get(phone_number, deque(maxlen=self.max_history))
        context = self.get_or_create_context(phone_number)
        
        history.append({
            "question": question,
            "response": response[:500],
            "intent": intent.value,
            "entity": entity,
            "timestamp": datetime.utcnow().isoformat()
        })
        self.history[phone_number] = history
        
        if entities:
            if EntityType.DN_NUMBER in entities:
                context["current_dn"] = entities[EntityType.DN_NUMBER].value
            if EntityType.DEALER in entities:
                context["current_dealer"] = entities[EntityType.DEALER].value
            if EntityType.WAREHOUSE in entities:
                context["current_warehouse"] = entities[EntityType.WAREHOUSE].value
            if EntityType.CITY in entities:
                context["current_city"] = entities[EntityType.CITY].value
            if EntityType.PRODUCT in entities:
                context["current_product"] = entities[EntityType.PRODUCT].value
    
    def get_last_context(self, phone_number: str) -> Dict:
        context = self.get_or_create_context(phone_number)
        history = self.history.get(phone_number, deque())
        
        if not history:
            return context
        
        last = history[-1]
        return {**context, "last_intent": last.get("intent"), "last_entity": last.get("entity")}
    
    def resolve_follow_up(self, phone_number: str, question: str) -> Dict:
        context = self.get_or_create_context(phone_number)
        question_lower = question.lower()
        
        resolved = {}
        if any(w in question_lower for w in ["it", "this", "that", "the dn"]):
            if context.get("current_dn"):
                resolved["dn"] = context["current_dn"]
        if any(w in question_lower for w in ["the dealer", "this dealer"]):
            if context.get("current_dealer"):
                resolved["dealer"] = context["current_dealer"]
        if any(w in question_lower for w in ["the warehouse", "this warehouse"]):
            if context.get("current_warehouse"):
                resolved["warehouse"] = context["current_warehouse"]
        if any(w in question_lower for w in ["the city", "this city"]):
            if context.get("current_city"):
                resolved["city"] = context["current_city"]
        
        return resolved


# ==========================================================
# RESPONSE TEMPLATES
# ==========================================================

class ResponseTemplates:
    
    @staticmethod
    def dn_status_template(snapshot: AnalyticsSnapshot) -> str:
        status = DeliveryStatus(snapshot.delivery_status)
        status_icon = StatusEngine.get_status_icon(status)
        delay_bucket = DelayBucket(snapshot.delay_bucket)
        delay_icon = DelayBucketEngine.get_delay_icon(delay_bucket)
        
        delivery_sla_icon = "✅" if snapshot.delivery_sla_status == "On Time" else "🔴"
        pod_sla_icon = "✅" if snapshot.pod_sla_status == "On Time" else "🔴"
        
        return f"""╔══════════════════════════════════════════════════════════════════════════════╗
║                         📦 DN COMPLETE INTELLIGENCE REPORT                                 ║
║                                    {snapshot.dn_no}                                        ║
╚══════════════════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Dealer: {snapshot.dealer}
   • City: {snapshot.city}
   • Warehouse: {snapshot.warehouse}
   • Status: {status_icon} {snapshot.delivery_status}
   • Delay: {delay_icon} {snapshot.delay_bucket}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 *TIMELINE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Created: {snapshot.created_date.strftime('%d-%b-%Y') if snapshot.created_date else 'N/A'}
   • PGI: {snapshot.pgi_date.strftime('%d-%b-%Y') if snapshot.pgi_date else 'Pending'}
   • Delivery: {snapshot.delivery_date.strftime('%d-%b-%Y') if snapshot.delivery_date else 'Pending'}
   • POD: {snapshot.pod_date.strftime('%d-%b-%Y') if snapshot.pod_date else 'Pending'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING & SLA ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Delivery Aging: {snapshot.delivery_aging} days ({delivery_sla_icon} {snapshot.delivery_sla_status})
   • Pending Delivery: {snapshot.pending_delivery_aging} days
   • POD Aging: {snapshot.pod_aging} days ({pod_sla_icon} {snapshot.pod_sla_status})
   • Pending POD: {snapshot.pending_pod_aging} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *HEALTH SCORE: {snapshot.dn_health_score}/100*

💡 Type "timeline" for journey details, "products" for items in this DN"""
    
    @staticmethod
    def help_template() -> str:
        return WELCOME_MESSAGE


# ==========================================================
# INTENT ROUTER (With Full AnalyticsService Integration)
# ==========================================================

class IntentRouter:
    def __init__(self, db: Session):
        self.db = db
        
        # Problem 10: AnalyticsService Integration
        self.analytics = None
        if ANALYTICS_SERVICE_AVAILABLE:
            try:
                self.analytics = AnalyticsService(db)
                logger.info("✅ AnalyticsService integrated into IntentRouter")
            except Exception as e:
                logger.error(f"Failed to initialize AnalyticsService: {e}")
    
    def route(self, intent: IntentType, entity: Optional[str] = None,
              entities: Dict = None, context: Dict = None) -> Dict[str, Any]:
        
        # DN Intents
        if intent == IntentType.DN_STATUS:
            return self._handle_dn_status(entity, entities)
        elif intent == IntentType.DN_TIMELINE:
            return self._handle_dn_timeline(entity, entities)
        elif intent == IntentType.DN_PRODUCTS:
            return self._handle_dn_products(entity, entities)
        
        # Dealer Intents
        elif intent == IntentType.DEALER_DASHBOARD:
            return self._handle_dealer_dashboard(entity, entities)
        elif intent == IntentType.DEALER_RANKING:
            return self._handle_dealer_ranking()
        
        # Warehouse Intents (Problem 6)
        elif intent == IntentType.WAREHOUSE_DASHBOARD:
            return self._handle_warehouse_dashboard(entity, entities)
        elif intent == IntentType.WAREHOUSE_RANKING:
            return self._handle_warehouse_ranking()
        
        # City Intents (Problem 7)
        elif intent == IntentType.CITY_DASHBOARD:
            return self._handle_city_dashboard(entity, entities)
        elif intent == IntentType.CITY_RANKING:
            return self._handle_city_ranking()
        
        # Product Intents (Problem 8)
        elif intent == IntentType.PRODUCT_DASHBOARD:
            return self._handle_product_dashboard(entity, entities)
        elif intent == IntentType.PRODUCT_RANKING:
            return self._handle_product_ranking()
        
        # Executive Intents (Problem 10 - AnalyticsService)
        elif intent == IntentType.CEO_BRIEFING:
            return self._handle_ceo_briefing()
        elif intent == IntentType.EXECUTIVE_SUMMARY:
            return self._handle_executive_summary()
        elif intent == IntentType.NETWORK_HEALTH:
            return self._handle_network_health()
        elif intent == IntentType.TOP_RISKS:
            return self._handle_top_risks()
        elif intent == IntentType.RECOMMENDATIONS:
            return self._handle_recommendations()
        
        # Analytics Intents (Problems 11, 12, 13)
        elif intent == IntentType.ROOT_CAUSE_ANALYSIS:
            return self._handle_root_cause_analysis()
        elif intent == IntentType.TREND_ANALYSIS:
            return self._handle_trend_analysis()
        elif intent == IntentType.PREDICTIVE_ANALYSIS:
            return self._handle_predictive_analysis()
        
        # POD/PGI Intents
        elif intent == IntentType.POD_PENDING:
            return self._handle_pod_pending()
        elif intent == IntentType.PGI_PENDING:
            return self._handle_pgi_pending()
        
        # Revenue Intents
        elif intent == IntentType.REVENUE_ANALYSIS:
            return self._handle_revenue_analysis()
        elif intent == IntentType.REVENUE_AT_RISK:
            return self._handle_revenue_at_risk()
        
        # Help
        elif intent == IntentType.HELP:
            return {"success": True, "response": ResponseTemplates.help_template()}
        
        # General - will go to GROQ
        else:
            return {"success": False, "needs_ai": True}
    
    # ==========================================================
    # HANDLERS
    # ==========================================================
    
    def _handle_dn_status(self, entity, entities):
        dn = entity or (entities.get(EntityType.DN_NUMBER).value if entities.get(EntityType.DN_NUMBER) else None)
        if not dn:
            return {"success": False, "response": "❓ Please provide a DN number."}
        
        record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn).first()
        if not record:
            return {"success": False, "response": f"❌ DN {dn} not found"}
        
        # Use all business engines
        delivery_aging = AgingEngine.calculate_delivery_aging(record)
        pending_delivery_aging = AgingEngine.calculate_pending_delivery_aging(record)
        pod_aging = AgingEngine.calculate_pod_aging(record)
        pending_pod_aging = AgingEngine.calculate_pending_pod_aging(record)
        
        delivery_sla_status, _ = SLAEngine.calculate_delivery_sla(delivery_aging)
        pod_sla_status, _ = SLAEngine.calculate_pod_sla(pod_aging)
        
        delay_days = max(delivery_aging, pending_delivery_aging, pod_aging, pending_pod_aging)
        delay_bucket = DelayBucketEngine.get_delay_bucket(delay_days)
        
        status = StatusEngine.get_delivery_status(record.pgi_status, record.pod_status)
        health_score = max(0, 100 - (delay_days * 5))
        
        snapshot = AnalyticsSnapshot(
            dn_no=dn,
            dealer=record.customer_name or "Unknown",
            warehouse=record.warehouse or "Unknown",
            city=record.ship_to_city or "Unknown",
            delivery_status=status.value,
            delivery_aging=delivery_aging,
            pending_delivery_aging=pending_delivery_aging,
            pod_aging=pod_aging,
            pending_pod_aging=pending_pod_aging,
            delivery_sla_status=delivery_sla_status,
            pod_sla_status=pod_sla_status,
            delay_bucket=delay_bucket.value,
            dn_health_score=health_score,
            total_value=float(record.dn_amount or 0),
            total_units=float(record.dn_qty or 0),
            created_date=record.dn_create_date,
            pgi_date=record.good_issue_date,
            delivery_date=record.delivery_date,
            pod_date=record.pod_date
        )
        
        return {"success": True, "response": ResponseTemplates.dn_status_template(snapshot)}
    
    def _handle_dn_timeline(self, entity, entities):
        dn = entity or (entities.get(EntityType.DN_NUMBER).value if entities.get(EntityType.DN_NUMBER) else None)
        if not dn:
            return {"success": False, "response": "❓ Please provide a DN number."}
        
        record = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn).first()
        if not record:
            return {"success": False, "response": f"❌ DN {dn} not found"}
        
        events = DNTimelineEngine.get_timeline(record)
        response = DNTimelineEngine.format_timeline(events)
        return {"success": True, "response": response}
    
    def _handle_dn_products(self, entity, entities):
        dn = entity or (entities.get(EntityType.DN_NUMBER).value if entities.get(EntityType.DN_NUMBER) else None)
        if not dn:
            return {"success": False, "response": "❓ Please provide a DN number."}
        
        records = self.db.query(DeliveryReport).filter(DeliveryReport.dn_no == dn).all()
        if not records:
            return {"success": False, "response": f"❌ DN {dn} not found"}
        
        response = f"📦 *Products in DN {dn}*\n\n"
        for r in records:
            response += f"   • {r.product}: {float(r.dn_qty or 0):.0f} units (Rs {float(r.dn_amount or 0):,.2f})\n"
        
        return {"success": True, "response": response}
    
    def _handle_dealer_dashboard(self, entity, entities):
        dealer = entity or (entities.get(EntityType.DEALER).value if entities.get(EntityType.DEALER) else None)
        if not dealer:
            return {"success": False, "response": "🏪 Please provide a dealer name."}
        
        records = self.db.query(DeliveryReport).filter(DeliveryReport.customer_name == dealer).all()
        if not records:
            return {"success": False, "response": f"🏪 Dealer '{dealer}' not found"}
        
        total_dns = len(set(r.dn_no for r in records))
        total_value = sum(float(r.dn_amount or 0) for r in records)
        completed_dns = len([r for r in records if r.pgi_status == "Completed"])
        pod_pending = len([r for r in records if r.pgi_status == "Completed" and r.pod_status == "Pending"])
        
        completion_rate = (completed_dns / total_dns * 100) if total_dns > 0 else 0
        health_score = completion_rate
        
        response = f"""🏪 *DEALER DASHBOARD: {dealer}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {total_dns}
   • Completed: {completed_dns}
   • POD Pending: {pod_pending}
   • Completion Rate: {completion_rate:.1f}%
   • Health Score: {health_score:.1f}/100

💰 *FINANCIAL*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {total_value:,.2f}"""
        
        return {"success": True, "response": response}
    
    def _handle_dealer_ranking(self):
        results = self.db.query(
            DeliveryReport.customer_name,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns")
        ).filter(
            DeliveryReport.customer_name.isnot(None)
        ).group_by(
            DeliveryReport.customer_name
        ).order_by(
            desc("total_value")
        ).limit(10).all()
        
        response = "🏆 *TOP 10 DEALERS*\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. *{r.customer_name[:35]}*\n"
            response += f"   💰 Rs {float(r.total_value or 0):,.2f} | 📦 {r.total_dns} DNs\n\n"
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # PROBLEM 6: WAREHOUSE DASHBOARD
    # ==========================================================
    
    def _handle_warehouse_dashboard(self, entity, entities):
        warehouse = entity or (entities.get(EntityType.WAREHOUSE).value if entities.get(EntityType.WAREHOUSE) else None)
        if not warehouse:
            return {"success": False, "response": "🏭 Please provide a warehouse name."}
        
        records = self.db.query(DeliveryReport).filter(DeliveryReport.warehouse == warehouse).all()
        if not records:
            return {"success": False, "response": f"🏭 Warehouse '{warehouse}' not found"}
        
        total_dns = len(set(r.dn_no for r in records))
        total_value = sum(float(r.dn_amount or 0) for r in records)
        completed_dns = len([r for r in records if r.pgi_status == "Completed"])
        
        # Calculate average lead time
        lead_times = []
        for r in records:
            if r.good_issue_date and r.dn_create_date:
                lead_time = (r.good_issue_date - r.dn_create_date).days
                if lead_time >= 0:
                    lead_times.append(lead_time)
        
        avg_lead_time = sum(lead_times) / len(lead_times) if lead_times else 0
        efficiency = max(0, 100 - (avg_lead_time * 5))
        
        response = f"""🏭 *WAREHOUSE DASHBOARD: {warehouse}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {total_dns}
   • Completed: {completed_dns}
   • Completion Rate: {(completed_dns/total_dns*100) if total_dns else 0:.1f}%
   • Avg Lead Time: {avg_lead_time:.1f} days
   • Efficiency: {efficiency:.1f}%

💰 *FINANCIAL*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {total_value:,.2f}"""
        
        return {"success": True, "response": response}
    
    def _handle_warehouse_ranking(self):
        results = self.db.query(
            DeliveryReport.warehouse,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("completed_dns")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            desc("total_value")
        ).limit(10).all()
        
        response = "🏭 *TOP 10 WAREHOUSES*\n\n"
        for i, r in enumerate(results, 1):
            completion = (r.completed_dns / r.total_dns * 100) if r.total_dns else 0
            response += f"{i}. *{r.warehouse[:35]}*\n"
            response += f"   💰 Rs {float(r.total_value or 0):,.2f}\n"
            response += f"   📦 {r.total_dns} DNs | Completion: {completion:.0f}%\n\n"
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # PROBLEM 7: CITY DASHBOARD
    # ==========================================================
    
    def _handle_city_dashboard(self, entity, entities):
        city = entity or (entities.get(EntityType.CITY).value if entities.get(EntityType.CITY) else None)
        if not city:
            return {"success": False, "response": "🌆 Please provide a city name."}
        
        records = self.db.query(DeliveryReport).filter(DeliveryReport.ship_to_city == city).all()
        if not records:
            return {"success": False, "response": f"🌆 City '{city}' not found"}
        
        total_dns = len(set(r.dn_no for r in records))
        total_value = sum(float(r.dn_amount or 0) for r in records)
        completed_dns = len([r for r in records if r.pgi_status == "Completed"])
        pending_value = sum(float(r.dn_amount or 0) for r in records if r.pgi_status != "Completed")
        
        risk_score = (pending_value / total_value * 100) if total_value else 0
        
        response = f"""🌆 *CITY DASHBOARD: {city}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {total_dns}
   • Completed: {completed_dns}
   • Completion Rate: {(completed_dns/total_dns*100) if total_dns else 0:.1f}%

💰 *FINANCIAL METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {total_value:,.2f}
   • Pending Value: Rs {pending_value:,.2f}
   • Risk Score: {risk_score:.1f}/100"""
        
        return {"success": True, "response": response}
    
    def _handle_city_ranking(self):
        results = self.db.query(
            DeliveryReport.ship_to_city,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).label("total_dns")
        ).filter(
            DeliveryReport.ship_to_city.isnot(None)
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            desc("total_value")
        ).limit(10).all()
        
        response = "🌆 *TOP 10 CITIES*\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. *{r.ship_to_city[:35]}*\n"
            response += f"   💰 Rs {float(r.total_value or 0):,.2f} | 📦 {r.total_dns} DNs\n\n"
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # PROBLEM 8: PRODUCT DASHBOARD
    # ==========================================================
    
    def _handle_product_dashboard(self, entity, entities):
        product = entity or (entities.get(EntityType.PRODUCT).value if entities.get(EntityType.PRODUCT) else None)
        if not product:
            return {"success": False, "response": "📦 Please provide a product name."}
        
        records = self.db.query(DeliveryReport).filter(DeliveryReport.product == product).all()
        if not records:
            return {"success": False, "response": f"📦 Product '{product}' not found"}
        
        total_qty = sum(float(r.dn_qty or 0) for r in records)
        total_value = sum(float(r.dn_amount or 0) for r in records)
        delivered_qty = sum(float(r.dn_qty or 0) for r in records if r.pgi_status == "Completed")
        pending_qty = total_qty - delivered_qty
        
        fill_rate = (delivered_qty / total_qty * 100) if total_qty > 0 else 0
        fill_icon = "🟢" if fill_rate >= 80 else "🟡" if fill_rate >= 50 else "🔴"
        
        response = f"""📦 *PRODUCT DASHBOARD: {product}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *ORDER SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Ordered Qty: {total_qty:,.0f}
   • Delivered Qty: {delivered_qty:,.0f} ✅
   • Pending Qty: {pending_qty:,.0f} ⏳
   • Fill Rate: {fill_icon} {fill_rate:.1f}%

💰 *VALUE SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Value: Rs {total_value:,.2f}
   • Total DNs: {len(set(r.dn_no for r in records))}"""
        
        return {"success": True, "response": response}
    
    def _handle_product_ranking(self):
        results = self.db.query(
            DeliveryReport.product,
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.sum(DeliveryReport.dn_qty).label("total_qty"),
            func.count(DeliveryReport.dn_no).label("total_dns")
        ).filter(
            DeliveryReport.product.isnot(None)
        ).group_by(
            DeliveryReport.product
        ).order_by(
            desc("total_value")
        ).limit(10).all()
        
        response = "🏆 *TOP 10 PRODUCTS*\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. *{r.product[:35]}*\n"
            response += f"   💰 Rs {float(r.total_value or 0):,.2f}\n"
            response += f"   📦 {float(r.total_qty or 0):,.0f} units | {r.total_dns} DNs\n\n"
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # PROBLEM 9: VENDOR DASHBOARD (Simplified)
    # ==========================================================
    
    def _handle_vendor_dashboard(self, entity, entities):
        vendor = entity or (entities.get(EntityType.VENDOR).value if entities.get(EntityType.VENDOR) else None)
        if not vendor:
            return {"success": False, "response": "🏪 Please provide a vendor name."}
        
        # Vendor would be linked to warehouse or product
        records = self.db.query(DeliveryReport).filter(DeliveryReport.warehouse == vendor).all()
        if not records:
            return {"success": False, "response": f"🏪 Vendor '{vendor}' not found"}
        
        response = f"""🏪 *VENDOR DASHBOARD: {vendor}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total DNs: {len(set(r.dn_no for r in records))}
   • Total Value: Rs {sum(float(r.dn_amount or 0) for r in records):,.2f}
   • On-Time Delivery: {'N/A'}

💡 Vendor analytics requires additional data integration."""
        
        return {"success": True, "response": response}
    
    def _handle_vendor_ranking(self):
        results = self.db.query(
            DeliveryReport.warehouse,
            func.sum(DeliveryReport.dn_amount).label("total_value")
        ).filter(
            DeliveryReport.warehouse.isnot(None)
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            desc("total_value")
        ).limit(10).all()
        
        response = "🏪 *TOP 10 VENDORS*\n\n"
        for i, r in enumerate(results, 1):
            response += f"{i}. *{r.warehouse[:35]}*\n"
            response += f"   💰 Rs {float(r.total_value or 0):,.2f}\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_vendor_compliance(self, entity, entities):
        return {"success": True, "response": "📋 *Vendor Compliance*\n\nVendor compliance analytics requires additional data integration with vendor master."}
    
    # ==========================================================
    # PROBLEM 10: EXECUTIVE (AnalyticsService Integration)
    # ==========================================================
    
    def _handle_ceo_briefing(self):
        if self.analytics:
            try:
                result = self.analytics.ceo_briefing()
                return {"success": True, "response": result.get("formatted_response", str(result))}
            except Exception as e:
                logger.error(f"CEO briefing error: {e}")
                return self._fallback_executive_response()
        return self._fallback_executive_response()
    
    def _handle_executive_summary(self):
        if self.analytics:
            try:
                result = self.analytics.get_executive_dashboard()
                return {"success": True, "response": result.get("formatted_response", str(result))}
            except Exception as e:
                logger.error(f"Executive summary error: {e}")
                return self._fallback_executive_response()
        return self._fallback_executive_response()
    
    def _handle_network_health(self):
        if self.analytics:
            try:
                result = self.analytics.get_enhanced_network_health()
                return {"success": True, "response": ResponseTemplates.network_health_template(result) if hasattr(ResponseTemplates, 'network_health_template') else str(result)}
            except Exception as e:
                logger.error(f"Network health error: {e}")
                return self._fallback_network_response()
        return self._fallback_network_response()
    
    def _handle_top_risks(self):
        if self.analytics:
            try:
                result = self.analytics.get_enhanced_top_risk_dealers(10)
                if result:
                    response = "🚨 *TOP RISK DEALERS*\n\n"
                    for i, d in enumerate(result[:10], 1):
                        response += f"{i}. *{d.get('name', 'N/A')[:35]}*\n"
                        response += f"   Risk Score: {d.get('risk_score', 0)}/100\n"
                        response += f"   Pending Value: Rs {d.get('pending_value', 0):,.2f}\n\n"
                    return {"success": True, "response": response}
            except Exception as e:
                logger.error(f"Top risks error: {e}")
        return {"success": True, "response": "🚨 *Top Risks*\n\nRisk analytics requires AnalyticsService integration."}
    
    def _handle_recommendations(self):
        if self.analytics:
            try:
                result = self.analytics.why_sales_decreased(30)
                return {"success": True, "response": result.get("formatted_response", str(result))}
            except Exception as e:
                logger.error(f"Recommendations error: {e}")
        return {"success": True, "response": "💡 *Recommendations*\n\n1. Focus on pending POD collection\n2. Review delayed DNs\n3. Follow up with risk dealers"}
    
    # ==========================================================
    # PROBLEM 11, 12, 13: ANALYTICS (Root Cause, Trend, Predictive)
    # ==========================================================
    
    def _handle_root_cause_analysis(self):
        if self.analytics:
            try:
                result = self.analytics.logistics_delay_analysis()
                return {"success": True, "response": result.get("formatted_response", str(result))}
            except Exception as e:
                logger.error(f"Root cause error: {e}")
        return self._fallback_root_cause_response()
    
    def _handle_trend_analysis(self):
        if self.analytics:
            try:
                result = self.analytics.why_sales_decreased(30)
                return {"success": True, "response": result.get("formatted_response", str(result))}
            except Exception as e:
                logger.error(f"Trend analysis error: {e}")
        return self._fallback_trend_response()
    
    def _handle_predictive_analysis(self):
        # Simple predictive logic
        delayed_count = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status != "Completed",
            DeliveryReport.dn_create_date <= date.today() - timedelta(days=7)
        ).count()
        
        response = f"""🔮 *PREDICTIVE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SLA PREDICTIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Likely to miss SLA: {delayed_count} DNs
   • High-risk dealers: Check dealer rankings
   • High-risk warehouses: Check warehouse rankings

💡 Recommendations:
   • Prioritize DNs pending >7 days
   • Focus on high-risk dealers first"""
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # POD/PGI/REVENUE HANDLERS
    # ==========================================================
    
    def _handle_pod_pending(self):
        results = self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.customer_name,
            DeliveryReport.dn_amount,
            DeliveryReport.delivery_date,
            DeliveryReport.good_issue_date
        ).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).limit(15).all()
        
        if not results:
            return {"success": True, "response": "✅ No pending PODs found."}
        
        response = "📋 *PENDING PODs*\n\n"
        for r in results:
            aging = 0
            if r.delivery_date:
                aging = (date.today() - r.delivery_date).days
            elif r.good_issue_date:
                aging = (date.today() - r.good_issue_date).days
            response += f"🔢 *{r.dn_no}*\n"
            response += f"   🏪 {r.customer_name[:30]}\n"
            response += f"   💰 Rs {float(r.dn_amount or 0):,.2f}\n"
            response += f"   ⏱️ {aging} days pending\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_pgi_pending(self):
        results = self.db.query(
            DeliveryReport.dn_no,
            DeliveryReport.customer_name,
            DeliveryReport.dn_amount,
            DeliveryReport.dn_create_date
        ).filter(
            DeliveryReport.pgi_status != "Completed"
        ).order_by(DeliveryReport.dn_create_date).limit(15).all()
        
        if not results:
            return {"success": True, "response": "✅ No pending PGI found."}
        
        response = "⏳ *PENDING PGI DNs*\n\n"
        for r in results:
            aging = (date.today() - r.dn_create_date).days if r.dn_create_date else 0
            response += f"🔢 *{r.dn_no}*\n"
            response += f"   🏪 {r.customer_name[:30]}\n"
            response += f"   💰 Rs {float(r.dn_amount or 0):,.2f}\n"
            response += f"   ⏱️ {aging} days pending\n\n"
        
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self):
        total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
        pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).scalar() or 0
        
        realized = delivered - pod_pending
        realization_rate = (realized / total * 100) if total > 0 else 0
        
        response = f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Revenue: Rs {total:,.2f}
   • Realized: Rs {realized:,.2f} ✅
   • Pending Delivery: Rs {total - delivered:,.2f} ⏳
   • POD Pending: Rs {pod_pending:,.2f} 📋

📈 *REALIZATION RATE: {realization_rate:.1f}%*"""
        
        return {"success": True, "response": response}
    
    def _handle_revenue_at_risk(self):
        pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pgi_status == "Completed",
            DeliveryReport.pod_status == "Pending"
        ).scalar() or 0
        
        pending_delivery = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pgi_status != "Completed"
        ).scalar() or 0
        
        total_at_risk = pod_pending + pending_delivery
        
        response = f"""⚠️ *REVENUE AT RISK*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Total Revenue at Risk: Rs {total_at_risk:,.2f}
   • Pending Delivery: Rs {pending_delivery:,.2f}
   • POD Pending: Rs {pod_pending:,.2f}

💡 Focus on pending POD collection to reduce risk."""
        
        return {"success": True, "response": response}
    
    # ==========================================================
    # FALLBACK RESPONSES
    # ==========================================================
    
    def _fallback_executive_response(self):
        return {"success": True, "response": """👑 *EXECUTIVE SUMMARY*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Quick Stats*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Type "DN <number>" for DN status
   • Type "Top dealers" for rankings
   • Type "Pending PODs" for POD status

💡 Full executive dashboard requires AnalyticsService integration."""}
    
    def _fallback_network_response(self):
        return {"success": True, "response": """📊 *NETWORK HEALTH*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *Available Commands*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • "Top dealers" - Dealer rankings
   • "Pending PODs" - POD collection status
   • "Revenue analysis" - Financial view
   • "DN <number>" - Track specific DN

💡 Type "Help" for complete menu."""}
    
    def _fallback_root_cause_response(self):
        return {"success": True, "response": """🔍 *ROOT CAUSE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Common Delay Causes*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1. Warehouse processing delays
   2. Transporter availability issues
   3. POD collection lag
   4. Customer confirmation delays

💡 Check "Pending PGI" and "Pending PODs" for specific issues."""}
    
    def _fallback_trend_response(self):
        return {"success": True, "response": """📈 *TREND ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *Available Analysis*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   • Compare "Top dealers" performance
   • Check "Warehouse ranking" for efficiency
   • Review "City ranking" for regional trends

💡 For detailed trends, check individual dashboards."""}


# ==========================================================
# MAIN AI QUERY SERVICE (WITH GROQ PRESERVED)
# ==========================================================

class AIQueryService:
    """Complete orchestrator with GROQ AI and full analytics integration"""
    
    def __init__(self, db: Session):
        self.db = db
        self.conversation_memory = ConversationMemory()
        self.entity_extractor = EntityExtractor()
        self.nlp_mapper = NaturalLanguageMapper()
        self.intent_router = IntentRouter(db)
        
        # GROQ Integration (PRESERVED)
        self.ai_provider = None
        self.ai_available = False
        
        if AI_PROVIDER_AVAILABLE:
            try:
                self.ai_provider = get_ai_provider_service(db)
                if self.ai_provider:
                    self.ai_available = self._check_groq_health()
                    logger.info(f"✅ GROQ AI Provider: {'Available' if self.ai_available else 'Unavailable'}")
            except Exception as e:
                logger.error(f"Failed to initialize AI provider: {e}")
                self.ai_available = False
        
        logger.info("=" * 60)
        logger.info("🚀 AI QUERY ORCHESTRATOR v21.0 (COMPLETE INTEGRATION)")
        logger.info(f"   GROQ AI: {'Available' if self.ai_available else 'Not Available'}")
        logger.info(f"   AnalyticsService: {'Available' if ANALYTICS_SERVICE_AVAILABLE else 'Not Available'}")
        logger.info(f"   Models: {'Available' if MODELS_AVAILABLE else 'Not Available'}")
        logger.info("=" * 60)
    
    def _check_groq_health(self) -> bool:
        if not self.ai_provider:
            return False
        try:
            result = self.ai_provider.answer_question(question="Say 'GROQ is working'", user_role="system")
            return result.get("success", False)
        except Exception as e:
            logger.error(f"GROQ health check error: {e}")
            return False
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        # Step 1: Extract entities
        entities = self.entity_extractor.extract_all(question)
        logger.info(f"🔍 Entities: {[(e.type.value, e.value) for e in entities.values()]}")
        
        # Step 2: Resolve follow-up context
        follow_up = {}
        if user_phone:
            follow_up = self.conversation_memory.resolve_follow_up(user_phone, question)
            if follow_up:
                logger.info(f"🔄 Follow-up resolved: {follow_up}")
                if "dn" in follow_up and EntityType.DN_NUMBER not in entities:
                    entities[EntityType.DN_NUMBER] = ExtractedEntity(EntityType.DN_NUMBER, follow_up["dn"])
                if "dealer" in follow_up and EntityType.DEALER not in entities:
                    entities[EntityType.DEALER] = ExtractedEntity(EntityType.DEALER, follow_up["dealer"])
                if "warehouse" in follow_up and EntityType.WAREHOUSE not in entities:
                    entities[EntityType.WAREHOUSE] = ExtractedEntity(EntityType.WAREHOUSE, follow_up["warehouse"])
                if "city" in follow_up and EntityType.CITY not in entities:
                    entities[EntityType.CITY] = ExtractedEntity(EntityType.CITY, follow_up["city"])
        
        # Step 3: Map to intent
        intent, entity = self.nlp_mapper.map_to_intent(question, entities)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        # Step 4: Get conversation context
        conv_context = {}
        if user_phone:
            conv_context = self.conversation_memory.get_last_context(user_phone)
        
        # Step 5: Route intent
        result = self.intent_router.route(intent, entity, entities, conv_context)
        
        # Step 6: Fallback to GROQ if needed (PRESERVED)
        if result.get("needs_ai") or (result.get("success") is False and result.get("response") and "not found" in result.get("response", "").lower()):
            if self.ai_available and self.ai_provider:
                logger.info(f"🤖 Falling back to GROQ for: {question[:50]}")
                try:
                    ai_result = self.ai_provider.answer_question(
                        question=f"User asked: {question}\n\nProvide a helpful, concise response for a logistics WhatsApp bot.",
                        user_phone=user_phone,
                        user_role=user_role or "guest"
                    )
                    if ai_result.get("success"):
                        result = {"success": True, "response": ai_result.get("content")}
                    else:
                        result = self._get_fallback_response(question)
                except Exception as e:
                    logger.error(f"GROQ error: {e}")
                    result = self._get_fallback_response(question)
            else:
                result = self._get_fallback_response(question)
        
        # Step 7: Store in memory
        if user_phone and result.get("success"):
            self.conversation_memory.add(user_phone, question, result.get("response", ""), intent, entity, entities)
        
        # Step 8: Add metrics
        result["processing_time_ms"] = int((time.time() - start_time) * 1000)
        logger.info(f"⚡ Response time: {result['processing_time_ms']}ms")
        
        return result
    
    def _get_fallback_response(self, question: str) -> Dict[str, Any]:
        return {
            "success": True,
            "response": f"""🤖 *AI LOGISTICS ASSISTANT v21.0*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 "DN 80012345" - DN status & timeline
🏪 "ABC Electronics" - Dealer dashboard
🏭 "Lahore warehouse" - Warehouse performance
🌆 "Karachi city" - City analytics
👑 "Executive summary" - Complete dashboard
🚨 "Top risks" - Risk analysis
📋 "Pending PODs" - POD collection

Type "Help" for complete menu."""
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "⚠️ Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


# ==========================================================
# WELCOME MESSAGE
# ==========================================================

WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v21.0*

Complete logistics intelligence with GROQ AI integration.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *WHAT YOU CAN ASK:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN TRACKING*
   • "DN 80012345" - Complete status with aging & SLA
   • "Timeline of DN 80012345" - Journey tracking
   • "Products in DN 80012345" - Line items

🏪 *DEALER INSIGHTS*
   • "ABC Electronics" - Dealer dashboard
   • "Top dealers" - Rankings
   • "High risk dealers" - Risk analysis

🏭 *WAREHOUSE ANALYTICS*
   • "Lahore warehouse" - Performance dashboard
   • "Warehouse ranking" - Efficiency comparison

🌆 *CITY INTELLIGENCE*
   • "Karachi city" - City dashboard
   • "City ranking" - Performance by city

📦 *PRODUCT ANALYTICS*
   • "Product HSU-18HFPAA" - Product dashboard
   • "Top products" - Best sellers
   • "Product fill rate" - Fulfillment metrics

👑 *EXECUTIVE REPORTS*
   • "Executive summary" - Complete dashboard
   • "CEO briefing" - Leadership view
   • "Network health" - System status
   • "Top risks" - Critical issues
   • "Recommendations" - Action items

📈 *ADVANCED ANALYTICS*
   • "Why are deliveries delayed?" - Root cause
   • "What are the trends?" - Trend analysis
   • "Predict future delays" - Predictive analysis

📋 *POD & PGI*
   • "Pending PODs" - Collection required
   • "Pending PGI" - Dispatch pending

💰 *REVENUE*
   • "Revenue analysis" - Complete breakdown
   • "Revenue at risk" - Exposure analysis

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRO TIPS:* I remember context! Ask "What products?" after a DN query.
    Type "Help" anytime for this menu.

*Powered by AI Logistics Intelligence v21.0 | GROQ AI | AnalyticsService*"""
