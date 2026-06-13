# ==========================================================
# FILE: app/services/ai_provider_service.py (v9.1 - STABLE)
# ==========================================================
# PURPOSE: AI Orchestration for Logistics - SIMPLIFIED & STABLE
# ==========================================================

import json
import time
import re
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from datetime import datetime
from loguru import logger

# Simple Groq import with error handling
GROQ_AVAILABLE = False
GROQ_CLIENT = None

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    logger.warning("Groq SDK not installed - AI features disabled")

from app.config import config


# ==========================================================
# SIMPLE DATA CLASSES
# ==========================================================

@dataclass
class QueryContext:
    """Simple query context"""
    query_type: str
    dealer_name: Optional[str] = None
    dn_number: Optional[str] = None
    warehouse_name: Optional[str] = None
    city_name: Optional[str] = None


# ==========================================================
# WHATSAPP FORMATTER
# ==========================================================

class WhatsAppFormatter:
    """Simple WhatsApp formatter"""
    
    @staticmethod
    def format_dealer_response(dashboard: Dict) -> str:
        """Format dealer dashboard"""
        return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{dashboard.get('dealer_name', 'N/A')}*
📍 City: {dashboard.get('city', 'N/A')}

📊 *PERFORMANCE*
• Total DNs: {dashboard.get('total_dn', 0):,}
• Units: {dashboard.get('total_units', 0):,}
• Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}
• Delivery Rate: {dashboard.get('delivery_rate', 0)}%
• POD Rate: {dashboard.get('pod_rate', 0)}%

