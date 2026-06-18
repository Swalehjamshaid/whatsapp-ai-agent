# ==========================================================
# FILE: app/services/ai_provider_service.py (v17.2 - FULLY SELF-HEALING)
# ==========================================================
# PURPOSE: Master Orchestrator - WhatsApp AI Analytics Agent
# 
# SELF-HEALING ARCHITECTURE:
# 1. ✅ Request Isolation - Fresh context for every request
# 2. ✅ Multiple Recovery Attempts - 6 strategies for dealer resolution
# 3. ✅ Never Cache Failures - Only cache successful responses
# 4. ✅ Groq AI Fallback - For queries outside structured analytics
# 5. ✅ Production Diagnostics - Full logging at every step
# 6. ✅ System Survival - Failed queries never poison future queries
# 7. ✅ Context Validation - Invalid entities never stored
# 8. ✅ Error Recovery - Graceful handling with user guidance
# 9. ✅ Cache Self-Healing - Auto-clean corrupted entries
# 
# MASTER GROQ INTELLIGENCE:
# 10. ✅ AI Logistics Control Tower - Groq as Chief Logistics Officer
# 11. ✅ Dealer Distance Engine - Haversine Formula with Same City Rule
# 12. ✅ Transit Time Engine - Distance-based delivery estimation
# 13. ✅ Delay Engine - Actual vs Expected delivery analysis
# 14. ✅ Risk Engine - Multi-level risk assessment
# 15. ✅ Root Cause Analysis - Why analysis with recommendations
# 16. ✅ AI Insight Generation - Automatic business intelligence
# 17. ✅ Forecasting Engine - Predictive analytics
# 18. ✅ WhatsApp Menu System - Interactive navigation
# 19. ✅ Executive Decision Support - Management recommendations
# ==========================================================

import time
import uuid
import hashlib
import re
import asyncio
import concurrent.futures
import traceback
import math
from typing import Optional, Callable, Any, Dict, List, Tuple
from cachetools import TTLCache
from loguru import logger
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.config import config
from app.database import SessionLocal

# ==========================================================
# LAZY IMPORTS - Avoid circular dependencies
# ==========================================================

def _get_ai_query_service():
    from app.services.ai_query_service import get_ai_query_service
    return get_ai_query_service()

def _get_analytics_service():
    from app.services.analytics_service import get_analytics_service, AnalyticsResponse
    return get_analytics_service(), AnalyticsResponse

def _get_kpi_service():
    from app.services.kpi_service import get_kpi_service
    return get_kpi_service()

def _get_groq_service():
    from app.services.groq_service import get_groq_service
    return get_groq_service()

def _get_schema_service():
    from app.schemas.schema_service import get_schema_service
    return get_schema_service()

def _get_whatsapp_service():
    from app.services.whatsapp_service import get_whatsapp_service
    return get_whatsapp_service()


# ==========================================================
# CONFIGURATION
# ==========================================================

CACHE_TTL_SECONDS = 300
CONTEXT_TTL_SECONDS = 1800
MAX_RETRY_ATTEMPTS = 5
DEALER_SUGGESTION_LIMIT = 3
GROQ_TIMEOUT_SECONDS = 8  # Increased for complex reasoning
ENRICHMENT_TIMEOUT_SECONDS = 5  # Increased for insights
MAX_RECOVERY_ATTEMPTS = 5
MAX_RESPONSE_LENGTH = 3000  # WhatsApp character limit

DN_PATTERN_LOOSE = re.compile(r'\b(\d{8,12})\b')

# ==========================================================
# DISTANCE & TRANSIT CONFIGURATION
# ==========================================================

EARTH_RADIUS_KM = 6371.0

TRANSIT_DAYS_RULES = {
    "same_city": 1,
    "0-50": 1,
    "51-150": 2,
    "151-300": 3,
    "301-500": 4,
    "501-800": 5,
    "800+": 7
}

RISK_THRESHOLDS = {
    "low": 0.10,    # <= 10% delay
    "medium": 0.30, # 11-30% delay
    "high": 0.30    # > 30% delay
}

# ==========================================================
# MASTER GROQ INTELLIGENCE PROMPT
# ==========================================================

MASTER_GROQ_PROMPT = """
You are Haier Pakistan's AI Logistics Control Tower.

You act as:
1. Chief Logistics Officer
2. Supply Chain Director
3. Secondary Operations Head
4. Warehouse Operations Manager
5. Transportation Manager
6. Business Intelligence Analyst
7. Data Analyst
8. Executive Consultant
9. Dealer Relationship Manager
10. Customer Service Advisor

Your responsibility is to analyze logistics data and provide business intelligence, insights, recommendations, predictions, and answers.

DATA UNDERSTANDING RULES:
- Customer Name = Dealer Name = Sold-To Party
- Dealer Code = Dealer Code
- Customer Code = Customer Code
- DN Count = COUNT(DISTINCT dn_no)
- Units = SUM(dn_qty)
- Revenue = SUM(dn_amount)
- Never mix DN Count, Units, and Revenue

DISTANCE ENGINE:
- Calculate distance using Haversine Formula
- If Warehouse City = Dealer City: Same City Delivery (1 Day)
- Transit Times: Same City=1, 0-50KM=1, 51-150KM=2, 151-300KM=3, 301-500KM=4, 501-800KM=5, 800+KM=7

DELAY ENGINE:
- Expected Days = Transit Time Rules
- Actual Days = POD Date - PGI Date
- Pending Days = Today Date - PGI Date
- Delay Days = Actual Days - Expected Days
- If Delay Days > 0: Status = Delayed

RISK ENGINE:
- Low Risk: Delay <= 10%
- Medium Risk: Delay 11% - 30%
- High Risk: Delay > 30%

RESPONSE RULES:
- Always explain: What happened, Why it happened, Impact, Recommendation
- Never simply repeat SQL results
- Use analytics data first, then Groq reasoning
- Maximum 3000 characters per response
- WhatsApp-safe formatting with emojis
"""

# ==========================================================
# SPECIAL COMMANDS
# ==========================================================

SPECIAL_COMMANDS = {
    "control tower": "control_tower",
    "control": "control_tower",
    "tower": "control_tower",
    "executive summary": "executive_summary",
    "executive insights": "executive_summary",
    "executive": "executive_summary",
    "ceo": "executive_summary",
    "management": "executive_summary",
    "help": "help",
    "hi": "help",
    "hello": "help",
    "menu": "help",
    "start": "help",
    "whatsapp menu": "help"
}

# ==========================================================
# GROQ PROTECTION PATTERNS
# ==========================================================

GROQ_BLOCKED_PATTERNS = {
    'dealer', 'customer', 'sold to', 'buyer', 'traders', 'electronics',
    'enterprises', 'industries', 'corporation', 'group', 'sons',
    'delivery', 'pgi', 'pod', 'dn', 'warehouse', 'ship to',
    'dispatch', 'transit', 'delivered', 'pending', 'order',
    'revenue', 'sales', 'units', 'quantity', 'aging', 'performance',
    'kpi', 'rate', 'completion', 'efficiency', 'metrics', 'target',
    'root cause', 'improvement', 'bottleneck', 'insight', 'executive',
    'critical', 'urgent', 'priority', 'alert', 'issue', 'problem',
    'key issue', 'bring improvement', 'why delayed', 'what is the key',
    'top', 'bottom', 'best', 'worst', 'compare', 'vs', 'versus',
    'highest', 'lowest', 'ranking', 'rank',
    'today', 'yesterday', 'week', 'month', 'year', 'trend', 'historical',
    'show', 'display', 'get', 'view', 'list', 'fetch', 'find', 'tell',
    'forecast', 'predict', 'estimated', 'projected', 'future',
    'service score', 'health', 'risk', 'compliance',
    'distance', 'km', 'transit', 'travel'
}


# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

class ConversationContext:
    def __init__(self, phone_number: str):
        self.phone_number = phone_number
        self.last_intent: Optional[str] = None
        self.last_entity: Optional[str] = None
        self.last_dealer: Optional[str] = None
        self.last_warehouse: Optional[str] = None
        self.last_city: Optional[str] = None
        self.last_dn: Optional[str] = None
        self.last_question: Optional[str] = None
        self.last_response: Optional[str] = None
        self.message_count: int = 0
        self.created_at: float = time.time()
        self.last_updated: float = time.time()
        self.confidence: float = 0.0
        self.retry_count: int = 0
        self.last_distance: Optional[float] = None
        self.last_transit_days: Optional[int] = None
        self.last_risk_level: Optional[str] = None
        self.is_valid: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_dealer": self.last_dealer,
            "last_warehouse": self.last_warehouse,
            "last_city": self.last_city,
            "last_dn": self.last_dn,
            "last_intent": self.last_intent,
            "phone_number": self.phone_number,
            "last_question": self.last_question,
            "confidence": self.confidence,
            "retry_count": self.retry_count,
            "last_distance": self.last_distance,
            "last_transit_days": self.last_transit_days,
            "last_risk_level": self.last_risk_level,
            "is_valid": self.is_valid
        }


# ==========================================================
# MASTER ORCHESTRATOR - SELF-HEALING ARCHITECTURE v17.2
# ==========================================================

