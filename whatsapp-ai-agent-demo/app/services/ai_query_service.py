# ==========================================================
# FILE: app/services/ai_query_service.py (INTEGRATED v34.0)
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
#              └───────────────┴───────────────┘
#                              ↓
#                    ai_provider_service
# ==========================================================

import re
import time
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
from sqlalchemy.orm import Session
from loguru import logger


# ==========================================================
# INTENT TYPES
# ==========================================================

class Intent(str, Enum):
    DN_LOOKUP = "dn_lookup"
    DN_TIMELINE = "dn_timeline"
    DN_PRODUCTS = "dn_products"
    PENDING_POD = "pending_pod"
    PENDING_PGI = "pending_pgi"
    TOP_DEALERS = "top_dealers"
    TOP_WAREHOUSES = "top_warehouses"
    TOP_PRODUCTS = "top_products"
    EXECUTIVE_DASHBOARD = "executive_dashboard"
    NETWORK_HEALTH = "network_health"
    CONTROL_TOWER = "control_tower"
    HELP = "help"
    GREETING = "greeting"
    GENERAL = "general"


# ==========================================================
# ENTITY EXTRACTION
# ==========================================================

@dataclass
class ExtractedEntities:
    dn_number: Optional[str] = None
    dealer: Optional[str] = None
    warehouse: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    days: Optional[int] = None
    limit: Optional[int] = 10


class EntityExtractor:
    DN_PATTERN = re.compile(r'\b(\d{8,15})\b')
    DAYS_PATTERN = re.compile(r'(\d+)\s+days?', re.IGNORECASE)
    LIMIT_PATTERN = re.compile(r'top\s+(\d+)', re.IGNORECASE)
    
    @classmethod
    def extract(cls, question: str) -> ExtractedEntities:
        question_lower = question.lower().strip()
        entities = ExtractedEntities()
        
        # Extract DN
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
            entities.limit = min(int(limit_match.group(1)), 50)
        
        # Extract dealer
        dealer_match = re.search(r'dealer\s+([A-Za-z0-9\s]+?)(?:\s+$|\.|\,)', question_lower)
        if dealer_match:
            entities.dealer = dealer_match.group(1).strip()
        
        return entities


# ==========================================================
# INTENT DETECTION
# ==========================================================