⏱️ *AGING*
• Delivery: {dashboard.get('avg_delivery_aging', 0)} days
• POD: {dashboard.get('avg_pod_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_dn_response(dn_detail: Dict) -> str:
        """Format DN details"""
        return f"""
📄 *DN {dn_detail.get('dn_number', 'N/A')}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📅 Date: {dn_detail.get('dn_date', 'N/A')}
Status: {dn_detail.get('delivery_status', 'Unknown')}

🏪 Dealer: {dn_detail.get('dealer_name', 'N/A')}
📍 City: {dn_detail.get('city', 'N/A')}
🏭 Warehouse: {dn_detail.get('warehouse', 'N/A')}

💰 Amount: PKR {dn_detail.get('total_amount', 0):,.0f}
📦 Quantity: {dn_detail.get('total_quantity', 0)}

⏱️ Delivery Aging: {dn_detail.get('delivery_aging', 0)} days
📋 POD Aging: {dn_detail.get('pod_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_warehouse_response(dashboard: Dict) -> str:
        """Format warehouse dashboard"""
        return f"""
🏭 *WAREHOUSE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{dashboard.get('warehouse_name', 'N/A')}*

📊 *VOLUME*
• DNs: {dashboard.get('total_dn', 0):,}
• Units: {dashboard.get('total_units', 0):,}
• Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}

🚚 *DELIVERY SLA*
• Same Day: {dashboard.get('same_day_delivery', 0)}
• 1 Day: {dashboard.get('one_day_delivery', 0)}
• 2 Days: {dashboard.get('two_day_delivery', 0)}
• 3 Days: {dashboard.get('three_day_delivery', 0)}
• 4 Days: {dashboard.get('four_day_delivery', 0)}
• 5+ Days: {dashboard.get('five_plus_delivery', 0)}
• **Average: {dashboard.get('avg_delivery_aging', 0)} days**

⚠️ *PENDING*
• Deliveries: {dashboard.get('pending_delivery', 0)}
• PODs: {dashboard.get('pending_pod', 0)}

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    @staticmethod
    def format_ranking_response(title: str, items: List[Dict], metric: str) -> str:
        """Format ranking response"""
        response = f"🏆 *{title}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        
        for i, item in enumerate(items[:10], 1):
            if metric == 'revenue':
                response += f"{i}. {item.get('name')}: PKR {item.get('value', 0):,.0f}\n"
            else:
                response += f"{i}. {item.get('name')}: {item.get('value', 0)}\n"
        
        return response
    
    @staticmethod
    def format_help_response() -> str:
        """Format help response"""
        return """
🤖 *LOGISTICS AI ASSISTANT*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *DEALER COMMANDS:*
• `Dubai Electronics` - Dealer dashboard

🏭 *WAREHOUSE COMMANDS:*
• `Sargodha Warehouse` - Warehouse dashboard

📊 *RANKING COMMANDS:*
• `Top 10 dealers` - Best dealers
• `Top 10 warehouses` - Best warehouses

📍 *CITY COMMANDS:*
• `Lahore dashboard` - City performance

📦 *DN COMMANDS:*
• `6243610262` - DN details

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type any dealer or warehouse name!
"""


# ==========================================================
# MAIN AI PROVIDER SERVICE (SIMPLIFIED)
# ==========================================================

class AIProviderService:
    """
    Simplified AI Provider Service - Stable version
    """
    
    def __init__(self, analytics_service=None):
        """Initialize with optional analytics service"""
        self.analytics_service = analytics_service
        self.groq_client = None
        
        # Initialize GROQ if available
        self._init_groq()
        
        # Simple caches
        self.dealer_cache = {}
        self.dn_cache = {}
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "cache_hits": 0,
            "start_time": time.time()
        }
        
        logger.info("AI Provider Service v9.1 initialized (Simplified & Stable)")
    
    def _init_groq(self):
        """Initialize GROQ client if available"""
        if not GROQ_AVAILABLE:
            return
        
        groq_api_key = getattr(config, 'GROQ_API_KEY', None)
        if groq_api_key:
            try:
                self.groq_client = Groq(api_key=groq_api_key)
                logger.info("GROQ client initialized successfully")
            except Exception as e:
                logger.error(f"GROQ init failed: {e}")
                self.groq_client = None
    
    def classify_query(self, message: str) -> QueryContext:
        """Classify user query"""
        msg_lower = message.lower().strip()
        
        # DN pattern
        dn_match = re.search(r'\b(\d{8,12})\b', message)
        if dn_match:
            return QueryContext(query_type="dn", dn_number=dn_match.group())
        
        # Warehouse
        warehouses = ['lahore', 'karachi', 'rawalpindi', 'sargodha', 'islamabad', 'multan']
        for wh in warehouses:
            if wh in msg_lower:
                return QueryContext(query_type="warehouse", warehouse_name=wh.title())
        
        # City
        cities = ['lahore', 'karachi', 'islamabad', 'rawalpindi']
        for city in cities:
            if city in msg_lower:
                return QueryContext(query_type="city", city_name=city.title())
        
        # Ranking
        if 'top' in msg_lower:
            return QueryContext(query_type="ranking")
        
        # Help
        if any(word in msg_lower for word in ['help', 'menu', 'commands']):
            return QueryContext(query_type="help")
        
        # Default - dealer
        if len(msg_lower.split()) <= 5:
            return QueryContext(query_type="dealer", dealer_name=message)
        
        return QueryContext(query_type="help")
    
    def process_query(self, message: str, user_id: str = "guest") -> str:
        """Main entry point - process user query"""
        self.metrics["total_requests"] += 1
        start_time = time.time()
        
        try:
            # Classify query
            context = self.classify_query(message)
            
            # Route to appropriate handler
            if context.query_type == "dealer" and context.dealer_name:
                response = self._handle_dealer_query(context.dealer_name)
            elif context.query_type == "dn" and context.dn_number:
                response = self._handle_dn_query(context.dn_number)
            elif context.query_type == "warehouse" and context.warehouse_name:
                response = self._handle_warehouse_query(context.warehouse_name)
            elif context.query_type == "city" and context.city_name:
                response = self._handle_city_query(context.city_name)
            elif context.query_type == "ranking":
                response = self._handle_ranking_query(message)
            else:
                response = WhatsAppFormatter.format_help_response()
            
            self.metrics["successful_requests"] += 1
            
            # Log performance
            elapsed_ms = (time.time() - start_time) * 1000
            logger.debug(f"Query processed in {elapsed_ms:.0f}ms")
            
            return response
            
        except Exception as e:
            logger.exception(f"Query processing failed: {e}")
            return f"❌ Error: {str(e)}\n\nPlease try again or type 'Help'"
    
    def _handle_dealer_query(self, dealer_name: str) -> str:
        """Handle dealer query"""
        # Check cache
        cache_key = f"dealer_{dealer_name.lower()}"
        if cache_key in self.dealer_cache:
            self.metrics["cache_hits"] += 1
            return self.dealer_cache[cache_key]
        
        # Try to get from analytics service
        if self.analytics_service:
            try:
                dashboard = self.analytics_service.get_dealer_dashboard(dealer_name)
                if dashboard and "error" not in dashboard:
                    response = WhatsAppFormatter.format_dealer_response(dashboard)
                    self.dealer_cache[cache_key] = response
                    return response
            except Exception as e:
                logger.error(f"Analytics service error: {e}")
        
        # Fallback response
        return f"""
🏪 *DEALER DASHBOARD: {dealer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Data for {dealer_name} is being loaded.

💡 Try typing a specific DN number or warehouse name.

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    def _handle_dn_query(self, dn_number: str) -> str:
        """Handle DN query"""
        # Check cache
        cache_key = f"dn_{dn_number}"
        if cache_key in self.dn_cache:
            self.metrics["cache_hits"] += 1
            return self.dn_cache[cache_key]
        
        # Try to get from analytics service
        if self.analytics_service:
            try:
                dn_detail = self.analytics_service.get_complete_dn_detail(dn_number)
                if dn_detail and "error" not in dn_detail:
                    response = WhatsAppFormatter.format_dn_response(dn_detail)
                    self.dn_cache[cache_key] = response
                    return response
            except Exception as e:
                logger.error(f"Analytics service error: {e}")
        
        return f"""