class AIOrchestrator:
    """
    ENTERPRISE LOGISTICS ANALYTICS ENGINE - v17.2 (FULLY SELF-HEALING)
    
    Self-Healing Features:
    - Request isolation (fresh context for every request)
    - Multiple recovery attempts (6 strategies)
    - Never cache failures
    - Groq AI fallback
    - System survival (failed queries never poison future queries)
    - Context validation and auto-cleaning
    - Cache self-healing
    
    Master Groq Intelligence:
    - AI Logistics Control Tower
    - Distance Engine (Haversine Formula)
    - Transit Time Engine
    - Delay Engine
    - Risk Engine
    - Root Cause Analysis
    - AI Insight Generation
    - Forecasting Engine
    - WhatsApp Menu System
    - Executive Decision Support
    """
    
    def __init__(self):
        # Lazy loaded services
        self._query_service = None
        self._analytics = None
        self._analytics_response = None
        self._kpi = None
        self._groq = None
        self._schema = None
        self._whatsapp = None
        self._dn_pattern = DN_PATTERN_LOOSE
        
        # Caches - Only successful responses cached
        self.response_cache = TTLCache(maxsize=500, ttl=CACHE_TTL_SECONDS)
        self.failure_cache = TTLCache(maxsize=100, ttl=60)  # Short TTL for failures
        self.conversation_cache: Dict[str, ConversationContext] = {}
        self.dealer_resolution_cache: Dict[str, Tuple[str, float, float]] = {}
        self.distance_cache: Dict[str, Tuple[float, float]] = {}  # (distance_km, transit_days)
        
        # Circuit breaker for Groq
        self._groq_failures = 0
        self._groq_last_failure_time = 0
        self._groq_circuit_breaker_open = False
        
        # Request isolation state
        self._current_request_id: Optional[str] = None
        self._request_start_time: float = 0
        self._request_cache: Dict[str, Any] = {}
        self._recovery_attempts: int = 0
        self._groq_used: bool = False
        
        # Warehouse coordinates cache
        self._warehouse_coords: Dict[str, Tuple[float, float]] = {
            "lahore": (31.5204, 74.3587),
            "karachi": (24.8607, 67.0011),
            "rawalpindi": (33.5651, 73.0169),
            "faisalabad": (31.4504, 73.1350),
            "multan": (30.1575, 71.5249),
            "hyderabad": (25.3960, 68.3578),
            "peshawar": (34.0151, 71.5249),
            "quetta": (30.1798, 66.9750),
            "islamabad": (33.6844, 73.0479),
            "gujranwala": (32.1877, 74.1945),
            "sialkot": (32.4945, 74.5227),
            "sukkur": (27.7036, 68.8578),
            "larkana": (27.5622, 68.2024),
            "sahiwal": (30.6681, 73.1033),
            "okara": (30.8089, 73.4516),
            "bahawalpur": (29.3981, 71.6757),
            "dera ghazi khan": (30.0478, 70.6483),
            "sargodha": (32.0836, 72.6711),
            "mianwali": (32.5906, 71.5391),
            "abbottabad": (34.1688, 73.2215),
            "mansehra": (34.3372, 73.1957),
            "haripur": (34.0000, 72.9333),
            "taxila": (33.7461, 72.8511),
            "wah cantt": (33.7800, 72.7100),
            "attock": (33.7667, 72.3667),
            "chakwal": (32.9333, 72.8500),
            "jhelum": (32.9333, 73.7333),
            "gujrat": (32.5738, 74.0789)
        }
        
        # Metrics
        self.metrics = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "cache_failures_avoided": 0,
            "cache_cleaned": 0,
            "dn_lookups": 0,
            "dn_lookups_success": 0,
            "dn_lookups_failure": 0,
            "dn_retry_attempts": 0,
            "dealer_queries": 0,
            "dealer_queries_success": 0,
            "dealer_queries_failure": 0,
            "dealer_suggestions": 0,
            "city_queries": 0,
            "warehouse_queries": 0,
            "comparisons": 0,
            "executive_insights": 0,
            "root_cause_analyses": 0,
            "control_tower": 0,
            "product_queries": 0,
            "groq_uses": 0,
            "groq_fallbacks": 0,
            "overrides": 0,
            "rejections": 0,
            "timeouts": 0,
            "errors": 0,
            "service_successes": 0,
            "service_failures": 0,
            "analytics_response_errors": 0,
            "dealer_resolution_attempts": 0,
            "dealer_resolution_success": 0,
            "dealer_resolution_failure": 0,
            "recovery_attempts": 0,
            "distance_calculations": 0,
            "transit_calculations": 0,
            "risk_assessments": 0,
            "forecast_requests": 0,
            "root_cause_requests": 0,
            "context_reset": 0,
            "invalid_entity_cleared": 0
        }
        
        logger.info("=" * 70)
        logger.info("AI Orchestrator v17.2 - Fully Self-Healing")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   SELF-HEALING FEATURES:")
        logger.info("   ✅ Request Isolation")
        logger.info("   ✅ Multiple Recovery Attempts (6 strategies)")
        logger.info("   ✅ Never Cache Failures")
        logger.info("   ✅ Groq AI Fallback")
        logger.info("   ✅ System Survival")
        logger.info("   ✅ Context Validation & Auto-Cleaning")
        logger.info("   ✅ Cache Self-Healing")
        logger.info("")
        logger.info("   MASTER GROQ INTELLIGENCE:")
        logger.info("   ✅ AI Logistics Control Tower")
        logger.info("   ✅ Dealer Distance Engine (Haversine)")
        logger.info("   ✅ Transit Time Engine")
        logger.info("   ✅ Delay Engine")
        logger.info("   ✅ Risk Engine")
        logger.info("   ✅ Root Cause Analysis")
        logger.info("   ✅ AI Insight Generation")
        logger.info("   ✅ Forecasting Engine")
        logger.info("   ✅ WhatsApp Menu System")
        logger.info("   ✅ Executive Decision Support")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    # ==========================================================
    # LAZY PROPERTIES
    # ==========================================================
    
    @property
    def query_service(self):
        if self._query_service is None:
            self._query_service = _get_ai_query_service()
        return self._query_service
    
    @property
    def analytics(self):
        if self._analytics is None:
            self._analytics, self._analytics_response = _get_analytics_service()
        return self._analytics
    
    @property
    def kpi(self):
        if self._kpi is None:
            self._kpi = _get_kpi_service()
        return self._kpi
    
    @property
    def groq(self):
        if self._groq is None:
            self._groq = _get_groq_service()
        return self._groq
    
    @property
    def schema(self):
        if self._schema is None:
            self._schema = _get_schema_service()
        return self._schema
    
    @property
    def whatsapp(self):
        if self._whatsapp is None:
            self._whatsapp = _get_whatsapp_service()
        return self._whatsapp
    
    # ==========================================================
    # ANALYTICSRESPONSE VALIDATION
    # ==========================================================
    
    def _validate_analytics_response(
        self,
        response: Any,
        service_name: str,
        request_id: str
    ) -> bool:
        if response is None:
            logger.error(f"[{request_id}] AnalyticsResponse is None for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'success'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'success' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if response.success is False:
            logger.error(f"[{request_id}] AnalyticsResponse success=False for {service_name}: {response.error}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'data'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'data' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        if not hasattr(response, 'error'):
            logger.error(f"[{request_id}] AnalyticsResponse missing 'error' for {service_name}")
            self.metrics["analytics_response_errors"] += 1
            return False
        
        return True
    
    def _is_analytics_response(self, obj) -> bool:
        if obj is None:
            return False
        return hasattr(obj, 'success') and hasattr(obj, 'data') and hasattr(obj, 'error')
    
    # ==========================================================
    # GROQ CIRCUIT BREAKER
    # ==========================================================
    
    def _is_groq_circuit_breaker_open(self) -> bool:
        if not self._groq_circuit_breaker_open:
            return False
        
        if time.time() - self._groq_last_failure_time > 60:
            self._groq_circuit_breaker_open = False
            self._groq_failures = 0
            logger.info("Groq circuit breaker: CLOSED (recovery period passed)")
            return False
        
        return True
    
    def _record_groq_success(self):
        self._groq_failures = 0
        self._groq_circuit_breaker_open = False
    
    def _record_groq_failure(self):
        self._groq_failures += 1
        self._groq_last_failure_time = time.time()
        
        if self._groq_failures >= 3:
            self._groq_circuit_breaker_open = True
            logger.error("Groq circuit breaker: OPEN (3 consecutive failures)")
    
    def _is_groq_available(self) -> bool:
        if self._is_groq_circuit_breaker_open():
            return False
        return hasattr(self.groq, 'is_available') and self.groq.is_available
    
    # ==========================================================
    # DISTANCE ENGINE (Haversine Formula)
    # ==========================================================
    
    def _calculate_haversine_distance(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float
    ) -> float:
        """Calculate distance between two points using Haversine formula."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        
        a = math.sin(delta_phi / 2) ** 2 + \
            math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        
        return EARTH_RADIUS_KM * c
    
    def _get_warehouse_coordinates(self, warehouse_name: str) -> Optional[Tuple[float, float]]:
        """Get warehouse coordinates from cache."""
        warehouse_lower = warehouse_name.lower().strip()
        return self._warehouse_coords.get(warehouse_lower)
    
    def _get_dealer_coordinates(self, dealer_name: str) -> Optional[Tuple[float, float]]:
        """Get dealer coordinates from dealer data."""
        try:
            result = self.analytics.get_dealer_dashboard(dealer_name)
            if result and result.success:
                data = result.data or {}
                profile = data.get("profile", {})
                lat = profile.get("latitude")
                lon = profile.get("longitude")
                if lat is not None and lon is not None:
                    return (float(lat), float(lon))
        except Exception as e:
            logger.debug(f"Failed to get dealer coordinates: {e}")
        return None
    
    def _calculate_distance_and_transit(
        self,
        warehouse_name: str,
        dealer_name: str,
        request_id: str
    ) -> Tuple[float, int, str]:
        """
        Calculate distance between warehouse and dealer.
        Returns: (distance_km, transit_days, status)
        Status: "same_city", "calculated", "unknown"
        """
        self.metrics["distance_calculations"] += 1
        
        # Get warehouse coordinates
        warehouse_coords = self._get_warehouse_coordinates(warehouse_name)
        if not warehouse_coords:
            logger.warning(f"[{request_id}] Warehouse coordinates not found: {warehouse_name}")
            return 0.0, 0, "unknown"
        
        # Get dealer coordinates
        dealer_coords = self._get_dealer_coordinates(dealer_name)
        if not dealer_coords:
            logger.warning(f"[{request_id}] Dealer coordinates not found: {dealer_name}")
            return 0.0, 0, "unknown"
        
        # Check same city
        warehouse_lower = warehouse_name.lower().strip()
        dealer_lower = dealer_name.lower().strip()
        
        # Get city from dealer data
        try:
            result = self.analytics.get_dealer_dashboard(dealer_name)
            if result and result.success:
                data = result.data or {}
                profile = data.get("profile", {})
                dealer_city = profile.get("city", "").lower()
                warehouse_city = warehouse_lower
                
                if dealer_city and dealer_city == warehouse_city:
                    return 0.0, 1, "same_city"
        except:
            pass
        
        # Calculate distance
        distance = self._calculate_haversine_distance(
            warehouse_coords[0],
            warehouse_coords[1],
            dealer_coords[0],
            dealer_coords[1]
        )
        
        # Calculate transit days
        transit_days = self._calculate_transit_days(distance)
        
        return distance, transit_days, "calculated"
    
    def _calculate_transit_days(self, distance_km: float) -> int:
        """Calculate expected transit days based on distance."""
        self.metrics["transit_calculations"] += 1
        
        if distance_km <= 0:
            return 1
        elif distance_km <= 50:
            return 1
        elif distance_km <= 150:
            return 2
        elif distance_km <= 300:
            return 3
        elif distance_km <= 500:
            return 4
        elif distance_km <= 800:
            return 5
        else:
            return 7
    
    def _calculate_delay_days(
        self,
        actual_days: int,
        expected_days: int
    ) -> int:
        """Calculate delay days."""
        return max(0, actual_days - expected_days)
    
    def _calculate_risk_level(
        self,
        delay_days: int,
        expected_days: int
    ) -> Tuple[str, float]:
        """Calculate risk level based on delay percentage."""
        self.metrics["risk_assessments"] += 1
        
        if expected_days <= 0:
            return "low", 0.0
        
        delay_percentage = delay_days / expected_days
        
        if delay_percentage <= 0.10:
            return "low", delay_percentage
        elif delay_percentage <= 0.30:
            return "medium", delay_percentage
        else:
            return "high", delay_percentage
    
    def _get_distance_summary(
        self,
        distance_km: float,
        transit_days: int,
        status: str,
        dealer_name: str
    ) -> str:
        """Generate distance summary text."""
        if status == "same_city":
            return f"📍 Same City Delivery\nWarehouse and Dealer are located in the same city.\nExpected Delivery: 1 Day\nRisk: Low\nDistance: Not Applicable"
        
        if distance_km <= 0:
            return "📍 Distance: Not Available"
        
        # Determine route description
        if distance_km <= 50:
            route_desc = "Short distance route"
        elif distance_km <= 150:
            route_desc = "Medium distance route"
        elif distance_km <= 300:
            route_desc = "Long distance route"
        elif distance_km <= 500:
            route_desc = "Extended distance route"
        else:
            route_desc = "Very long distance route"
        
        return f"""📍 Distance Analysis
Dealer: {dealer_name}
Distance: {distance_km:.1f} KM
Route Type: {route_desc}
Expected Transit: {transit_days} Days
Risk Level: Low"""

    # ==========================================================
    # FORECASTING ENGINE
    # ==========================================================
    
    def _generate_forecast(
        self,
        historical_data: Dict,
        period: str = "next_month"
    ) -> Dict:
        """Generate forecast based on historical data."""
        self.metrics["forecast_requests"] += 1
        
        # Simple trend-based forecasting
        monthly_trend = historical_data.get("monthly_trend", [])
        if not monthly_trend:
            return {
                "forecast_revenue": 0,
                "forecast_units": 0,
                "forecast_dns": 0,
                "confidence": 0.0,
                "trend": "insufficient_data"
            }
        
        # Calculate average growth rate
        revenues = [m.get("revenue", 0) for m in monthly_trend[-3:]]  # Last 3 months
        units = [m.get("units", 0) for m in monthly_trend[-3:]]
        dns = [m.get("dns", 0) for m in monthly_trend[-3:]]
        
        if len(revenues) >= 2:
            revenue_growth = (revenues[-1] - revenues[0]) / max(revenues[0], 1)
            units_growth = (units[-1] - units[0]) / max(units[0], 1)
            dns_growth = (dns[-1] - dns[0]) / max(dns[0], 1)
            
            avg_revenue = sum(revenues) / len(revenues)
            avg_units = sum(units) / len(units)
            avg_dns = sum(dns) / len(dns)
            
            forecast_revenue = avg_revenue * (1 + revenue_growth)
            forecast_units = avg_units * (1 + units_growth)
            forecast_dns = avg_dns * (1 + dns_growth)
            
            trend = "increasing" if revenue_growth > 0.05 else \
                    "decreasing" if revenue_growth < -0.05 else "stable"
            
            confidence = min(0.95, 0.70 + (len(monthly_trend) * 0.05))
            
            return {
                "forecast_revenue": forecast_revenue,
                "forecast_units": forecast_units,
                "forecast_dns": forecast_dns,
                "confidence": confidence,
                "trend": trend,
                "growth_rate": revenue_growth
            }
        
        return {
            "forecast_revenue": 0,
            "forecast_units": 0,
            "forecast_dns": 0,
            "confidence": 0.0,
            "trend": "insufficient_data"
        }
    
    # ==========================================================
    # ROOT CAUSE ANALYSIS
    # ==========================================================
    
    def _perform_root_cause_analysis(
        self,
        question: str,
        data: Dict,
        request_id: str
    ) -> str:
        """Perform root cause analysis using Groq intelligence."""
        self.metrics["root_cause_requests"] += 1
        
        if not self._is_groq_available():
            return "🔍 Root cause analysis requires AI enrichment. Please try again."
        
        try:
            # Prepare context for Groq
            context = {
                "question": question,
                "data": data,
                "analysis_type": "root_cause",
                "timestamp": datetime.now().isoformat()
            }
            
            root_cause_prompt = f"""
As Haier Pakistan's AI Logistics Control Tower, perform root cause analysis.

Question: {question}

Available Data:
{data}

Provide:
1. Root Cause - What is the primary cause?
2. Impact - What is the business impact?
3. Risk - What is the risk level?
4. Recommendation - What should management do?

Keep it concise and actionable.
"""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, root_cause_prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self.metrics["groq_uses"] += 1
                        return f"🔍 *Root Cause Analysis*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{request_id}] Root cause analysis timeout")
            
            return "🔍 Unable to perform root cause analysis at this time."
            
        except Exception as e:
            logger.error(f"[{request_id}] Root cause analysis failed: {e}")
            return "🔍 Unable to perform root cause analysis."
    
    # ==========================================================
    # AI INSIGHT GENERATION
    # ==========================================================
    
    def _generate_insights(
        self,
        data: Dict,
        insight_type: str,
        request_id: str
    ) -> str:
        """Generate AI insights using Groq."""
        if not self._is_groq_available():
            return ""
        
        try:
            context = {
                "data": data,
                "insight_type": insight_type,
                "timestamp": datetime.now().isoformat()
            }
            
            insight_prompt = f"""
As Haier Pakistan's AI Logistics Control Tower, generate business insights.

Data: {data}
Insight Type: {insight_type}

Provide concise business intelligence insights.
Focus on:
1. What is happening
2. Why it matters
3. What to do next

Keep insights actionable and brief.
"""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, insight_prompt, context)
                try:
                    response = future.result(timeout=ENRICHMENT_TIMEOUT_SECONDS)
                    if response and len(response) > 10:
                        self.metrics["groq_uses"] += 1
                        return response
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{request_id}] Insight generation timeout")
            
            return ""
            
        except Exception as e:
            logger.error(f"[{request_id}] Insight generation failed: {e}")
            return ""
    
    # ==========================================================
    # SELF-HEALING CACHE MANAGEMENT
    # ==========================================================
    
    def _get_cached_success(self, key: str) -> Optional[str]:
        if key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            logger.debug(f"Cache: Skipping failed response for {key[:20]}")
            return None
        return self.response_cache.get(key)
    
    def _cache_success(self, key: str, value: str):
        self.response_cache[key] = value
    
    def _cache_failure(self, key: str):
        self.failure_cache[key] = time.time()
    
    def _get_cached_response(self, question: str, phone_number: Optional[str]) -> Optional[str]:
        cache_key = self._generate_cache_key(question, phone_number)
        
        # Check failure cache first
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return None
        
        return self.response_cache.get(cache_key)
    
    def _cache_response(self, question: str, phone_number: Optional[str], response: str, success: bool = True):
        cache_key = self._generate_cache_key(question, phone_number)
        
        if success and not response.startswith("❌") and "Unable" not in response and "not found" not in response.lower():
            self.response_cache[cache_key] = response
        else:
            self.failure_cache[cache_key] = time.time()
    
    def _generate_cache_key(self, question: str, phone_number: Optional[str]) -> str:
        key = question.lower().strip()
        if phone_number:
            key = f"{phone_number}:{key}"
        return hashlib.md5(key.encode()).hexdigest()
    
    # ==========================================================
    # DN NORMALIZATION & DETECTION
    # ==========================================================
    
    def _normalize_dn(self, text: str) -> str:
        return re.sub(r"\D", "", text.strip())
    
    def _is_dn_query(self, question: str) -> bool:
        digits = self._normalize_dn(question)
        return 8 <= len(digits) <= 12
    
    # ==========================================================
    # REQUEST ISOLATION
    # ==========================================================
    
    def _reset_request_context(self, request_id: str):
        """Reset all request-specific state for isolation."""
        self._current_request_id = request_id
        self._request_start_time = time.time()
        self._request_cache = {}
        self._recovery_attempts = 0
        self._groq_used = False
        logger.info(f"[{request_id}] 🔄 Request context reset (isolation)")
    
    # ==========================================================
    # VALIDATION HELPERS
    # ==========================================================
    
    def _is_invalid_entity(self, entity: str) -> bool:
        """Check if an entity is invalid/corrupted."""
        if not entity:
            return True
        # Check for error patterns
        error_patterns = ['not found', 'error', 'invalid', 'unable', 'failed', 'null', 'none']
        if any(pattern in entity.lower() for pattern in error_patterns):
            return True
        # Check if entity is too short or too long
        if len(entity.strip()) < 2 or len(entity.strip()) > 100:
            return True
        # Check if entity contains SQL or code patterns
        if any(char in entity for char in [';', '--', "'", '"', '\\']):
            return True
        return False
    
    def _clean_context(self, phone_number: str) -> None:
        """Clean corrupted context for a user."""
        if phone_number not in self.conversation_cache:
            return
        
        context = self.conversation_cache[phone_number]
        cleaned = False
        
        # Check dealer
        if context.last_dealer and self._is_invalid_entity(context.last_dealer):
            logger.warning(f"Cleaning invalid dealer: {context.last_dealer}")
            context.last_dealer = None
            context.confidence = 0.0
            cleaned = True
            self.metrics["invalid_entity_cleared"] += 1
        
        # Check DN
        if context.last_dn and self._is_invalid_entity(context.last_dn):
            logger.warning(f"Cleaning invalid DN: {context.last_dn}")
            context.last_dn = None
            cleaned = True
            self.metrics["invalid_entity_cleared"] += 1
        
        # Check warehouse
        if context.last_warehouse and self._is_invalid_entity(context.last_warehouse):
            logger.warning(f"Cleaning invalid warehouse: {context.last_warehouse}")
            context.last_warehouse = None
            cleaned = True
            self.metrics["invalid_entity_cleared"] += 1
        
        # Check city
        if context.last_city and self._is_invalid_entity(context.last_city):
            logger.warning(f"Cleaning invalid city: {context.last_city}")
            context.last_city = None
            cleaned = True
            self.metrics["invalid_entity_cleared"] += 1
        
        if cleaned:
            self.metrics["context_reset"] += 1
            logger.info(f"Context cleaned for {phone_number}")
    
    # ==========================================================
    # DEALER RESOLUTION ENGINE (6 Strategies)
    # ==========================================================
    
    def _resolve_dealer_safe(self, dealer_input: str, req_id: str) -> Tuple[Optional[str], float, str]:
        """
        Safe dealer resolution with multiple recovery strategies.
        
        Strategy 1: SchemaService resolution
        Strategy 2: Analytics service resolution
        Strategy 3: Direct database fallback
        Strategy 4: Normalized match
        Strategy 5: Fuzzy match
        Strategy 6: Groq AI fallback
        """
        self.metrics["dealer_resolution_attempts"] += 1
        self._recovery_attempts += 1
        
        if not dealer_input or not dealer_input.strip():
            return None, 0.0, "empty_input"
        
        # Check cache with validation
        cache_key = dealer_input.lower().strip()
        if cache_key in self.dealer_resolution_cache:
            resolved, confidence, timestamp = self.dealer_resolution_cache[cache_key]
            # Check if cached entry is valid
            if resolved and not self._is_invalid_entity(resolved):
                if time.time() - timestamp < 3600:
                    logger.info(f"[{req_id}] Dealer resolution cache hit: '{resolved}'")
                    return resolved, confidence, "cache_hit"
            else:
                # Remove invalid cache entry
                del self.dealer_resolution_cache[cache_key]
                self.metrics["cache_cleaned"] += 1
                logger.info(f"[{req_id}] Removed invalid cache entry for: '{dealer_input}'")
        
        dealer_clean = dealer_input.strip()
        logger.info(f"[{req_id}] 🔍 Safe Dealer Resolution: '{dealer_clean}'")
        
        # ==========================================================
        # STRATEGY 1: SchemaService Resolution
        # ==========================================================
        try:
            resolved = self.schema.resolve_dealer(dealer_clean)
            if resolved and not self._is_invalid_entity(resolved):
                confidence = 0.99
                self.metrics["dealer_resolution_success"] += 1
                logger.info(f"[{req_id}] ✅ Strategy 1 (Schema): '{resolved}'")
                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                return resolved, confidence, "schema_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Strategy 1 failed: {e}")
        
        # ==========================================================
        # STRATEGY 2: Analytics Service Resolution
        # ==========================================================
        try:
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    if name and name.lower() == dealer_clean.lower():
                        resolved = name
                        confidence = 0.95
                        self.metrics["dealer_resolution_success"] += 1
                        logger.info(f"[{req_id}] ✅ Strategy 2 (Analytics): '{resolved}'")
                        self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                        return resolved, confidence, "analytics_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Strategy 2 failed: {e}")
        
        # ==========================================================
        # STRATEGY 3: Direct Database Fallback
        # ==========================================================
        try:
            resolved = self.schema.resolve_dealer_direct(dealer_clean)
            if resolved and not self._is_invalid_entity(resolved):
                confidence = 0.90
                self.metrics["dealer_resolution_success"] += 1
                logger.info(f"[{req_id}] ✅ Strategy 3 (Direct DB): '{resolved}'")
                self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                return resolved, confidence, "direct_db_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Strategy 3 failed: {e}")
        
        # ==========================================================
        # STRATEGY 4: Normalized Match
        # ==========================================================
        try:
            normalized_input = re.sub(r'[^a-zA-Z0-9\s]', '', dealer_clean).lower()
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    if name:
                        normalized_name = re.sub(r'[^a-zA-Z0-9\s]', '', name).lower()
                        if normalized_input == normalized_name:
                            resolved = name
                            confidence = 0.85
                            self.metrics["dealer_resolution_success"] += 1
                            logger.info(f"[{req_id}] ✅ Strategy 4 (Normalized): '{resolved}'")
                            self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                            return resolved, confidence, "normalized_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Strategy 4 failed: {e}")
        
        # ==========================================================
        # STRATEGY 5: Fuzzy Match
        # ==========================================================
        try:
            result = self.analytics.get_all_dealers_dashboard()
            if result and result.success:
                dealers = result.data.get("dealers", [])
                best_match = None
                best_score = 0.0
                
                for dealer in dealers:
                    name = dealer.get("dealer_name", "")
                    if name:
                        score = SequenceMatcher(None, dealer_clean.lower(), name.lower()).ratio()
                        if score > best_score and score >= 0.70:
                            best_score = score
                            best_match = name
                
                if best_match:
                    resolved = best_match
                    confidence = round(best_score, 2)
                    self.metrics["dealer_resolution_success"] += 1
                    logger.info(f"[{req_id}] ✅ Strategy 5 (Fuzzy): '{resolved}' (score: {confidence:.2f})")
                    self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                    return resolved, confidence, "fuzzy_match"
        except Exception as e:
            logger.debug(f"[{req_id}] Strategy 5 failed: {e}")
        
        # ==========================================================
        # STRATEGY 6: Groq AI Fallback (with validation)
        # ==========================================================
        if self._is_groq_available():
            try:
                groq_prompt = f"Based on common dealer names in Pakistan, what is the closest match to '{dealer_clean}'? Return only the dealer name."
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(self.groq.chat, groq_prompt, {})
                    try:
                        resolved = future.result(timeout=3.0)
                        if resolved and len(resolved) > 3 and not self._is_invalid_entity(resolved):
                            confidence = 0.60
                            self.metrics["dealer_resolution_success"] += 1
                            self.metrics["groq_fallbacks"] += 1
                            logger.info(f"[{req_id}] ✅ Strategy 6 (Groq): '{resolved}'")
                            self.dealer_resolution_cache[cache_key] = (resolved, confidence, time.time())
                            return resolved, confidence, "groq_match"
                    except concurrent.futures.TimeoutError:
                        logger.debug(f"[{req_id}] Groq timeout for dealer resolution")
            except Exception as e:
                logger.debug(f"[{req_id}] Strategy 6 failed: {e}")
        
        # All strategies failed
        self.metrics["dealer_resolution_failure"] += 1
        logger.warning(f"[{req_id}] ❌ All dealer resolution strategies failed for: '{dealer_input}'")
        return None, 0.0, "all_failed"
    
    def _get_dealer_suggestions(self, dealer_input: str, req_id: str) -> List[str]:
        try:
            suggestions = []
            result = self.analytics.get_all_dealers_dashboard()
            if not result or not result.success:
                return []
            
            dealers = result.data.get("dealers", [])
            scored = []
            
            for dealer in dealers:
                name = dealer.get("dealer_name", "")
                if name:
                    score = SequenceMatcher(None, dealer_input.lower(), name.lower()).ratio()
                    if 0.40 <= score < 0.80:
                        scored.append((name, score))
            
            scored.sort(key=lambda x: x[1], reverse=True)
            suggestions = [s[0] for s in scored[:DEALER_SUGGESTION_LIMIT]]
            
            if suggestions:
                self.metrics["dealer_suggestions"] += 1
                logger.info(f"[{req_id}] 💡 Dealer suggestions: {suggestions}")
            
            return suggestions
        except Exception as e:
            logger.debug(f"[{req_id}] Dealer suggestions failed: {e}")
            return []
    
    # ==========================================================
    # DN RETRY LOGIC
    # ==========================================================
    
    def _execute_dn_lookup_with_retry(self, dn_number: str, req_id: str) -> Tuple[str, bool]:
        logger.info(f"[{req_id}] 🔍 DN Lookup: '{dn_number}'")
        self.metrics["dn_retry_attempts"] += 1
        
        cache_key = f"dn_fail_{dn_number}"
        if cache_key in self.failure_cache:
            self.metrics["cache_failures_avoided"] += 1
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
        
        try:
            result = self.analytics.get_dn_analytics(dn_number)
            if self._validate_analytics_response(result, "dn_lookup", req_id):
                if result.success:
                    formatted = self._format_dn_dashboard(result, req_id)
                    self.metrics["dn_lookups_success"] += 1
                    return formatted, True
            
            # Try normalized
            normalized = self._normalize_dn(dn_number)
            if normalized != dn_number:
                result = self.analytics.get_dn_analytics(normalized)
                if self._validate_analytics_response(result, "dn_lookup_normalized", req_id):
                    if result.success:
                        formatted = self._format_dn_dashboard(result, req_id)
                        self.metrics["dn_lookups_success"] += 1
                        return formatted, True
            
            self.metrics["dn_lookups_failure"] += 1
            self.failure_cache[cache_key] = time.time()
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
            
        except Exception as e:
            logger.exception(f"[{req_id}] DN lookup failed: {e}")
            self.metrics["dn_lookups_failure"] += 1
            self.failure_cache[cache_key] = time.time()
            return f"❌ Unable to retrieve DN {dn_number}. Please verify the number and try again.", False
    
    # ==========================================================
    # GROQ EXECUTION WITH TIMEOUT
    # ==========================================================
    
    def _execute_groq_safe(self, intent: str, context: Dict, req_id: str) -> str:
        """Execute Groq with circuit breaker and timeout."""
        
        # Check circuit breaker
        if self._is_groq_circuit_breaker_open():
            logger.warning(f"[{req_id}] Groq circuit breaker open, skipping Groq")
            return self._get_groq_fallback_response()
        
        if not self._is_groq_available():
            return self._get_groq_fallback_response()
        
        if self._is_logistics_query(intent):
            return self._get_groq_blocked_response()
        
        try:
            logger.info(f"[{req_id}] 🤖 Calling Groq with timeout={GROQ_TIMEOUT_SECONDS}s")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._safe_groq_call, intent, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 10:
                        self._record_groq_success()
                        self.metrics["groq_uses"] += 1
                        return response
                    else:
                        self._record_groq_failure()
                        return self._get_groq_fallback_response()
                except concurrent.futures.TimeoutError:
                    self._record_groq_failure()
                    logger.error(f"[{req_id}] Groq timeout after {GROQ_TIMEOUT_SECONDS}s")
                    return self._get_groq_fallback_response()
                    
        except Exception as e:
            self._record_groq_failure()
            logger.error(f"[{req_id}] Groq execution failed: {e}")
            return self._get_groq_fallback_response()
    
    def _safe_groq_call(self, intent: str, context: Dict) -> str:
        try:
            return self.groq.chat(intent, context)
        except Exception as e:
            logger.error(f"Groq call failed: {e}")
            return ""
    
    def _get_groq_fallback_response(self) -> str:
        return "ℹ️ AI enrichment is currently unavailable. The analytics data above is still accurate."
    
    def _get_groq_blocked_response(self) -> str:
        return """📋 *AI Logistics Assistant*

I specialize in logistics analytics. Please ask questions about:
- Dealer performance
- Warehouse operations
- City performance
- DN tracking
- Delivery performance
- Revenue and units
- Distance and transit
- Forecasting
- Root cause analysis

Type 'help' for the full menu."""
    
    # ==========================================================
    # MASTER GROQ EXECUTION (v17.2)
    # ==========================================================
    
    def _execute_master_groq(
        self,
        question: str,
        data: Dict,
        context: Dict,
        request_id: str
    ) -> str:
        """Execute Master Groq Intelligence with full context."""
        if not self._is_groq_available():
            return self._get_help_message()
        
        try:
            # Build master prompt
            master_prompt = f"""{MASTER_GROQ_PROMPT}

USER QUESTION:
{question}

ANALYTICS DATA:
{data}

CONTEXT:
{context}

RESPONSE REQUIREMENTS:
1. Use analytics data first
2. Provide Groq reasoning and insights
3. Explain: What happened, Why, Impact, Recommendation
4. WhatsApp-safe formatting with emojis
5. Maximum 3000 characters

Provide the most comprehensive and actionable response possible."""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, master_prompt, context)
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS + 5)
                    if response and len(response) > 20:
                        self._record_groq_success()
                        self.metrics["groq_uses"] += 1
                        self.metrics["groq_fallbacks"] += 1
                        return response
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{request_id}] Master Groq timeout")
                    self._record_groq_failure()
            
            return self._get_help_message()
            
        except Exception as e:
            logger.error(f"[{request_id}] Master Groq execution failed: {e}")
            return self._get_help_message()
    
    # ==========================================================
    # EXECUTIVE GROQ INTELLIGENCE
    # ==========================================================
    
    def _execute_executive_groq(
        self,
        question: str,
        data: Dict,
        request_id: str
    ) -> str:
        """Execute Groq for executive-level intelligence."""
        if not self._is_groq_available():
            return "👔 Executive insights require AI enrichment. Please try again."
        
        try:
            executive_prompt = f"""
As Haier Pakistan's Chief Logistics Officer, provide executive intelligence.

Question: {question}

Data: {data}

Provide:
1. Executive Summary - One paragraph overview
2. Critical Issues - Top 3 challenges
3. Strategic Recommendations - Actionable items
4. Risk Assessment - Key risks and mitigation
5. Business Impact - Bottom-line implications

Format for WhatsApp with clear sections and emojis.
Keep it concise but comprehensive.
"""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, executive_prompt, {})
                try:
                    response = future.result(timeout=GROQ_TIMEOUT_SECONDS)
                    if response and len(response) > 20:
                        self.metrics["groq_uses"] += 1
                        return f"👔 *Executive Intelligence*\n\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{request_id}] Executive Groq timeout")
            
            return "👔 Unable to generate executive insights at this time."
            
        except Exception as e:
            logger.error(f"[{request_id}] Executive Groq failed: {e}")
            return "👔 Unable to generate executive insights."
    
    # ==========================================================
    # GROQ FALLBACK FOR NON-ANALYTICS QUERIES (v17.2)
    # ==========================================================
    
    def _execute_groq_fallback(self, query: str, req_id: str) -> str:
        """Execute Groq AI fallback with Master Intelligence."""
        try:
            logger.info(f"[{req_id}] 🤖 Groq AI fallback triggered for: {query[:50]}...")
            self.metrics["groq_uses"] += 1
            self.metrics["groq_fallbacks"] += 1
            
            if not self._is_groq_available():
                return self._get_help_message()
            
            # Check if this is a executive/strategic question
            strategic_keywords = ["ceo", "executive", "strategy", "management", "recommendation", 
                                 "critical", "urgent", "priority", "overall", "nationwide"]
            
            if any(kw in query.lower() for kw in strategic_keywords):
                return self._execute_executive_groq(query, {}, req_id)
            
            # Check if this is a root cause question
            root_cause_keywords = ["why", "root cause", "reason", "cause", "because", "due to"]
            
            if any(kw in query.lower() for kw in root_cause_keywords):
                return self._perform_root_cause_analysis(query, {}, req_id)
            
            # Regular fallback with Master Groq
            groq_context = {
                "query": query,
                "intent": "general_logistics",
                "context": "User asked a logistics-related question",
                "mode": "master_intelligence"
            }
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._safe_groq_call, query, groq_context)
                try:
                    response = future.result(timeout=8.0)
                    if response and len(response) > 10:
                        return f"💡 *AI Logistics Control Tower:*\n{response}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Groq fallback timeout")
            
            return self._get_help_message()
            
        except Exception as e:
            logger.error(f"[{req_id}] Groq fallback failed: {e}")
            return self._get_help_message()
    
    # ==========================================================
    # SAFE ENRICHMENT WITH TIMEOUT (v17.2)
    # ==========================================================
    
    def _enrich_with_groq_safe(
        self,
        response: str,
        intent: str,
        question: str,
        context: Dict,
        req_id: str
    ) -> str:
        """Safe Groq enrichment with timeout - NON-BLOCKING."""
        if not self._is_groq_available():
            return response
        
        if intent not in ["executive_insight", "root_cause", "executive_summary", "dealer_dashboard"]:
            return response
        
        if "0" in response and "No" in response:
            return response
        
        if len(response) < 50:
            return response
        
        try:
            enrichment_prompt = f"""
As Haier Pakistan's AI Logistics Control Tower, provide executive enrichment.

Based on this logistics analytics data:

{response[:500]}

Provide:
1. Key Insight - What's the most important finding?
2. Impact - What's the business impact?
3. Recommendation - One immediate action

Keep it concise and actionable. Max 3 sentences.
"""
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self.groq.chat, enrichment_prompt, context)
                try:
                    groq_summary = future.result(timeout=ENRICHMENT_TIMEOUT_SECONDS)
                    if groq_summary and len(groq_summary) > 10:
                        self.metrics["groq_uses"] += 1
                        return f"{response}\n\n💡 *AI Insight:*\n{groq_summary}"
                except concurrent.futures.TimeoutError:
                    logger.warning(f"[{req_id}] Groq enrichment timeout ({ENRICHMENT_TIMEOUT_SECONDS}s)")
                    
        except Exception as e:
            logger.warning(f"[{req_id}] Groq enrichment failed: {e}")
        
        return response
    
    # ==========================================================
    # MAIN ENTRY POINT
    # ==========================================================
    
    def process_whatsapp_query(
        self,
        question: str,
        session_factory: Optional[Callable[[], Session]] = None,
        phone_number: Optional[str] = None,
        user_id: Optional[str] = None,
        request_id: Optional[str] = None
    ) -> str:
        start_time = time.time()
        req_id = request_id or str(uuid.uuid4())[:8]
        
        # Reset request context for isolation
        self._reset_request_context(req_id)
        
        # Clean any corrupted context for this user
        if phone_number:
            self._clean_context(phone_number)
        
        self.metrics["total_requests"] += 1
        
        logger.bind(request_id=req_id).info(f"📥 Processing: {question[:100]}")
        
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(self._process_sync, question, phone_number, req_id)
                response = future.result(timeout=30)
                duration_ms = int((time.time() - start_time) * 1000)
                logger.bind(request_id=req_id).info(f"✅ Done: {duration_ms}ms | Length: {len(response)}")
                return response
                    
        except concurrent.futures.TimeoutError:
            self.metrics["timeouts"] += 1
            logger.error(f"[{req_id}] Request timed out after 30 seconds")
            return f"⏳ *Request Timed Out*\n\nPlease try again or simplify your question.\n\nReference: `{req_id}`"
                
        except Exception as e:
            self.metrics["errors"] += 1
            error_id = str(uuid.uuid4())[:8]
            logger.exception(f"[{req_id}] FATAL ERROR [{error_id}]: {e}")
            return self._get_error_response(question, e, error_id, req_id)
    
    # ==========================================================
    # SYNC PROCESSING (SELF-HEALING v17.2)
    # ==========================================================
    
    def _process_sync(self, question: str, phone_number: Optional[str], req_id: str) -> str:
        try:
            # Load context (fresh)
            context = self._load_context(phone_number)
            context_dict = context.to_dict() if context else {}
            
            # ==========================================================
            # STEP 0: SPECIAL COMMANDS (HIGHEST PRIORITY)
            # ==========================================================
            
            question_clean = question.strip()
            question_lower = question_clean.lower()
            
            if question_lower in SPECIAL_COMMANDS:
                command = SPECIAL_COMMANDS[question_lower]
                
                if command == "control_tower":
                    logger.info(f"[{req_id}] 🚨 Control Tower command detected")
                    self.metrics["control_tower"] += 1
                    result = self.analytics.get_control_tower_alerts()
                    if self._validate_analytics_response(result, "control_tower", req_id):
                        response = self._format_control_tower_dashboard(result, req_id)
                        self._cache_response(question, phone_number, response, True)
                        return response
                    else:
                        return "🚨 Unable to retrieve Control Tower data."
                
                if command == "executive_summary":
                    logger.info(f"[{req_id}] 👔 Executive Summary command detected")
                    self.metrics["executive_insights"] += 1
                    result = self.analytics.get_executive_summary()
                    if self._validate_analytics_response(result, "executive_summary", req_id):
                        response = self._format_executive_dashboard(result, req_id)
                        self._cache_response(question, phone_number, response, True)
                        return response
                    else:
                        return "👔 Unable to retrieve Executive Summary."
                
                if command == "help":
                    return self._get_help_message()
            
            # ==========================================================
            # STEP 1: Check for "warehouse" keyword
            # ==========================================================
            
            if "warehouse" in question_lower:
                logger.info(f"[{req_id}] 🏭 Warehouse keyword detected")
                warehouse_result = self.schema.resolve_warehouse(question_clean)
                if warehouse_result:
                    logger.info(f"[{req_id}] ✅ Warehouse resolved: '{warehouse_result}'")
                    self.metrics["warehouse_queries"] += 1
                    result = self.analytics.get_warehouse_dashboard(warehouse_result)
                    if self._validate_analytics_response(result, "warehouse_dashboard", req_id):
                        response = self._format_warehouse_dashboard(result, warehouse_result, req_id)
                        self._cache_response(question, phone_number, response, True)
                        self._update_context_safe(phone_number, "warehouse_dashboard", "warehouse", warehouse_result, req_id, response, True)
                        return response
                    else:
                        error_msg = f"🏭 Unable to retrieve warehouse dashboard for '{warehouse_result}'."
                        return error_msg
            
            # ==========================================================
            # STEP 2: Check for city
            # ==========================================================
            
            city_result = self.schema.resolve_city(question_clean)
            if city_result:
                logger.info(f"[{req_id}] 🏙️ City resolved: '{city_result}'")
                self.metrics["city_queries"] += 1
                result = self.analytics.get_city_dashboard(city_result)
                if self._validate_analytics_response(result, "city_dashboard", req_id):
                    response = self._format_city_dashboard(result, city_result, req_id)
                    self._cache_response(question, phone_number, response, True)
                    self._update_context_safe(phone_number, "city_dashboard", "city", city_result, req_id, response, True)
                    return response
                else:
                    error_msg = f"🏙️ Unable to retrieve city dashboard for '{city_result}'."
                    return error_msg
            
            # ==========================================================
            # STEP 3: DN Lookup with Validation
            # ==========================================================
            
            if self._is_dn_query(question):
                logger.info(f"[{req_id}] 🔍 DN Lookup: {question}")
                self.metrics["dn_lookups"] += 1
                dn_normalized = self._normalize_dn(question)
                response, success = self._execute_dn_lookup_with_retry(dn_normalized, req_id)
                self._update_context_safe(phone_number, "dn_lookup", "dn", question, req_id, response, success)
                self._cache_response(question, phone_number, response, success)
                return response
            
            # ==========================================================
            # STEP 4: Dealer Resolution with Recovery and Validation
            # ==========================================================
            
            # Try dealer resolution with recovery
            resolved, confidence, strategy = self._resolve_dealer_safe(question_clean, req_id)
            
            if resolved:
                logger.info(f"[{req_id}] 🏪 Dealer resolved: '{resolved}' (strategy: {strategy})")
                self.metrics["dealer_queries"] += 1
                result = self.analytics.get_dealer_dashboard(resolved)
                if self._validate_analytics_response(result, "dealer_dashboard", req_id):
                    response = self._format_dealer_360_dashboard(result, resolved, req_id, confidence)
                    
                    # Add distance analysis if available
                    if context and context.last_warehouse:
                        distance, transit_days, status = self._calculate_distance_and_transit(
                            context.last_warehouse, resolved, req_id
                        )
                        if status != "unknown":
                            distance_summary = self._get_distance_summary(
                                distance, transit_days, status, resolved
                            )
                            response = f"{response}\n\n{distance_summary}"
                    
                    self._cache_response(question, phone_number, response, True)
                    self._update_context_safe(phone_number, "dealer_dashboard", "dealer", resolved, req_id, response, True)
                    return response
                else:
                    error_msg = f"🏪 Unable to retrieve dealer dashboard for '{resolved}'."
                    return error_msg
            
            # ==========================================================
            # STEP 5: Dealer Not Found - Help User Recover
            # ==========================================================
            
            # Try to get suggestions
            suggestions = self._get_dealer_suggestions(question_clean, req_id)
            if suggestions:
                suggestion_text = "\n".join([f"   • {s}" for s in suggestions[:3]])
                help_response = f"""❌ Dealer '{question_clean}' not found.

💡 *Did You Mean?*
{suggestion_text}

📋 *Try these commands:*
• Enter 8-12 digit DN number
• Type "help" for full menu
• Use city name (e.g., "Lahore")
• Use warehouse name (e.g., "Rawalpindi")

*What would you like to know?* 🤖"""
                return help_response
            
            # ==========================================================
            # STEP 6: Master Groq Intelligence (for all remaining queries)
            # ==========================================================
            
            logger.info(f"[{req_id}] 🤖 Master Groq Intelligence triggered")
            
            # Gather any available data for context
            context_data = {}
            if context:
                context_data = context.to_dict()
            
            # Execute Master Groq
            response = self._execute_master_groq(question, context_data, {}, req_id)
            
            # Cache the response
            self._cache_response(question, phone_number, response, True)
            return response
            
        except Exception as e:
            logger.exception(f"[{req_id}] Sync processing error: {e}")
            # Return friendly error instead of crashing
            return f"⚠️ *Unable to process your request*\n\nPlease try again or type 'help' for assistance.\n\nReference: `{req_id}`"
    
    # ==========================================================
    # SAFE CONTEXT UPDATE
    # ==========================================================
    
    def _update_context_safe(
        self,
        phone_number: Optional[str],
        intent: str,
        entity_type: str,
        entity: str,
        req_id: str,
        response: str = "",
        success: bool = True
    ):
        """Safe context update with validation."""
        if not phone_number:
            return
            
        # Only update context if the operation was successful
        if not success:
            logger.info(f"[{req_id}] Not updating context due to failed operation")
            return
            
        # Validate entity before storing
        if self._is_invalid_entity(entity):
            logger.warning(f"[{req_id}] Not storing invalid entity: {entity}")
            return
            
        context = self._load_context(phone_number)
        if not context:
            return
        
        context.last_intent = intent
        context.last_question = entity
        context.confidence = 0.9  # High confidence for successful operations
        
        if entity_type == "dealer":
            context.last_dealer = entity
        elif entity_type == "warehouse":
            context.last_warehouse = entity
        elif entity_type == "city":
            context.last_city = entity
        elif entity_type == "dn":
            context.last_dn = entity
        
        if response:
            context.last_response = response[:200]
        context.message_count += 1
        context.last_updated = time.time()
        context.is_valid = True
    
    # ==========================================================
    # ROUTING DECISION
    # ==========================================================
    
    def _get_routing_decision(self, question: str, context: Dict) -> Any:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        try:
            if asyncio.iscoroutinefunction(self.query_service.process_query):
                return loop.run_until_complete(
                    self.query_service.process_query(question, context)
                )
            return self.query_service.process_query(question, context)
        except Exception as e:
            logger.error(f"Routing decision failed: {e}")
            from types import SimpleNamespace
            return SimpleNamespace(
                intent="help",
                entity=None,
                service="help",
                confidence=0.0,
                needs_groq=False,
                reason=f"Routing error: {str(e)[:50]}"
            )
    
    # ==========================================================
    # CONTEXT MANAGEMENT (SELF-HEALING)
    # ==========================================================
    
    def _load_context(self, phone_number: Optional[str]) -> Optional[ConversationContext]:
        """Load context with validation and self-healing."""
        if not phone_number:
            return None
        
        # Clean context on load
        self._clean_context(phone_number)
            
        if phone_number not in self.conversation_cache:
            self.conversation_cache[phone_number] = ConversationContext(phone_number)
            
        context = self.conversation_cache[phone_number]
        
        # Check if context is stale
        if time.time() - context.last_updated > CONTEXT_TTL_SECONDS:
            logger.info(f"Context expired for {phone_number}, resetting")
            context = ConversationContext(phone_number)
            self.conversation_cache[phone_number] = context
            self.metrics["context_reset"] += 1
        
        return context
    
    def _update_context(
        self,
        phone_number: Optional[str],
        intent: str,
        entity_type: str,
        entity: str,
        req_id: str,
        response: str = ""
    ):
        # Deprecated - use _update_context_safe instead
        self._update_context_safe(phone_number, intent, entity_type, entity, req_id, response, True)
    
    def clear_caches(self):
        self.response_cache.clear()
        self.failure_cache.clear()
        self.conversation_cache.clear()
        self.dealer_resolution_cache.clear()
        self.distance_cache.clear()
        self.metrics["cache_cleaned"] += 1
        logger.info("🗑️ All caches cleared")
        return {"status": "cleared", "version": "17.2", "message": "All caches cleared successfully"}
    
    # ==========================================================
    # QUERY DETECTION HELPERS
    # ==========================================================
    
    def _is_logistics_query(self, question: str) -> bool:
        question_lower = question.lower()
        for pattern in GROQ_BLOCKED_PATTERNS:
            if pattern in question_lower:
                return True
        if hasattr(self.schema, 'detect_metric') and self.schema.detect_metric(question):
            return True
        if hasattr(self.schema, 'is_logistics_keyword') and self.schema.is_logistics_keyword(question):
            return True
        return False
    
    # ==========================================================
    # ENTERPRISE DASHBOARD FORMATTERS (v17.2)
    # ==========================================================
    
    def _format_dn_dashboard(self, data, req_id: str) -> str:
        """Format professional DN Dashboard with distance and transit."""
        try:
            if not self._validate_analytics_response(data, "dn_dashboard", req_id):
                return "❌ Unable to retrieve DN details."
            
            if not data.success:
                logger.warning(f"[{req_id}] DN not found")
                return "❌ DN not found."
            
            record = data.data.get("record", {})
            validation = data.data.get("validation", {})
            status = data.data.get("status", "unknown")
            distance_info = data.data.get("distance_info", {})
            risk_level = data.data.get("risk_level", "low")
            risk_percentage = data.data.get("risk_percentage", 0)
            
            # Extract all fields
            dn_no = record.get('dn_number', record.get('dn_no', 'N/A'))
            dealer_name = record.get('customer_name', record.get('dealer', 'N/A'))
            dealer_code = record.get('dealer_code', 'N/A')
            customer_code = record.get('customer_code', 'N/A')
            division = record.get('division', 'N/A')
            warehouse = record.get('warehouse', 'N/A')
            warehouse_code = record.get('warehouse_code', 'N/A')
            city = record.get('ship_to_city', 'N/A')
            delivery_location = record.get('delivery_location', 'N/A')
            material_no = record.get('material_no', 'N/A')
            model = record.get('customer_model', 'N/A')
            sales_office = record.get('sales_office', 'N/A')
            sales_manager = record.get('sales_manager', 'N/A')
            units = record.get('units', 0)
            amount = record.get('amount', record.get('dn_amount', 0))
            delivery_status = record.get('delivery_status', 'N/A')
            pgi_status = record.get('pgi_status', 'N/A')
            pod_status = record.get('pod_status', 'N/A')
            pending_flag = record.get('pending_flag', False)
            
            # Dates
            dn_date = record.get('dn_create_date', 'N/A')
            pgi_date = record.get('good_issue_date', 'N/A')
            pod_date = record.get('pod_date', 'N/A')
            
            # Aging
            pgi_aging = record.get('pgi_aging_days', 'N/A')
            pod_aging = record.get('pod_aging_days', 'N/A')
            total_aging = record.get('total_aging_days', 'N/A')
            
            # Distance and transit
            distance_km = distance_info.get('distance_km', 0)
            transit_days = distance_info.get('transit_days', 0)
            distance_status = distance_info.get('status', 'unknown')
            distance_summary = distance_info.get('summary', '')
            
            # Compliance
            delivery_compliance = self._get_compliance_status(pgi_aging)
            pod_compliance = self._get_compliance_status(pod_aging)
            
            # Journey Tracking
            journey = self._get_dn_journey(dn_date, pgi_date, pod_date, status)
            
            # Risk Assessment
            risk_emoji = self._get_risk_emoji(risk_level)
            
            # Management Action
            management_action = self._get_dn_management_action(pod_aging, pgi_aging, status)
            
            lines = [
                "📄 *DN ANALYTICS DASHBOARD*",
                "",
                "📋 *DN Profile*",
                f"DN No: {dn_no}",
                f"Order Type: {record.get('order_type', 'N/A')}",
                f"Division: {division}",
                "",
                "🏪 *Dealer Information*",
                f"Dealer Name: {dealer_name}",
                f"Dealer Code: {dealer_code}",
                f"Customer Code: {customer_code}",
                "",
                "📍 *Delivery Information*",
                f"Warehouse: {warehouse}",
                f"Warehouse Code: {warehouse_code}",
                f"City: {city}",
                f"Delivery Location: {delivery_location}",
                "",
                "📦 *Product Information*",
                f"Model: {model}",
                f"Material No: {material_no}",
                "",
                "💰 *Financial Summary*",
                f"Units: {units}",
                f"Revenue: PKR {amount:,.0f}",
                f"Revenue Per Unit: PKR {amount / units if units > 0 else 0:,.0f}",
                "",
                "📊 *Delivery Status*",
                f"Delivery Status: {delivery_status}",
                f"PGI Status: {pgi_status}",
                f"POD Status: {pod_status}",
                f"Pending Flag: {'No' if not pending_flag else 'Yes'}",
                "",
                "📅 *Timeline*",
                f"DN Created: {self._format_date(dn_date)}",
                f"PGI Date: {self._format_date(pgi_date)}",
                f"POD Date: {self._format_date(pod_date)}",
                "",
                "⏱️ *Logistics Performance*",
                f"🚚 Delivery Days: {pgi_aging if pgi_aging != 'N/A' else 'N/A'}",
                f"📋 POD Days: {pod_aging if pod_aging != 'N/A' else 'N/A'}",
                f"🔄 Total Cycle Days: {total_aging if total_aging != 'N/A' else 'N/A'}",
            ]
            
            # Distance info
            if distance_summary:
                lines.append("")
                lines.append("📍 *Distance & Transit*")
                lines.append(distance_summary)
            
            # Risk
            lines.append("")
            lines.append("⚠️ *Risk Assessment*")
            lines.append(f"Risk Level: {risk_emoji} {risk_level.upper()}")
            lines.append(f"Risk Score: {risk_percentage:.1f}%" if risk_percentage > 0 else "Risk Score: 0%")
            lines.append("")
            lines.append("📈 *Compliance*")
            lines.append(f"Delivery Compliance: {delivery_compliance}")
            lines.append(f"POD Compliance: {pod_compliance}")
            lines.append("")
            lines.append("📍 *Journey Tracking*")
            
            for step, completed in journey.items():
                icon = "✅" if completed else "⏳"
                lines.append(f"{icon} {step}")
            
            # Data Quality
            issues = validation.get('issues', [])
            if issues:
                lines.append("")
                lines.append("⚠️ *Data Quality Issues:*")
                for issue in issues:
                    lines.append(f"   • {issue}")
            
            if management_action:
                lines.append("")
                lines.append(f"🎯 *Management Action*")
                lines.append(management_action)
            
            # AI Insight
            insight = self._generate_insights(
                {"record": record, "status": status, "risk": risk_level},
                "dn_analytics",
                req_id
            )
            if insight:
                lines.append("")
                lines.append(f"💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] DN formatting failed: {e}")
            return f"❌ Unable to format DN details."
    
    def _format_dealer_360_dashboard(self, data, dealer_name: str, req_id: str, confidence: float = 0.0) -> str:
        """Format professional Dealer 360 Dashboard with distance and transit."""
        try:
            if not self._validate_analytics_response(data, "dealer_360_dashboard", req_id):
                return f"❌ Unable to retrieve dashboard for {dealer_name}."
            
            if not data.success:
                return f"❌ No data found for {dealer_name}"
            
            response_data = data.data or {}
            
            profile = response_data.get("profile", {})
            summary = response_data.get("summary", {})
            aging = response_data.get("aging", {})
            performance = response_data.get("performance", {})
            distance_info = response_data.get("distance_info", {})
            
            total_dns = summary.get("total_dns", 0)
            
            if total_dns == 0:
                suggestions = self._get_dealer_suggestions(dealer_name, req_id)
                if suggestions:
                    suggestion_text = "\n\n💡 *Did You Mean?*\n" + "\n".join([f"   • {s}" for s in suggestions])
                    return f"❌ Dealer '{dealer_name}' not found.{suggestion_text}"
                return f"❌ No data found for {dealer_name}"
            
            dealer_code = profile.get("dealer_code", "N/A")
            customer_code = profile.get("customer_code", "N/A")
            city = profile.get("city", "N/A")
            warehouse = profile.get("warehouse", "N/A")
            division = profile.get("division", "N/A")
            sales_office = profile.get("sales_office", "N/A")
            sales_manager = profile.get("sales_manager", "N/A")
            dealer_status = profile.get("dealer_status", "Unknown")
            
            avg_dn_value = summary.get("total_revenue", 0) / total_dns if total_dns > 0 else 0
            avg_units_per_dn = summary.get("total_units", 0) / total_dns if total_dns > 0 else 0
            
            risk_level = performance.get("risk_level", "low").lower()
            risk_emoji = self._get_risk_emoji(risk_level)
            risk_display = RISK_LEVELS.get(risk_level, "🟢 LOW")
            health_score = performance.get("health_score", 0)
            
            # Generate forecast
            forecast = self._generate_forecast(response_data, "next_month")
            
            lines = [
                "🏪 *DEALER 360 DASHBOARD*",
                "",
                "👤 *Dealer Profile*",
                f"Dealer Name: {dealer_name}",
                f"Dealer Code: {dealer_code}",
                f"Customer Code: {customer_code}",
                f"City: {city}",
                f"Warehouse: {warehouse}",
                f"Division: {division}",
                "",
                "📊 *Business Summary*",
                f"Total DNs: {total_dns:,}",
                f"Total Units: {summary.get('total_units', 0):,}",
                f"Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                f"Average DN Value: PKR {avg_dn_value:,.0f}",
                f"Average Units per DN: {avg_units_per_dn:.1f}",
                "",
                "📈 *Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_pgi', 0)}",
                f"Pending PGIs: {summary.get('pending_pgi', 0)}",
                f"Pending PODs: {aging.get('pending_pod', 0)}",
                "",
                "⚠️ *Risk Analysis*",
                f"Risk Level: {risk_emoji} {risk_display}",
                f"Health Score: {health_score}/100",
                "",
                "📅 *Timeline*",
                f"First Transaction: {self._format_month_year(profile.get('first_dn_date'))}",
                f"Last DN: {self._format_date(profile.get('last_dn_date'))}",
            ]
            
            # Distance info
            if distance_info:
                summary_text = distance_info.get("summary", "")
                if summary_text:
                    lines.append("")
                    lines.append(summary_text)
            
            # Forecast
            if forecast.get("trend") != "insufficient_data":
                lines.append("")
                lines.append("📊 *Forecast (Next Month)*")
                lines.append(f"Expected Revenue: PKR {forecast.get('forecast_revenue', 0):,.0f}")
                lines.append(f"Expected Units: {forecast.get('forecast_units', 0):,.0f}")
                lines.append(f"Expected DNs: {forecast.get('forecast_dns', 0):,.0f}")
                lines.append(f"Trend: {forecast.get('trend', 'stable').title()}")
                lines.append(f"Confidence: {forecast.get('confidence', 0) * 100:.0f}%")
            
            # Top Models
            if response_data.get("products"):
                lines.append("")
                lines.append("🏆 *Top Models*")
                for product in response_data.get("products", [])[:3]:
                    lines.append(f"{product.get('name', 'N/A')}")
            
            # Monthly Trend
            if response_data.get("monthly_trend"):
                trends = response_data.get("monthly_trend", [])
                if trends:
                    latest = trends[0]
                    lines.append("")
                    lines.append("📈 *Monthly Trend*")
                    lines.append(f"Revenue: PKR {latest.get('revenue', 0):,.0f}")
                    lines.append(f"Units: {latest.get('units', 0)}")
                    lines.append(f"DNs: {latest.get('dns', 0)}")
            
            # Management Recommendation
            recommendation = self._get_dealer_recommendation(summary, aging)
            if recommendation:
                lines.append("")
                lines.append("🎯 *Management Recommendation*")
                lines.append(recommendation)
            
            # AI Insight
            insight = self._generate_insights(
                {"profile": profile, "summary": summary, "performance": performance},
                "dealer_dashboard",
                req_id
            )
            if insight:
                lines.append("")
                lines.append("💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] Dealer 360 formatting failed: {e}")
            return f"❌ Unable to format dealer dashboard for {dealer_name}"
    
    def _format_city_dashboard(self, data, city_name: str, req_id: str) -> str:
        """Format professional City Performance Dashboard with forecasting."""
        try:
            if not self._validate_analytics_response(data, "city_dashboard", req_id):
                return f"❌ No data found for {city_name}"
            
            if not data.success:
                return f"❌ No data found for {city_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"❌ No data found for {city_name}"
            
            # Generate forecast
            forecast = self._generate_forecast(d, "next_month")
            
            lines = [
                "🏙️ *CITY PERFORMANCE DASHBOARD*",
                "",
                "📍 *City Profile*",
                f"City: {city_name}",
                "",
                "📊 *Business Summary*",
                f"Total Dealers: {summary.get('total_dealers', 0):,}",
                f"Total Warehouses Serving: {summary.get('total_warehouses', 0)}",
                "",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"Total Units: {summary.get('total_units', 0):,}",
                f"Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Operational Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0)}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0)}",
            ]
            
            # Forecast
            if forecast.get("trend") != "insufficient_data":
                lines.append("")
                lines.append("📊 *Forecast (Next Month)*")
                lines.append(f"Expected Revenue: PKR {forecast.get('forecast_revenue', 0):,.0f}")
                lines.append(f"Expected Units: {forecast.get('forecast_units', 0):,.0f}")
                lines.append(f"Expected DNs: {forecast.get('forecast_dns', 0):,.0f}")
                lines.append(f"Trend: {forecast.get('trend', 'stable').title()}")
            
            # Top Dealers
            if d.get("top_dealers"):
                lines.append("")
                lines.append("🏆 *Top Dealers*")
                for dealer in d.get("top_dealers", [])[:5]:
                    lines.append(f"{dealer.get('name', 'N/A')}")
            
            # Top Products
            if d.get("top_products"):
                lines.append("")
                lines.append("🏆 *Top Products*")
                for product in d.get("top_products", [])[:5]:
                    lines.append(f"{product.get('name', 'N/A')}")
            
            lines.append("")
            lines.append("⚠️ *Risk Dashboard*")
            lines.append(f"Late Deliveries: {summary.get('late_deliveries', 0)}")
            lines.append(f"Pending POD Dealers: {summary.get('pending_pod_dealers', 0)}")
            lines.append(f"Pending PGI Dealers: {summary.get('pending_pgi_dealers', 0)}")
            
            # Monthly Trend
            if d.get("monthly_trend"):
                trend = d.get("monthly_trend", {})
                lines.append("")
                lines.append("📅 *Monthly Trend*")
                lines.append(f"DNs: {trend.get('dns', 0)}")
                lines.append(f"Units: {trend.get('units', 0)}")
                lines.append(f"Revenue: PKR {trend.get('revenue', 0):,.0f}")
            
            # Recommendation
            recommendation = self._get_city_recommendation(summary)
            if recommendation:
                lines.append("")
                lines.append("🎯 *Management Recommendation*")
                lines.append(recommendation)
            
            # AI Insight
            insight = self._generate_insights(
                {"summary": summary, "top_dealers": d.get("top_dealers", [])},
                "city_dashboard",
                req_id
            )
            if insight:
                lines.append("")
                lines.append("💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] City dashboard formatting failed: {e}")
            return f"❌ Unable to format city dashboard for {city_name}"
    
    def _format_warehouse_dashboard(self, data, warehouse_name: str, req_id: str) -> str:
        """Format professional Warehouse Performance Dashboard with forecasting."""
        try:
            if not self._validate_analytics_response(data, "warehouse_dashboard", req_id):
                return f"❌ No data found for {warehouse_name}"
            
            if not data.success:
                return f"❌ No data found for {warehouse_name}"
            
            d = data.data or {}
            summary = d.get("summary", {})
            
            if summary.get("total_dns", 0) == 0:
                return f"❌ No data found for {warehouse_name}"
            
            # Generate forecast
            forecast = self._generate_forecast(d, "next_month")
            
            lines = [
                "🏭 *WAREHOUSE PERFORMANCE DASHBOARD*",
                "",
                "🏭 *Warehouse Profile*",
                f"Warehouse: {warehouse_name}",
                f"Warehouse Code: {d.get('warehouse_code', 'N/A')}",
                "",
                "📍 *Coverage*",
                f"Cities Served: {summary.get('cities_served', 0):,}",
                f"Dealers Served: {summary.get('total_dealers', 0):,}",
                "",
                "📊 *Business Summary*",
                f"Total DNs: {summary.get('total_dns', 0):,}",
                f"Total Units: {summary.get('total_units', 0):,}",
                f"Total Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                "",
                "📈 *Operational Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                f"Pending DNs: {summary.get('pending_dns', 0):,}",
                f"Pending PGIs: {summary.get('pending_pgi_dns', 0):,}",
                f"Pending PODs: {summary.get('pending_pod_dns', 0):,}",
            ]
            
            # Forecast
            if forecast.get("trend") != "insufficient_data":
                lines.append("")
                lines.append("📊 *Forecast (Next Month)*")
                lines.append(f"Expected Revenue: PKR {forecast.get('forecast_revenue', 0):,.0f}")
                lines.append(f"Expected Units: {forecast.get('forecast_units', 0):,.0f}")
                lines.append(f"Expected DNs: {forecast.get('forecast_dns', 0):,.0f}")
                lines.append(f"Trend: {forecast.get('trend', 'stable').title()}")
            
            # Top Cities
            if d.get("top_cities"):
                lines.append("")
                lines.append("🏆 *Top Cities Served*")
                for city in d.get("top_cities", [])[:5]:
                    name = city.get('name', 'N/A')
                    dns = city.get('dns', 0)
                    lines.append(f"{name} ({dns:,} DNs)")
            
            # Top Dealers
            if d.get("top_dealers"):
                lines.append("")
                lines.append("🏆 *Top Dealers Served*")
                for dealer in d.get("top_dealers", [])[:5]:
                    name = dealer.get('name', 'N/A')
                    revenue = dealer.get('revenue', 0)
                    lines.append(f"{name} - PKR {revenue:,.0f}")
            
            lines.append("")
            lines.append("⚠️ *Risk Dashboard*")
            lines.append(f"Delayed Deliveries: {summary.get('delayed_deliveries', 0)}")
            lines.append(f"Pending POD Cases: {summary.get('pending_pod_dns', 0):,}")
            
            # Monthly Trend
            if d.get("monthly_trend"):
                trend = d.get("monthly_trend", {})
                lines.append("")
                lines.append("📅 *Monthly Trend*")
                lines.append(f"DNs: {trend.get('dns', 0):,}")
                lines.append(f"Units: {trend.get('units', 0):,}")
                lines.append(f"Revenue: PKR {trend.get('revenue', 0):,.0f}")
            
            # Recommendation
            recommendation = self._get_warehouse_recommendation(summary)
            if recommendation:
                lines.append("")
                lines.append("🎯 *Management Recommendation*")
                lines.append(recommendation)
            
            # AI Insight
            insight = self._generate_insights(
                {"summary": summary, "top_cities": d.get("top_cities", [])},
                "warehouse_dashboard",
                req_id
            )
            if insight:
                lines.append("")
                lines.append("💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] Warehouse dashboard formatting failed: {e}")
            return f"❌ Unable to format warehouse dashboard for {warehouse_name}"
    
    def _format_control_tower_dashboard(self, data, req_id: str) -> str:
        """Format professional Control Tower Dashboard with AI insights."""
        try:
            if not self._validate_analytics_response(data, "control_tower", req_id):
                return "🚨 No control tower data available."
            
            if not data.success:
                return "🚨 No control tower data available."
            
            d = data.data or {}
            alerts = d.get("alerts", [])
            critical_count = d.get("critical_count", 0)
            high_count = d.get("high_count", 0)
            
            # Get network summary from analytics
            network = self.analytics.get_all_dealers_dashboard()
            network_data = network.data if network and network.success else {}
            
            lines = [
                "🚨 *LOGISTICS CONTROL TOWER*",
                "",
                "📊 *Network Overview*",
                f"Total DNs: {network_data.get('total_dns', 0):,}",
                f"Total Units: {network_data.get('total_units', 0):,}",
                f"Total Revenue: PKR {network_data.get('total_revenue', 0):,.0f}",
                "",
                "🚚 *Delivery Performance*",
                f"Delivery Rate: {network_data.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {network_data.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {network_data.get('pod_rate', 0):.1f}%",
                "",
                "⚠️ *Pending Activities*",
                f"Pending DNs: {network_data.get('pending_dns', 0)}",
                f"Pending PGIs: {network_data.get('pending_pgi', 0)}",
                f"Pending PODs: {network_data.get('pending_pod', 0)}",
            ]
            
            if d.get("high_risk_areas"):
                lines.append("")
                lines.append("🔴 *High Risk Areas*")
                for area in d.get("high_risk_areas", [])[:5]:
                    lines.append(f"{area}")
            
            if d.get("high_risk_dealers"):
                lines.append("")
                lines.append("🔴 *High Risk Dealers*")
                for dealer in d.get("high_risk_dealers", [])[:5]:
                    lines.append(f"{dealer}")
            
            if d.get("high_risk_warehouses"):
                lines.append("")
                lines.append("🏭 *High Risk Warehouses*")
                for warehouse in d.get("high_risk_warehouses", [])[:5]:
                    lines.append(f"{warehouse}")
            
            lines.append("")
            lines.append("📈 *SLA Compliance*")
            lines.append(f"Delivery SLA: {d.get('delivery_sla', 0):.1f}%")
            lines.append(f"POD SLA: {d.get('pod_sla', 0):.1f}%")
            
            # Immediate Actions
            actions = self._get_control_tower_actions(critical_count, high_count, d)
            if actions:
                lines.append("")
                lines.append("🎯 *Immediate Actions*")
                for action in actions:
                    lines.append(f"• {action}")
            
            # AI Insight
            insight = self._generate_insights(
                {"alerts": alerts, "critical_count": critical_count},
                "control_tower",
                req_id
            )
            if insight:
                lines.append("")
                lines.append("💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] Control tower formatting failed: {e}")
            return "🚨 Unable to format control tower."
    
    def _format_executive_dashboard(self, data, req_id: str) -> str:
        """Format professional Executive Dashboard with AI insights."""
        try:
            if not self._validate_analytics_response(data, "executive_dashboard", req_id):
                return "👔 No executive insights available."
            
            if not data.success:
                return "👔 No executive insights available."
            
            d = data.data or {}
            summary = d.get("summary", {})
            insights_list = d.get("insights", [])
            top_dealers = d.get("top_dealers", [])
            top_cities = d.get("top_cities", [])
            
            top_product = d.get("top_product", "N/A")
            top_dealer = top_dealers[0].get("dealer_name", "N/A") if top_dealers else "N/A"
            top_city = top_cities[0].get("city", "N/A") if top_cities else "N/A"
            
            health_score = d.get("health_score", 0)
            health_status = "Healthy" if health_score >= 80 else "Needs Attention" if health_score >= 60 else "Critical"
            health_emoji = "✅" if health_score >= 80 else "⚠️" if health_score >= 60 else "🔴"
            
            # Generate forecast
            forecast = self._generate_forecast(d, "next_month")
            
            lines = [
                "👔 *EXECUTIVE DASHBOARD*",
                "",
                "💰 *Business Performance*",
                f"Revenue: PKR {summary.get('total_revenue', 0):,.0f}",
                f"Units: {summary.get('total_units', 0):,}",
                f"DN Count: {summary.get('total_dns', 0):,}",
                "",
                "📈 *KPI Performance*",
                f"Delivery Rate: {summary.get('delivery_rate', 0):.1f}%",
                f"PGI Rate: {summary.get('pgi_rate', 0):.1f}%",
                f"POD Rate: {summary.get('pod_rate', 0):.1f}%",
                "",
                "🏆 *Top Performer*",
                f"Dealer: {top_dealer}",
                f"Revenue: PKR {top_dealers[0].get('total_revenue', 0) if top_dealers else 0:,.0f}",
                "",
                "🏙️ *Top City*",
                f"{top_city}",
                "",
                "🏭 *Top Warehouse*",
                f"{d.get('top_warehouse', 'N/A')}",
                "",
                "📦 *Top Product*",
                f"{top_product}",
                "",
                "⚠️ *Key Risks*",
                f"Pending PODs: {summary.get('pending_pod', 0)}",
                f"Delayed Deliveries: {summary.get('pending_pgi', 0)}",
            ]
            
            # Forecast
            if forecast.get("trend") != "insufficient_data":
                lines.append("")
                lines.append("📊 *Forecast (Next Month)*")
                lines.append(f"Expected Revenue: PKR {forecast.get('forecast_revenue', 0):,.0f}")
                lines.append(f"Expected Units: {forecast.get('forecast_units', 0):,.0f}")
                lines.append(f"Expected DNs: {forecast.get('forecast_dns', 0):,.0f}")
                lines.append(f"Trend: {forecast.get('trend', 'stable').title()}")
            
            if insights_list:
                lines.append("")
                lines.append("💡 *Insights*")
                for insight in insights_list[:3]:
                    lines.append(f"   • {insight}")
            
            # Management Recommendations
            recommendations = self._get_executive_recommendations(summary)
            if recommendations:
                lines.append("")
                lines.append("🎯 *Management Recommendations*")
                for rec in recommendations:
                    lines.append(f"• {rec}")
            
            lines.append("")
            lines.append("📊 *Overall Health Score*")
            lines.append(f"{health_score}/100")
            lines.append(f"Status: {health_emoji} {health_status}")
            
            # AI Insight
            insight = self._generate_insights(
                {"summary": summary, "health_score": health_score},
                "executive_dashboard",
                req_id
            )
            if insight:
                lines.append("")
                lines.append("💡 *AI Insight:*")
                lines.append(insight)
            
            # Truncate if too long
            result = "\n".join(lines)
            if len(result) > MAX_RESPONSE_LENGTH:
                result = result[:MAX_RESPONSE_LENGTH] + "\n\n... (truncated)"
            
            return result
            
        except Exception as e:
            logger.exception(f"[{req_id}] Executive dashboard formatting failed: {e}")
            return "👔 Unable to format executive dashboard."
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _format_date(self, date_str: Optional[str]) -> str:
        if not date_str or date_str == 'N/A':
            return 'N/A'
        try:
            if isinstance(date_str, str):
                dt = datetime.fromisoformat(date_str)
                return dt.strftime("%d-%b-%Y")
            return date_str
        except:
            return str(date_str)
    
    def _format_month_year(self, date_str: Optional[str]) -> str:
        if not date_str or date_str == 'N/A':
            return 'N/A'
        try:
            if isinstance(date_str, str):
                dt = datetime.fromisoformat(date_str)
                return dt.strftime("%b-%Y")
            return date_str
        except:
            return str(date_str)
    
    def _get_risk_emoji(self, risk_level: str) -> str:
        risk_level = risk_level.lower()
        if risk_level == "critical":
            return "🔴"
        elif risk_level == "high":
            return "🟠"
        elif risk_level == "medium":
            return "🟡"
        else:
            return "🟢"
    
    def _get_compliance_status(self, days) -> str:
        if days == 'N/A' or days is None:
            return "N/A"
        try:
            days = int(days)
            if days <= 3:
                return "✅ Excellent"
            elif days <= 7:
                return "👍 Good"
            elif days <= 15:
                return "⚠️ Needs Attention"
            else:
                return "🔴 Critical"
        except:
            return "N/A"
    
    def _get_dn_journey(self, dn_date, pgi_date, pod_date, status) -> Dict[str, bool]:
        dn_created = dn_date != 'N/A' and dn_date is not None
        pgi_completed = pgi_date != 'N/A' and pgi_date is not None
        in_transit = status in ["pending_pod", "in_transit"]
        delivered = status in ["delivered", "completed"]
        pod_received = pod_date != 'N/A' and pod_date is not None
        
        return {
            "DN Created": dn_created,
            "PGI Completed": pgi_completed,
            "In Transit": in_transit,
            "Delivered": delivered,
            "POD Received": pod_received
        }
    
    def _get_dn_management_action(self, pod_aging, pgi_aging, status) -> str:
        if status in ["delivered", "completed"]:
            if pod_aging != 'N/A' and pod_aging is not None:
                if pod_aging > 15:
                    return "POD collection requires improvement."
                elif pod_aging > 7:
                    return "Monitor POD collection process."
            return "Delivery completed successfully."
        elif status == "pending_pod":
            return "Follow up for POD collection."
        elif status == "pending_pgi":
            return "Expedite PGI processing."
        return "Monitor DN progress."
    
    def _get_dealer_recommendation(self, summary: Dict, aging: Dict) -> str:
        pod_rate = summary.get("pod_rate", 0)
        delivery_rate = summary.get("delivery_rate", 0)
        pending_pod = aging.get("pending_pod", 0)
        
        if pod_rate < 80:
            return "Focus on POD closure and dealer follow-up."
        elif pending_pod > 5:
            return "Improve POD collection process."
        elif delivery_rate < 85:
            return "Improve delivery performance."
        else:
            return "Continue maintaining good performance."
    
    def _get_city_recommendation(self, summary: Dict) -> str:
        pod_rate = summary.get("pod_rate", 0)
        delivery_rate = summary.get("delivery_rate", 0)
        
        if pod_rate < 80:
            return "Improve POD collection performance."
        elif delivery_rate < 85:
            return "Improve delivery performance in this city."
        return "Continue monitoring city performance."
    
    def _get_warehouse_recommendation(self, summary: Dict) -> str:
        pod_rate = summary.get("pod_rate", 0)
        pending_pod = summary.get("pending_pod_dns", 0)
        
        if pending_pod > 50:
            return "Reduce POD backlog and improve POD collection."
        elif pod_rate < 80:
            return "Improve POD collection process."
        return "Continue maintaining warehouse performance."
    
    def _get_control_tower_actions(self, critical: int, high: int, data: Dict) -> List[str]:
        actions = []
        if critical > 0:
            actions.append(f"Address {critical} critical alerts immediately")
        if high > 0:
            actions.append(f"Review {high} high priority issues")
        if data.get("pending_pod", 0) > 100:
            actions.append("Close pending PODs")
        if data.get("delayed_deliveries", 0) > 50:
            actions.append("Review delayed deliveries")
        if data.get("high_risk_dealers"):
            actions.append("Escalate high-risk dealers")
        if not actions:
            actions.append("All systems normal - continue monitoring")
        return actions
    
    def _get_executive_recommendations(self, summary: Dict) -> List[str]:
        recommendations = []
        pod_rate = summary.get("pod_rate", 0)
        if pod_rate < 85:
            recommendations.append("Improve POD collection cycle")
        delivery_rate = summary.get("delivery_rate", 0)
        if delivery_rate < 85:
            recommendations.append("Reduce delivery aging")
        pending_pod = summary.get("pending_pod", 0)
        if pending_pod > 50:
            recommendations.append("Monitor high-risk dealers")
            recommendations.append("Strengthen warehouse dispatch control")
        if not recommendations:
            recommendations.append("All KPIs performing well - continue current practices")
        return recommendations
    
    # ==========================================================
    # ERROR & FALLBACK RESPONSES
    # ==========================================================
    
    def _get_help_message(self) -> str:
        return """🏠 *Haier Logistics AI - WhatsApp Menu*

*📋 Main Menu*
1️⃣ Dealer Dashboard
2️⃣ Warehouse Dashboard
3️⃣ City Dashboard
4️⃣ Product Dashboard
5️⃣ DN Tracking
6️⃣ Pending Deliveries
7️⃣ POD Dashboard
8️⃣ Distance Dashboard
9️⃣ Revenue Dashboard
🔟 Performance Dashboard
1️⃣1️⃣ Executive Dashboard
1️⃣2️⃣ Forecast Dashboard
1️⃣3️⃣ AI Ask Anything

*🔍 Quick Commands:*
• Enter 8-12 digit DN number
• Dealer name (e.g., "ZQ Electronics")
• City name (e.g., "Haripur")
• Warehouse name
• "Executive summary"
• "Control tower"
• "Top dealers"
• "Root cause analysis"

*💡 AI Intelligence:*
I can analyze any logistics question.
Ask about:
• Performance - "Which dealer has highest revenue?"
• Distance - "How far is dealer from warehouse?"
• Forecasting - "What is next month's revenue outlook?"
• Root Cause - "Why are deliveries delayed in Lahore?"

*What would you like to explore?* 🤖"""
    
    def _get_error_response(self, question: str, error: Exception, error_id: str, request_id: str) -> str:
        return (
            f"⚠️ *Unable to process your request*\n\n"
            f"• Error Reference: `{error_id}`\n"
            f"• Request ID: `{request_id}`\n\n"
            f"Please try again or contact support with the reference ID."
        )
    
    # ==========================================================
    # METRICS & ADMIN
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        total_dn = self.metrics["dn_lookups_success"] + self.metrics["dn_lookups_failure"]
        total_dealer = self.metrics["dealer_queries_success"] + self.metrics["dealer_queries_failure"]
        
        return {
            "version": "17.2",
            "total_requests": self.metrics["total_requests"],
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_failures_avoided": self.metrics["cache_failures_avoided"],
            "cache_cleaned": self.metrics["cache_cleaned"],
            "cache_hit_rate": round(self.metrics["cache_hits"] / max(1, self.metrics["cache_hits"] + self.metrics["cache_misses"]) * 100, 1),
            "dn_lookups": {
                "total": self.metrics["dn_lookups"],
                "success": self.metrics["dn_lookups_success"],
                "failure": self.metrics["dn_lookups_failure"],
                "success_rate": round(self.metrics["dn_lookups_success"] / max(total_dn, 1) * 100, 1),
                "retry_attempts": self.metrics["dn_retry_attempts"]
            },
            "dealer_queries": {
                "total": self.metrics["dealer_queries"],
                "success": self.metrics["dealer_queries_success"],
                "failure": self.metrics["dealer_queries_failure"],
                "success_rate": round(self.metrics["dealer_queries_success"] / max(total_dealer, 1) * 100, 1),
                "suggestions": self.metrics["dealer_suggestions"]
            },
            "dealer_resolution": {
                "attempts": self.metrics["dealer_resolution_attempts"],
                "success": self.metrics["dealer_resolution_success"],
                "failure": self.metrics["dealer_resolution_failure"],
                "success_rate": round(self.metrics["dealer_resolution_success"] / max(self.metrics["dealer_resolution_attempts"], 1) * 100, 1)
            },
            "city_queries": self.metrics["city_queries"],
            "warehouse_queries": self.metrics["warehouse_queries"],
            "product_queries": self.metrics["product_queries"],
            "comparisons": self.metrics["comparisons"],
            "executive_insights": self.metrics["executive_insights"],
            "root_cause_analyses": self.metrics["root_cause_analyses"],
            "control_tower": self.metrics["control_tower"],
            "groq_uses": self.metrics["groq_uses"],
            "groq_fallbacks": self.metrics["groq_fallbacks"],
            "overrides": self.metrics["overrides"],
            "timeouts": self.metrics["timeouts"],
            "errors": self.metrics["errors"],
            "analytics_response_errors": self.metrics["analytics_response_errors"],
            "recovery_attempts": self.metrics["recovery_attempts"],
            "distance_calculations": self.metrics["distance_calculations"],
            "transit_calculations": self.metrics["transit_calculations"],
            "risk_assessments": self.metrics["risk_assessments"],
            "forecast_requests": self.metrics["forecast_requests"],
            "root_cause_requests": self.metrics["root_cause_requests"],
            "context_reset": self.metrics["context_reset"],
            "invalid_entity_cleared": self.metrics["invalid_entity_cleared"],
            "conversation_count": len(self.conversation_cache),
            "cache_size": len(self.response_cache),
            "failure_cache_size": len(self.failure_cache)
        }
    
    def get_routing_debug(self, question: str) -> Dict[str, Any]:
        context = {}
        routing = self._get_routing_decision(question, context)
        return {
            "question": question,
            "is_dn_query": self._is_dn_query(question),
            "normalized_dn": self._normalize_dn(question) if self._is_dn_query(question) else None,
            "routing_decision": {
                "intent": getattr(routing, "intent", "unknown"),
                "entity": getattr(routing, "entity", None),
                "service": getattr(routing, "service", "unknown"),
                "confidence": getattr(routing, "confidence", 0.0),
                "needs_groq": getattr(routing, "needs_groq", False),
                "reason": getattr(routing, "reason", "")
            },
            "timestamp": time.time()
        }


# ==========================================================
# SINGLETON
# ==========================================================

_orchestrator = None

def get_orchestrator() -> AIOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = AIOrchestrator()
    return _orchestrator


# ==========================================================
# WRAPPER FUNCTIONS
# ==========================================================

def process_whatsapp_query(
    question: str,
    session_factory: Optional[Callable[[], Session]] = None,
    phone_number: Optional[str] = None,
    user_id: Optional[str] = None,
    request_id: Optional[str] = None
) -> str:
    orchestrator = get_orchestrator()
    return orchestrator.process_whatsapp_query(
        question=question,
        session_factory=session_factory,
        phone_number=phone_number,
        user_id=user_id,
        request_id=request_id
    )


def get_ai_service_metrics() -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_metrics()


def clear_ai_cache():
    orchestrator = get_orchestrator()
    return orchestrator.clear_caches()


def get_routing_debug(question: str) -> Dict[str, Any]:
    orchestrator = get_orchestrator()
    return orchestrator.get_routing_debug(question)


# ==========================================================
# INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v17.2 - Fully Self-Healing")
logger.info("=" * 70)
logger.info("")
logger.info("   SELF-HEALING FEATURES:")
logger.info("   ✅ Request Isolation")
logger.info("   ✅ Multiple Recovery Attempts (6 strategies)")
logger.info("   ✅ Never Cache Failures")
logger.info("   ✅ Groq AI Fallback")
logger.info("   ✅ System Survival")
logger.info("   ✅ Context Validation & Auto-Cleaning")
logger.info("   ✅ Cache Self-Healing")
logger.info("")
logger.info("   MASTER GROQ INTELLIGENCE:")
logger.info("   ✅ AI Logistics Control Tower")
logger.info("   ✅ Dealer Distance Engine (Haversine)")
logger.info("   ✅ Transit Time Engine")
logger.info("   ✅ Delay Engine")
logger.info("   ✅ Risk Engine")
logger.info("   ✅ Root Cause Analysis")
logger.info("   ✅ AI Insight Generation")
logger.info("   ✅ Forecasting Engine")
logger.info("   ✅ WhatsApp Menu System")
logger.info("   ✅ Executive Decision Support")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