class IntentDetector:
    @classmethod
    def detect(cls, question: str, entities: ExtractedEntities) -> Intent:
        question_lower = question.lower().strip()
        
        # Help
        if question_lower in ['help', 'menu', 'commands']:
            return Intent.HELP
        
        # Greeting
        if question_lower in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']:
            return Intent.GREETING
        
        # DN present
        if entities.dn_number:
            if 'timeline' in question_lower or 'history' in question_lower:
                return Intent.DN_TIMELINE
            elif 'product' in question_lower or 'item' in question_lower:
                return Intent.DN_PRODUCTS
            else:
                return Intent.DN_LOOKUP
        
        # Keywords
        if 'pending pod' in question_lower or 'pod pending' in question_lower:
            return Intent.PENDING_POD
        if 'pending pgi' in question_lower or 'pgi pending' in question_lower:
            return Intent.PENDING_PGI
        if 'top dealer' in question_lower or 'dealer ranking' in question_lower:
            return Intent.TOP_DEALERS
        if 'top warehouse' in question_lower or 'warehouse ranking' in question_lower:
            return Intent.TOP_WAREHOUSES
        if 'top product' in question_lower or 'product ranking' in question_lower:
            return Intent.TOP_PRODUCTS
        if 'executive dashboard' in question_lower or 'kpi dashboard' in question_lower:
            return Intent.EXECUTIVE_DASHBOARD
        if 'network health' in question_lower or 'system health' in question_lower:
            return Intent.NETWORK_HEALTH
        if 'control tower' in question_lower or 'alerts' in question_lower:
            return Intent.CONTROL_TOWER
        
        return Intent.GENERAL


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    @staticmethod
    def format_success(data: Any, summary: str = None) -> Dict:
        return {"success": True, "data": data, "summary": summary or ""}
    
    @staticmethod
    def format_error(message: str) -> Dict:
        return {"success": False, "data": {}, "summary": message}
    
    @staticmethod
    def format_help() -> str:
        return """
🤖 *AI LOGISTICS ASSISTANT - HELP*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *Track a DN*
• Send any 10+ digit number to track

📋 *Pending Items*
• `Pending POD` - Missing proof of deliveries
• `Pending PGI` - Pending dispatches

🏪 *Analytics*
• `Top dealers` - Dealer rankings
• `Top warehouses` - Warehouse rankings
• `Top products` - Product rankings

📊 *Executive Dashboard*
• `Executive dashboard` - KPI overview
• `Network health` - System status

🚨 *Control Tower*
• `Control tower` - Critical alerts

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

I'm your *AI Logistics Assistant*. I can help you track DNs, check performance, and more.

Type `Help` to see all commands.
"""


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    def __init__(self, db: Session):
        self.db = db
        self._logistics_service = None
        self._analytics_service = None
        self._kpi_service = None
        self._ai_provider = None
        self.formatter = ResponseFormatter()
        logger.info("✅ AI Query Service v34.0 - Pure Router Mode")
    
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
    
    def process_query(self, question: str, user_phone: str = None) -> Dict:
        """Main entry point - Pure routing pipeline."""
        start_time = time.time()
        
        logger.info(f"Processing: {question[:100]}")
        
        # Extract entities
        entities = EntityExtractor.extract(question)
        logger.debug(f"Entities: {entities}")
        
        # Detect intent
        intent = IntentDetector.detect(question, entities)
        logger.info(f"Intent: {intent.value}")
        
        # Route to service
        result = self._route(intent, entities, question)
        
        # Format response
        whatsapp_message = self._to_whatsapp(result)
        
        elapsed_ms = (time.time() - start_time) * 1000
        logger.info(f"Response generated in {elapsed_ms:.0f}ms")
        
        return {
            "success": result.get("success", True),
            "response": whatsapp_message,
            "intent": intent.value,
            "processing_time_ms": round(elapsed_ms, 2)
        }
    
    def _route(self, intent: Intent, entities: ExtractedEntities, question: str) -> Dict:
        """Route to appropriate service."""
        
        # DN Routes
        if intent == Intent.DN_LOOKUP:
            return self._call_logistics("get_complete_dn_intelligence", entities.dn_number)
        if intent == Intent.DN_TIMELINE:
            return self._call_logistics("get_dn_timeline", entities.dn_number)
        if intent == Intent.DN_PRODUCTS:
            return self._call_logistics("get_dn_products", entities.dn_number)
        
        # Pending Routes
        if intent == Intent.PENDING_POD:
            return self._call_logistics("get_pod_status", None)
        if intent == Intent.PENDING_PGI:
            return self._call_logistics("get_pending_pgi", None)
        
        # Analytics Routes
        if intent == Intent.TOP_DEALERS:
            return self._call_analytics("get_top_dealers", entities.limit)
        if intent == Intent.TOP_WAREHOUSES:
            return self._call_analytics("get_top_warehouses", entities.limit)
        if intent == Intent.TOP_PRODUCTS:
            return self._call_analytics("get_top_products", entities.limit)
        
        # KPI Routes
        if intent == Intent.EXECUTIVE_DASHBOARD:
            return self._call_kpi("get_executive_dashboard", 30)
        if intent == Intent.NETWORK_HEALTH:
            return self._call_kpi("get_network_health", 30)
        if intent == Intent.CONTROL_TOWER:
            return self._call_control_tower()
        
        # General Routes
        if intent == Intent.HELP:
            return self.formatter.format_success({}, self.formatter.format_help())
        if intent == Intent.GREETING:
            return self.formatter.format_success({}, self.formatter.format_greeting())
        
        # Default: AI Chat
        return self._call_ai("general", question)
    
    def _call_logistics(self, method: str, *args) -> Dict:
        if not self.logistics_service:
            return self.formatter.format_error("Logistics service unavailable")
        try:
            service_method = getattr(self.logistics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"Logistics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_analytics(self, method: str, *args) -> Dict:
        if not self.analytics_service:
            return self.formatter.format_error("Analytics service unavailable")
        try:
            service_method = getattr(self.analytics_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"Analytics call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_kpi(self, method: str, *args) -> Dict:
        if not self.kpi_service:
            return self.formatter.format_error("KPI service unavailable")
        try:
            service_method = getattr(self.kpi_service, method, None)
            if not service_method:
                return self.formatter.format_error(f"Method '{method}' not available")
            result = service_method(*args) if args else service_method()
            if isinstance(result, dict) and result.get("error"):
                return self.formatter.format_error(result["error"])
            return self.formatter.format_success(result)
        except Exception as e:
            logger.error(f"KPI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _call_control_tower(self) -> Dict:
        alerts = {}
        if self.logistics_service:
            try:
                pod_result = self.logistics_service.get_pod_status()
                if pod_result and not pod_result.get("error"):
                    alerts["pending_pods"] = pod_result
            except Exception as e:
                logger.error(f"Failed to get POD status: {e}")
        if self.kpi_service:
            try:
                risks = self.kpi_service.get_risk_alerts()
                if risks and not risks.get("error"):
                    alerts["risk_alerts"] = risks
            except Exception as e:
                logger.error(f"Failed to get risk alerts: {e}")
        
        pending_count = alerts.get("pending_pods", {}).get("pending_count", 0)
        alert_count = alerts.get("risk_alerts", {}).get("total_alerts", 0)
        
        summary = f"🚨 *CONTROL TOWER*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        summary += f"📋 Pending PODs: {pending_count}\n"
        summary += f"🔔 Active Alerts: {alert_count}\n"
        
        if alert_count > 0:
            summary += f"\n🔴 Immediate attention required!"
        else:
            summary += f"\n✅ All systems operational"
        
        return self.formatter.format_success(alerts, summary)
    
    def _call_ai(self, analysis_type: str, question: str) -> Dict:
        if not self.ai_provider:
            return self.formatter.format_error("AI service unavailable")
        try:
            result = self.ai_provider.chat(question, "guest")
            response_text = result if isinstance(result, str) else str(result)
            return self.formatter.format_success({"insight": response_text}, response_text)
        except Exception as e:
            logger.error(f"AI call failed: {e}")
            return self.formatter.format_error(str(e))
    
    def _to_whatsapp(self, response: Dict) -> str:
        if not response.get("success"):
            return f"❌ {response.get('summary', 'Unable to process request')}"
        summary = response.get("summary", "")
        if summary:
            return summary
        return "✅ Request processed successfully"
    
    def health_check(self) -> Dict:
        return {
            "service": "ai_query_service",
            "version": "34.0",
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
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, phone_number: str = None, user_id: str = None) -> str:
    try:
        service = AIQueryService(db)
        result = service.process_query(question, phone_number or user_id)
        return result.get("response", "⚠️ Unable to process your request.")
    except Exception as e:
        logger.exception(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."


logger.info("=" * 60)
logger.info("🧠 AI QUERY SERVICE v34.0 - PURE ROUTER MODE")
logger.info("   Integrated with: Logistics | Analytics | KPI | AI Provider")
logger.info("=" * 60)