📄 *DN {dn_number}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

❌ DN {dn_number} not found.

💡 Please check the DN number and try again.

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    def _handle_warehouse_query(self, warehouse_name: str) -> str:
        """Handle warehouse query"""
        if self.analytics_service:
            try:
                dashboard = self.analytics_service.get_warehouse_dashboard(warehouse_name)
                if dashboard and "error" not in dashboard:
                    return WhatsAppFormatter.format_warehouse_response(dashboard)
            except Exception as e:
                logger.error(f"Analytics service error: {e}")
        
        return f"""
🏭 *WAREHOUSE DASHBOARD: {warehouse_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Data for {warehouse_name} warehouse is being loaded.

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    def _handle_city_query(self, city_name: str) -> str:
        """Handle city query"""
        return f"""
📍 *CITY DASHBOARD: {city_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Data for {city_name} is being loaded.

💡 Type 'Help' for more commands.

━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
    
    def _handle_ranking_query(self, message: str) -> str:
        """Handle ranking query"""
        msg_lower = message.lower()
        
        # Determine ranking type
        if 'dealer' in msg_lower:
            dimension = 'dealer'
        elif 'warehouse' in msg_lower:
            dimension = 'warehouse'
        else:
            dimension = 'dealer'
        
        # Extract limit
        limit_match = re.search(r'top\s+(\d+)', msg_lower)
        limit = int(limit_match.group(1)) if limit_match else 10
        
        # Get from analytics
        if self.analytics_service:
            try:
                if dimension == 'dealer':
                    items = self.analytics_service.get_top_dealers(limit)
                else:
                    items = self.analytics_service.get_top_warehouses(limit)
                
                if items:
                    title = f"TOP {limit} {dimension.upper()}S BY REVENUE"
                    return WhatsAppFormatter.format_ranking_response(title, items, 'revenue')
            except Exception as e:
                logger.error(f"Analytics service error: {e}")
        
        return f"🏆 *TOP {limit} {dimension.upper()}S*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nData is being loaded.\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics"""
        uptime = time.time() - self.metrics["start_time"]
        
        return {
            "total_requests": self.metrics["total_requests"],
            "successful_requests": self.metrics["successful_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "uptime_seconds": round(uptime, 2),
            "groq_enabled": self.groq_client is not None
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Health check endpoint"""
        return {
            "service": "ai_provider_service",
            "version": "9.1",
            "status": "healthy",
            "metrics": self.get_metrics(),
            "timestamp": datetime.now().isoformat()
        }


# ==========================================================
# SINGLETON INSTANCE
# ==========================================================

_ai_provider = None
_analytics_service = None


def set_analytics_service(analytics_service):
    """Set analytics service dependency"""
    global _analytics_service
    _analytics_service = analytics_service
    logger.info("Analytics service injected into AI Provider")


def get_ai_provider() -> AIProviderService:
    """Get singleton instance"""
    global _ai_provider, _analytics_service
    if _ai_provider is None:
        _ai_provider = AIProviderService(analytics_service=_analytics_service)
    return _ai_provider


# ==========================================================
# WHATSAPP COMPATIBILITY FUNCTION
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory,
    phone_number: str = None,
    user_id: str = None,
    request_id: str = None
) -> str:
    """
    WhatsApp compatibility function - Entry point for webhook.
    
    CRITICAL: This function name MUST match what webhook.py imports.
    """
    req_id = request_id or str(uuid.uuid4())[:8]
    user_id_final = user_id or phone_number or "guest"
    
    logger.bind(request_id=req_id).info(f"📞 Processing: {question[:100]}...")
    
    db = None
    try:
        # Create database session
        db = session_factory()
        
        # Import analytics service
        from app.services.analytics_service import AnalyticsService
        
        # Create analytics service
        analytics_service = AnalyticsService(db)
        
        # Set analytics service for AI provider
        set_analytics_service(analytics_service)
        
        # Get AI provider
        ai_provider = get_ai_provider()
        
        # Process query
        response = ai_provider.process_query(question, user_id_final)
        
        logger.bind(request_id=req_id).info(f"✅ Response sent: {len(response)} chars")
        
        return response
        
    except ImportError as e:
        logger.bind(request_id=req_id).exception(f"Import error: {e}")
        return f"⚠️ Service configuration error. Please try again later."
        
    except Exception as e:
        logger.bind(request_id=req_id).exception(f"Query error: {e}")
        return f"❌ Error: {str(e)}. Please try again."
        
    finally:
        if db:
            db.close()


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 60)
logger.info("🤖 AI Provider Service v9.1 - Stable")
logger.info(f"   GROQ: {'✅ Enabled' if GROQ_AVAILABLE else '❌ Disabled'}")
logger.info("   Status: ✅ Ready")
logger.info("=" * 60)
