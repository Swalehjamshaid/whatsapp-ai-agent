"""
File: whatsapp-ai-agent-demo/app/services/dealer_analytics_service.py
Enterprise Dealer Intelligence Engine - Answers 50+ dealer questions naturally through WhatsApp.
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Dict, List, Tuple, Union
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum

from cachetools import TTLCache
from rapidfuzz import fuzz, process
from sqlalchemy import and_, case, distinct, func, or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

try:
    import openrouteservice
    from openrouteservice import Client
except ImportError:
    openrouteservice = None
    Client = None

try:
    from geopy.distance import great_circle, geodesic
except ImportError:
    great_circle = None
    geodesic = None


logger = logging.getLogger(__name__)
ORS_API_KEY = os.getenv("OPENROUTESERVICE_API_KEY") or os.getenv("ORS_API_KEY")
CACHE_TTL = max(300, int(os.getenv("DEALER_ANALYTICS_CACHE_TTL", "21600")))

# Pre-compile regex patterns for maximum performance
_STOP_PHRASES_PATTERN = re.compile(
    r'\b(?:tell me about|dealer dashboard|dealer profile|dealer performance|'
    r'dealer statistics|dealer revenue|dealer distance|dealer pending|'
    r'dealer status|dealer pod|dealer pgi|show|display|dealer|'
    r'profile|statistics|performance|status|revenue|distance|'
    r'pending|dashboard|about|of|the|company|private|'
    r'limited|pvt|ltd)\b'
)
_WHITESPACE_PATTERN = re.compile(r'\s+')
_SPECIAL_CHARS_PATTERN = re.compile(r'[^a-z0-9\s]')

# Thread pool for parallel operations
_executor = ThreadPoolExecutor(max_workers=4)


def _text(value: Any, default: str = "Unknown") -> str:
    if value is None:
        return default
    try:
        result = str(value).strip()
        return result if result else default
    except (TypeError, ValueError):
        return default


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _percent(numerator: Any, denominator: Any) -> float:
    bottom = _number(denominator)
    return round((_number(numerator) * 100.0 / bottom), 2) if bottom else 0.0


def _date_text(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    return _text(value, "N/A")


def _status_complete(column: Any) -> Any:
    return func.lower(func.coalesce(column, "")).in_(("completed", "complete", "delivered", "done", "yes"))


# ============================================================
# INTENT AND METRIC ENUMS
# ============================================================

class DealerIntent(Enum):
    """Dealer question intents."""
    DEALER_INFO = "dealer_info"
    REVENUE = "revenue"
    DN = "dn"
    UNITS = "units"
    DELIVERY = "delivery"
    PGI = "pgi"
    POD = "pod"
    PENDING = "pending"
    PRODUCT = "product"
    WAREHOUSE = "warehouse"
    RANKING = "ranking"
    HEALTH = "health"
    DASHBOARD = "dashboard"
    PROFILE = "profile"
    COMPARISON = "comparison"
    UNKNOWN = "unknown"


class MetricType(Enum):
    """Metric types for question answering."""
    TOTAL_REVENUE = "total_revenue"
    REVENUE_GROWTH = "revenue_growth"
    MONTHLY_REVENUE = "monthly_revenue"
    PENDING_REVENUE = "pending_revenue"
    DELIVERED_REVENUE = "delivered_revenue"
    AVG_REVENUE_PER_DN = "avg_revenue_per_dn"
    AVG_REVENUE_PER_UNIT = "avg_revenue_per_unit"
    
    TOTAL_DN = "total_dn"
    COMPLETED_DN = "completed_dn"
    PENDING_DN = "pending_dn"
    AVG_DN_VALUE = "avg_dn_value"
    HIGHEST_DN = "highest_dn"
    LOWEST_DN = "lowest_dn"
    NEWEST_DN = "newest_dn"
    OLDEST_PENDING_DN = "oldest_pending_dn"
    
    TOTAL_UNITS = "total_units"
    DELIVERED_UNITS = "delivered_units"
    PENDING_UNITS = "pending_units"
    AVG_UNITS_PER_DN = "avg_units_per_dn"
    
    DELIVERY_SUCCESS = "delivery_success"
    AVG_DELIVERY_DAYS = "avg_delivery_days"
    FASTEST_DELIVERY = "fastest_delivery"
    SLOWEST_DELIVERY = "slowest_delivery"
    SAME_DAY_DELIVERY = "same_day_delivery"
    NEXT_DAY_DELIVERY = "next_day_delivery"
    
    PGI_SUCCESS = "pgi_success"
    PENDING_PGI = "pending_pgi"
    AVG_PGI_DAYS = "avg_pgi_days"
    LATEST_PGI = "latest_pgi"
    
    POD_SUCCESS = "pod_success"
    PENDING_POD = "pending_pod"
    AVG_POD_DAYS = "avg_pod_days"
    LATEST_POD = "latest_pod"
    
    PENDING_PCT = "pending_pct"
    CRITICAL_PENDING = "critical_pending"
    OVERDUE_PENDING = "overdue_pending"
    PENDING_AGE = "pending_age"
    
    TOP_PRODUCT = "top_product"
    TOP_MODEL = "top_model"
    TOP_MATERIAL = "top_material"
    STRONGEST_CATEGORY = "strongest_category"
    WEAKEST_CATEGORY = "weakest_category"
    TOP_DIVISION = "top_division"
    
    WAREHOUSE = "warehouse"
    WAREHOUSE_CODE = "warehouse_code"
    WAREHOUSE_UTILIZATION = "warehouse_utilization"
    DISTANCE = "distance"
    DRIVING_TIME = "driving_time"
    ESTIMATED_DELIVERY = "estimated_delivery"
    
    DEALER_NAME = "dealer_name"
    DEALER_CODE = "dealer_code"
    CUSTOMER_CODE = "customer_code"
    CITY = "city"
    SALES_OFFICE = "sales_office"
    SALES_MANAGER = "sales_manager"
    DIVISION = "division"
    DELIVERY_LOCATION = "delivery_location"
    
    NATIONAL_RANK = "national_rank"
    REGIONAL_RANK = "regional_rank"
    REVENUE_RANK = "revenue_rank"
    DELIVERY_RANK = "delivery_rank"
    DN_RANK = "dn_rank"
    UNIT_RANK = "unit_rank"
    PENDING_RANK = "pending_rank"
    POD_RANK = "pod_rank"
    
    BUSINESS_SCORE = "business_score"
    OVERALL_STATUS = "overall_status"
    EXECUTIVE_SUMMARY = "executive_summary"


# ============================================================
# INTENT AND METRIC RESOLVERS
# ============================================================

class IntentResolver:
    """Resolves user questions to dealer intents."""
    
    INTENT_MAP = {
        # Dealer Information
        r'\b(?:tell me about|about|who is|what is)\s+(\w+)': DealerIntent.DEALER_INFO,
        r'\bdealer (?:info|information|details|summary|overview)\b': DealerIntent.DEALER_INFO,
        r'\b(?:sales manager|manager)\b': DealerIntent.DEALER_INFO,
        r'\b(?:sales office|office|region)\b': DealerIntent.DEALER_INFO,
        r'\b(?:dealer code|customer code)\b': DealerIntent.DEALER_INFO,
        r'\b(?:city|location)\b': DealerIntent.DEALER_INFO,
        
        # Revenue
        r'\brevenue\b': DealerIntent.REVENUE,
        r'\bsales\b': DealerIntent.REVENUE,
        r'\bincome\b': DealerIntent.REVENUE,
        r'\bturnover\b': DealerIntent.REVENUE,
        r'\brevenue (?:growth|trend|change)\b': DealerIntent.REVENUE,
        r'\b(?:this|current) month revenue\b': DealerIntent.REVENUE,
        r'\blast month revenue\b': DealerIntent.REVENUE,
        r'\bpending revenue\b': DealerIntent.REVENUE,
        r'\bdelivered revenue\b': DealerIntent.REVENUE,
        r'\baverage revenue\b': DealerIntent.REVENUE,
        
        # DN
        r'\bdn\b': DealerIntent.DN,
        r'\bdelivery note\b': DealerIntent.DN,
        r'\b(?:total|completed|pending) dns?\b': DealerIntent.DN,
        r'\bnewest dn\b': DealerIntent.DN,
        r'\boldest pending dn\b': DealerIntent.DN,
        r'\bhighest (?:value|revenue) dn\b': DealerIntent.DN,
        r'\blower?st (?:value|revenue) dn\b': DealerIntent.DN,
        
        # Units
        r'\bunits?\b': DealerIntent.UNITS,
        r'\bquantity\b': DealerIntent.UNITS,
        r'\b(?:total|delivered|pending) units?\b': DealerIntent.UNITS,
        r'\bunit (?:growth|trend)\b': DealerIntent.UNITS,
        r'\baverage units?\b': DealerIntent.UNITS,
        
        # Delivery
        r'\bdelivery\b': DealerIntent.DELIVERY,
        r'\bdelivery (?:success|performance|trend)\b': DealerIntent.DELIVERY,
        r'\b(?:average|fastest|slowest) delivery\b': DealerIntent.DELIVERY,
        r'\bsame day delivery\b': DealerIntent.DELIVERY,
        r'\bnext day delivery\b': DealerIntent.DELIVERY,
        r'\bdriving time\b': DealerIntent.DELIVERY,
        r'\bestimated delivery\b': DealerIntent.DELIVERY,
        r'\bdelivery days?\b': DealerIntent.DELIVERY,
        
        # PGI
        r'\bpgi\b': DealerIntent.PGI,
        r'\bgood issue\b': DealerIntent.PGI,
        r'\bpending pgi\b': DealerIntent.PGI,
        r'\b(?:average|latest) pgi\b': DealerIntent.PGI,
        
        # POD
        r'\bpod\b': DealerIntent.POD,
        r'\bproof of delivery\b': DealerIntent.POD,
        r'\bpending pod\b': DealerIntent.POD,
        r'\b(?:average|latest) pod\b': DealerIntent.POD,
        
        # Pending
        r'\bpending\b': DealerIntent.PENDING,
        r'\boutstanding\b': DealerIntent.PENDING,
        r'\bwaiting\b': DealerIntent.PENDING,
        r'\bincomplete\b': DealerIntent.PENDING,
        r'\bpending (?:dashboard|analytics|status)\b': DealerIntent.PENDING,
        r'\b(?:critical|overdue) pending\b': DealerIntent.PENDING,
        
        # Products
        r'\bproduct\b': DealerIntent.PRODUCT,
        r'\bmodel\b': DealerIntent.PRODUCT,
        r'\bmaterial\b': DealerIntent.PRODUCT,
        r'\bcategory\b': DealerIntent.PRODUCT,
        r'\bdivision\b': DealerIntent.PRODUCT,
        r'\btop (?:product|model|material)\b': DealerIntent.PRODUCT,
        r'\bstrongest category\b': DealerIntent.PRODUCT,
        r'\bweakest category\b': DealerIntent.PRODUCT,
        
        # Warehouse
        r'\bwarehouse\b': DealerIntent.WAREHOUSE,
        r'\bdepot\b': DealerIntent.WAREHOUSE,
        r'\bdistribution center\b': DealerIntent.WAREHOUSE,
        r'\bwarehouse (?:code|utilization|performance)\b': DealerIntent.WAREHOUSE,
        r'\bdistance\b': DealerIntent.WAREHOUSE,
        
        # Ranking
        r'\brank\b': DealerIntent.RANKING,
        r'\branking\b': DealerIntent.RANKING,
        r'\b(?:national|regional) rank\b': DealerIntent.RANKING,
        r'\b(?:revenue|delivery|dn|unit|pending|pod) rank\b': DealerIntent.RANKING,
        r'\btop dealers?\b': DealerIntent.RANKING,
        r'\bbottom dealers?\b': DealerIntent.RANKING,
        
        # Health
        r'\b(?:business|health|score)\b': DealerIntent.HEALTH,
        r'\b(?:overall|performance) status\b': DealerIntent.HEALTH,
        r'\b(?:executive|management) summary\b': DealerIntent.HEALTH,
        r'\brecommendations?\b': DealerIntent.HEALTH,
        r'\b(?:strengths|weaknesses)\b': DealerIntent.HEALTH,
        r'\binsights?\b': DealerIntent.HEALTH,
        
        # Dashboard/Profile
        r'\bdashboard\b': DealerIntent.DASHBOARD,
        r'\bprofile\b': DealerIntent.PROFILE,
        r'\bsummary\b': DealerIntent.PROFILE,
        r'\boverview\b': DealerIntent.PROFILE,
        r'\bperformance\b': DealerIntent.PROFILE,
        
        # Comparison
        r'\bcompare\b': DealerIntent.COMPARISON,
        r'\bvs\b': DealerIntent.COMPARISON,
        r'\bversus\b': DealerIntent.COMPARISON,
    }
    
    METRIC_MAP = {
        # Revenue
        r'\btotal revenue\b': MetricType.TOTAL_REVENUE,
        r'\brevenue (?:growth|trend|change)\b': MetricType.REVENUE_GROWTH,
        r'\b(?:this|current) month revenue\b': MetricType.MONTHLY_REVENUE,
        r'\blast month revenue\b': MetricType.MONTHLY_REVENUE,
        r'\bpending revenue\b': MetricType.PENDING_REVENUE,
        r'\bdelivered revenue\b': MetricType.DELIVERED_REVENUE,
        r'\baverage revenue per dn\b': MetricType.AVG_REVENUE_PER_DN,
        r'\baverage revenue per unit\b': MetricType.AVG_REVENUE_PER_UNIT,
        
        # DN
        r'\btotal dns?\b': MetricType.TOTAL_DN,
        r'\bcompleted dns?\b': MetricType.COMPLETED_DN,
        r'\bpending dns?\b': MetricType.PENDING_DN,
        r'\baverage dn value\b': MetricType.AVG_DN_VALUE,
        r'\bhighest (?:value|revenue) dn\b': MetricType.HIGHEST_DN,
        r'\blower?st (?:value|revenue) dn\b': MetricType.LOWEST_DN,
        r'\bnewest dn\b': MetricType.NEWEST_DN,
        r'\boldest pending dn\b': MetricType.OLDEST_PENDING_DN,
        
        # Units
        r'\btotal units?\b': MetricType.TOTAL_UNITS,
        r'\bdelivered units?\b': MetricType.DELIVERED_UNITS,
        r'\bpending units?\b': MetricType.PENDING_UNITS,
        r'\baverage units per dn\b': MetricType.AVG_UNITS_PER_DN,
        
        # Delivery
        r'\bdelivery success\b': MetricType.DELIVERY_SUCCESS,
        r'\baverage delivery days?\b': MetricType.AVG_DELIVERY_DAYS,
        r'\bfastest delivery\b': MetricType.FASTEST_DELIVERY,
        r'\bslowest delivery\b': MetricType.SLOWEST_DELIVERY,
        r'\bsame day delivery\b': MetricType.SAME_DAY_DELIVERY,
        r'\bnext day delivery\b': MetricType.NEXT_DAY_DELIVERY,
        
        # PGI
        r'\bpgi success\b': MetricType.PGI_SUCCESS,
        r'\bpending pgi\b': MetricType.PENDING_PGI,
        r'\baverage pgi days?\b': MetricType.AVG_PGI_DAYS,
        r'\blatest pgi\b': MetricType.LATEST_PGI,
        
        # POD
        r'\bpod success\b': MetricType.POD_SUCCESS,
        r'\bpending pod\b': MetricType.PENDING_POD,
        r'\baverage pod days?\b': MetricType.AVG_POD_DAYS,
        r'\blatest pod\b': MetricType.LATEST_POD,
        
        # Pending
        r'\bpending percentage\b': MetricType.PENDING_PCT,
        r'\bcritical pending\b': MetricType.CRITICAL_PENDING,
        r'\boverdue pending\b': MetricType.OVERDUE_PENDING,
        r'\bpending age\b': MetricType.PENDING_AGE,
        
        # Products
        r'\btop product\b': MetricType.TOP_PRODUCT,
        r'\btop model\b': MetricType.TOP_MODEL,
        r'\btop material\b': MetricType.TOP_MATERIAL,
        r'\bstrongest category\b': MetricType.STRONGEST_CATEGORY,
        r'\bweakest category\b': MetricType.WEAKEST_CATEGORY,
        r'\btop division\b': MetricType.TOP_DIVISION,
        
        # Warehouse
        r'\bwarehouse\b': MetricType.WAREHOUSE,
        r'\bwarehouse code\b': MetricType.WAREHOUSE_CODE,
        r'\bwarehouse utilization\b': MetricType.WAREHOUSE_UTILIZATION,
        r'\bdistance\b': MetricType.DISTANCE,
        r'\bdriving time\b': MetricType.DRIVING_TIME,
        r'\bestimated delivery\b': MetricType.ESTIMATED_DELIVERY,
        
        # Dealer Info
        r'\bdealer name\b': MetricType.DEALER_NAME,
        r'\bdealer code\b': MetricType.DEALER_CODE,
        r'\bcustomer code\b': MetricType.CUSTOMER_CODE,
        r'\bcity\b': MetricType.CITY,
        r'\bsales office\b': MetricType.SALES_OFFICE,
        r'\bsales manager\b': MetricType.SALES_MANAGER,
        r'\bdivision\b': MetricType.DIVISION,
        r'\bdelivery location\b': MetricType.DELIVERY_LOCATION,
        
        # Ranking
        r'\bnational rank\b': MetricType.NATIONAL_RANK,
        r'\bregional rank\b': MetricType.REGIONAL_RANK,
        r'\brevenue rank\b': MetricType.REVENUE_RANK,
        r'\bdelivery rank\b': MetricType.DELIVERY_RANK,
        r'\bdn rank\b': MetricType.DN_RANK,
        r'\bunit rank\b': MetricType.UNIT_RANK,
        r'\bpending rank\b': MetricType.PENDING_RANK,
        r'\bpod rank\b': MetricType.POD_RANK,
        
        # Health
        r'\bbusiness score\b': MetricType.BUSINESS_SCORE,
        r'\boverall status\b': MetricType.OVERALL_STATUS,
        r'\bexecutive summary\b': MetricType.EXECUTIVE_SUMMARY,
    }
    
    @classmethod
    def resolve_intent(cls, question: str) -> DealerIntent:
        """Resolve the intent from a question."""
        question_lower = question.lower()
        
        # Check for dashboard/profile first (full response)
        if any(word in question_lower for word in ['dashboard', 'profile', 'summary', 'overview', 'performance']):
            if 'dashboard' in question_lower:
                return DealerIntent.DASHBOARD
            if 'profile' in question_lower or 'summary' in question_lower:
                return DealerIntent.PROFILE
        
        # Check for comparison
        if 'compare' in question_lower or 'vs' in question_lower or 'versus' in question_lower:
            return DealerIntent.COMPARISON
        
        # Check intent patterns
        for pattern, intent in cls.INTENT_MAP.items():
            if re.search(pattern, question_lower):
                return intent
        
        return DealerIntent.UNKNOWN
    
    @classmethod
    def resolve_metric(cls, question: str) -> Optional[MetricType]:
        """Resolve the specific metric from a question."""
        question_lower = question.lower()
        
        # Check metric patterns
        for pattern, metric in cls.METRIC_MAP.items():
            if re.search(pattern, question_lower):
                return metric
        
        return None


# ============================================================
# DEALER QUESTION ENGINE
# ============================================================

@dataclass
class DealerQuestion:
    """Represents a parsed dealer question."""
    dealer: str
    intent: DealerIntent
    metric: Optional[MetricType]
    original_question: str
    is_full_dashboard: bool = False
    is_comparison: bool = False


@dataclass
class DealerAnswer:
    """Represents an answer to a dealer question."""
    question: DealerQuestion
    answer: str
    whatsapp_message: str
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0


class DealerQuestionEngine:
    """Intelligent dealer question answering engine."""
    
    def __init__(self, service: 'DealerAnalyticsService'):
        self.service = service
        self.intent_resolver = IntentResolver()
    
    def answer_question(self, question: str, dealer_name: str = "", **kwargs) -> Dict[str, Any]:
        """
        Answer a dealer question naturally.
        
        Examples:
            "What is dealer revenue?"
            "Show pending DNs for Taj Electronics"
            "Tell me about Mian Group Chakwal"
            "Dealer dashboard for Taj Electronics"
        """
        start_time = time.perf_counter()
        
        # Parse the question
        parsed = self._parse_question(question, dealer_name)
        
        # Get dealer data
        dashboard_result = self.service.get_dealer_dashboard(
            parsed.dealer or dealer_name or kwargs.get("dealer", ""),
            **kwargs
        )
        
        if not dashboard_result.get("success"):
            return dashboard_result
        
        dealer_data = dashboard_result.get("data")
        if not dealer_data:
            return {"success": False, "error_code": "DEALER_NOT_FOUND", "message": "Dealer not found."}
        
        # Generate answer based on intent
        answer_data = self._generate_answer(parsed, dealer_data, dashboard_result)
        
        # Prepare response
        response = {
            "success": True,
            "dealer": parsed.dealer,
            "intent": parsed.intent.value if parsed.intent else "unknown",
            "metric": parsed.metric.value if parsed.metric else None,
            "answer": answer_data.get("answer", ""),
            "whatsapp_message": answer_data.get("whatsapp_message", ""),
            "data": answer_data.get("data", dealer_data),
            "dashboard": dealer_data,
            "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
        }
        
        logger.info(
            f"Question answered: dealer={parsed.dealer}, intent={parsed.intent.value if parsed.intent else 'unknown'}, "
            f"metric={parsed.metric.value if parsed.metric else 'none'}, time={response['execution_time_ms']}ms"
        )
        
        return response
    
    def _parse_question(self, question: str, dealer_name: str = "") -> DealerQuestion:
        """Parse the question to extract dealer, intent, and metric."""
        question_lower = question.lower()
        
        # Extract dealer name from question if not provided
        if not dealer_name:
            # Try to extract dealer name from question
            words = question_lower.split()
            for i, word in enumerate(words):
                # Look for patterns like "for {dealer}" or "about {dealer}"
                if word in ['for', 'about', 'of', 'on'] and i + 1 < len(words):
                    potential_dealer = ' '.join(words[i+1:])
                    if len(potential_dealer) > 2:
                        dealer_name = potential_dealer
                        break
            
            # If no dealer found, try the whole question
            if not dealer_name:
                dealer_name = question_lower
        
        # Resolve intent
        intent = IntentResolver.resolve_intent(question)
        
        # Resolve metric
        metric = IntentResolver.resolve_metric(question) if intent != DealerIntent.UNKNOWN else None
        
        # Check if full dashboard requested
        is_full_dashboard = intent in [DealerIntent.DASHBOARD, DealerIntent.PROFILE] or \
                           any(word in question_lower for word in ['dashboard', 'profile', 'summary', 'overview'])
        
        # Check if comparison
        is_comparison = intent == DealerIntent.COMPARISON
        
        return DealerQuestion(
            dealer=dealer_name,
            intent=intent,
            metric=metric,
            original_question=question,
            is_full_dashboard=is_full_dashboard,
            is_comparison=is_comparison
        )
    
    def _generate_answer(self, parsed: DealerQuestion, dealer: DealerDashboard, dashboard_result: Dict) -> Dict:
        """Generate an answer based on intent and metric."""
        
        # Full dashboard
        if parsed.is_full_dashboard:
            return self._format_dashboard_response(dealer, dashboard_result)
        
        # Specific intent responses
        intent_handlers = {
            DealerIntent.DEALER_INFO: self._answer_dealer_info,
            DealerIntent.REVENUE: self._answer_revenue,
            DealerIntent.DN: self._answer_dn,
            DealerIntent.UNITS: self._answer_units,
            DealerIntent.DELIVERY: self._answer_delivery,
            DealerIntent.PGI: self._answer_pgi,
            DealerIntent.POD: self._answer_pod,
            DealerIntent.PENDING: self._answer_pending,
            DealerIntent.PRODUCT: self._answer_product,
            DealerIntent.WAREHOUSE: self._answer_warehouse,
            DealerIntent.RANKING: self._answer_ranking,
            DealerIntent.HEALTH: self._answer_health,
        }
        
        handler = intent_handlers.get(parsed.intent)
        if handler:
            return handler(dealer, parsed.metric)
        
        # Fallback: return dealer info
        return self._answer_dealer_info(dealer, None)
    
    def _format_dashboard_response(self, dealer: DealerDashboard, dashboard_result: Dict) -> Dict:
        """Format full dashboard response."""
        return {
            "answer": dashboard_result.get("whatsapp_message", ""),
            "whatsapp_message": dashboard_result.get("whatsapp_message", ""),
            "data": dealer
        }
    
    def _answer_dealer_info(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer dealer information questions."""
        if metric == MetricType.DEALER_NAME:
            answer = f"👤 Dealer Name\n\n{dealer.dealer_name}"
        elif metric == MetricType.DEALER_CODE:
            answer = f"🏷️ Dealer Code\n\n{dealer.dealer_code}"
        elif metric == MetricType.CUSTOMER_CODE:
            answer = f"🏷️ Customer Code\n\n{dealer.customer_code}"
        elif metric == MetricType.CITY:
            answer = f"📍 City\n\n{dealer.city}"
        elif metric == MetricType.SALES_OFFICE:
            answer = f"🏢 Sales Office\n\n{dealer.sales_office}"
        elif metric == MetricType.SALES_MANAGER:
            answer = f"👨‍💼 Sales Manager\n\n{dealer.sales_manager}"
        elif metric == MetricType.DIVISION:
            answer = f"📊 Division\n\n{dealer.division}"
        elif metric == MetricType.DELIVERY_LOCATION:
            answer = f"📍 Delivery Location\n\n{dealer.delivery_location}"
        else:
            # Full dealer info
            answer = (
                f"👤 Dealer Information\n\n"
                f"Name: {dealer.dealer_name}\n"
                f"Dealer Code: {dealer.dealer_code}\n"
                f"Customer Code: {dealer.customer_code}\n"
                f"City: {dealer.city}\n"
                f"Delivery Location: {dealer.delivery_location}\n"
                f"Sales Office: {dealer.sales_office}\n"
                f"Sales Manager: {dealer.sales_manager}\n"
                f"Division: {dealer.division}\n"
                f"Warehouse: {dealer.warehouse} ({dealer.warehouse_code})"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_revenue(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer revenue questions."""
        if metric == MetricType.TOTAL_REVENUE:
            answer = f"💰 Total Revenue\n\nPKR {dealer.total_revenue:,.2f}"
        elif metric == MetricType.REVENUE_GROWTH:
            growth = dealer.revenue_growth_pct or 0.0
            trend = "📈" if growth >= 0 else "📉"
            answer = f"{trend} Revenue Growth\n\n{growth:+.1f}% month over month"
        elif metric == MetricType.MONTHLY_REVENUE:
            answer = f"📊 Monthly Revenue\n\nCurrent: PKR {dealer.current_month_revenue:,.2f}\nPrevious: PKR {dealer.previous_month_revenue:,.2f}\nGrowth: {dealer.monthly_growth:+.1f}%"
        elif metric == MetricType.PENDING_REVENUE:
            answer = f"⏳ Pending Revenue\n\nPKR {dealer.pending_revenue:,.2f}"
        elif metric == MetricType.DELIVERED_REVENUE:
            answer = f"✅ Delivered Revenue\n\nPKR {dealer.delivered_revenue:,.2f}"
        elif metric == MetricType.AVG_REVENUE_PER_DN:
            answer = f"📊 Average Revenue per DN\n\nPKR {dealer.average_revenue_per_dn:,.2f}"
        elif metric == MetricType.AVG_REVENUE_PER_UNIT:
            answer = f"📊 Average Revenue per Unit\n\nPKR {dealer.average_revenue_per_unit:,.2f}"
        else:
            # Full revenue summary
            answer = (
                f"💰 Revenue Summary\n\n"
                f"Total Revenue: PKR {dealer.total_revenue:,.2f}\n"
                f"Delivered: PKR {dealer.delivered_revenue:,.2f}\n"
                f"Pending: PKR {dealer.pending_revenue:,.2f}\n"
                f"Monthly Growth: {dealer.monthly_growth:+.1f}%\n"
                f"Avg Revenue per DN: PKR {dealer.average_revenue_per_dn:,.2f}\n"
                f"Avg Revenue per Unit: PKR {dealer.average_revenue_per_unit:,.2f}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_dn(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer DN questions."""
        if metric == MetricType.TOTAL_DN:
            answer = f"📋 Total DNs\n\n{dealer.total_dn:,}"
        elif metric == MetricType.COMPLETED_DN:
            answer = f"✅ Completed DNs\n\n{dealer.completed_dn:,}"
        elif metric == MetricType.PENDING_DN:
            answer = f"⏳ Pending DNs\n\n{dealer.pending_dn:,}"
        elif metric == MetricType.AVG_DN_VALUE:
            answer = f"📊 Average DN Value\n\nPKR {dealer.average_revenue_per_dn:,.2f}"
        elif metric == MetricType.HIGHEST_DN:
            answer = f"🏆 Highest Revenue DN\n\n{dealer.highest_revenue_dn}"
        elif metric == MetricType.LOWEST_DN:
            answer = f"📉 Lowest Revenue DN\n\n{dealer.lowest_revenue_dn}"
        elif metric == MetricType.NEWEST_DN:
            answer = f"🆕 Newest DN\n\n{dealer.newest_dn}"
        elif metric == MetricType.OLDEST_PENDING_DN:
            answer = f"⏰ Oldest Pending DN\n\n{dealer.oldest_pending_dn} ({dealer.oldest_pending_days} days old)"
        else:
            # Full DN summary
            answer = (
                f"📋 DN Summary\n\n"
                f"Total: {dealer.total_dn:,}\n"
                f"Completed: {dealer.completed_dn:,}\n"
                f"Pending: {dealer.pending_dn:,}\n"
                f"Average Value: PKR {dealer.average_revenue_per_dn:,.2f}\n"
                f"Newest: {dealer.newest_dn}\n"
                f"Highest Revenue: {dealer.highest_revenue_dn}\n"
                f"Oldest Pending: {dealer.oldest_pending_dn} ({dealer.oldest_pending_days} days)"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_units(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer units questions."""
        if metric == MetricType.TOTAL_UNITS:
            answer = f"📦 Total Units\n\n{dealer.total_units:,}"
        elif metric == MetricType.DELIVERED_UNITS:
            answer = f"✅ Delivered Units\n\n{dealer.delivered_units:,}"
        elif metric == MetricType.PENDING_UNITS:
            answer = f"⏳ Pending Units\n\n{dealer.pending_units:,}"
        elif metric == MetricType.AVG_UNITS_PER_DN:
            answer = f"📊 Average Units per DN\n\n{dealer.average_units_per_dn:.2f}"
        else:
            # Full units summary
            answer = (
                f"📦 Units Summary\n\n"
                f"Total: {dealer.total_units:,}\n"
                f"Delivered: {dealer.delivered_units:,}\n"
                f"Pending: {dealer.pending_units:,}\n"
                f"Average per DN: {dealer.average_units_per_dn:.2f}\n"
                f"Highest DN: {dealer.highest_unit_dn}\n"
                f"Lowest DN: {dealer.lowest_unit_dn}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_delivery(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer delivery questions."""
        distance_text = f"{dealer.distance.distance_km:,.1f} KM" if dealer.distance.distance_km else "Unknown"
        
        if metric == MetricType.DELIVERY_SUCCESS:
            answer = f"🚚 Delivery Success\n\n{dealer.delivery_success_pct:.1f}%"
        elif metric == MetricType.AVG_DELIVERY_DAYS:
            answer = f"📅 Average Delivery Days\n\n{dealer.average_delivery_days:.2f} days"
        elif metric == MetricType.FASTEST_DELIVERY:
            answer = f"⚡ Fastest Delivery\n\n{dealer.fastest_delivery_days:.0f} days"
        elif metric == MetricType.SLOWEST_DELIVERY:
            answer = f"🐢 Slowest Delivery\n\n{dealer.slowest_delivery_days:.0f} days"
        elif metric == MetricType.SAME_DAY_DELIVERY:
            answer = f"📦 Same Day Deliveries\n\n{dealer.same_day_deliveries:,}"
        elif metric == MetricType.NEXT_DAY_DELIVERY:
            answer = f"📦 Next Day Deliveries\n\n{dealer.next_day_deliveries:,}"
        elif metric == MetricType.DISTANCE:
            answer = f"📍 Distance\n\n{distance_text}"
        elif metric == MetricType.DRIVING_TIME:
            answer = f"🚗 Driving Time\n\n{dealer.distance.estimated_driving_time}"
        elif metric == MetricType.ESTIMATED_DELIVERY:
            answer = f"📦 Estimated Delivery\n\n{dealer.distance.estimated_delivery_time}"
        else:
            # Full delivery summary
            answer = (
                f"🚚 Delivery Performance\n\n"
                f"Success Rate: {dealer.delivery_success_pct:.1f}%\n"
                f"Average Days: {dealer.average_delivery_days:.2f}\n"
                f"Fastest: {dealer.fastest_delivery_days:.0f} days\n"
                f"Slowest: {dealer.slowest_delivery_days:.0f} days\n"
                f"Same Day: {dealer.same_day_deliveries:,}\n"
                f"Next Day: {dealer.next_day_deliveries:,}\n"
                f"Distance: {distance_text}\n"
                f"Driving Time: {dealer.distance.estimated_driving_time}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_pgi(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer PGI questions."""
        if metric == MetricType.PGI_SUCCESS:
            answer = f"✅ PGI Success\n\n{dealer.pgi_success_pct:.1f}%"
        elif metric == MetricType.PENDING_PGI:
            answer = f"⏳ Pending PGI\n\n{dealer.pgi_pending_dn:,} DNs"
        elif metric == MetricType.AVG_PGI_DAYS:
            answer = f"📅 Average PGI Days\n\n{dealer.average_delivery_days:.2f} days"
        elif metric == MetricType.LATEST_PGI:
            answer = f"🆕 Latest PGI\n\n{dealer.latest_pgi_date}"
        else:
            # Full PGI summary
            answer = (
                f"📋 PGI Summary\n\n"
                f"Success Rate: {dealer.pgi_success_pct:.1f}%\n"
                f"Pending: {dealer.pgi_pending_dn:,} DNs\n"
                f"Average Days: {dealer.average_delivery_days:.2f}\n"
                f"Latest: {dealer.latest_pgi_date}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_pod(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer POD questions."""
        if metric == MetricType.POD_SUCCESS:
            answer = f"✅ POD Success\n\n{dealer.pod_success_pct:.1f}%"
        elif metric == MetricType.PENDING_POD:
            answer = f"⏳ Pending POD\n\n{dealer.pod_pending_dn:,} DNs"
        elif metric == MetricType.AVG_POD_DAYS:
            answer = f"📅 Average POD Days\n\n{dealer.average_pod_days:.2f} days"
        elif metric == MetricType.LATEST_POD:
            answer = f"🆕 Latest POD\n\n{dealer.latest_pod_date}"
        else:
            # Full POD summary
            answer = (
                f"📋 POD Summary\n\n"
                f"Success Rate: {dealer.pod_success_pct:.1f}%\n"
                f"Pending: {dealer.pod_pending_dn:,} DNs\n"
                f"Average Days: {dealer.average_pod_days:.2f}\n"
                f"Latest: {dealer.latest_pod_date}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_pending(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer pending questions."""
        if metric == MetricType.PENDING_PCT:
            answer = f"⏳ Pending Percentage\n\n{dealer.pending_pct:.1f}%"
        elif metric == MetricType.PENDING_REVENUE:
            answer = f"⏳ Pending Revenue\n\nPKR {dealer.pending_revenue:,.2f}"
        elif metric == MetricType.PENDING_UNITS:
            answer = f"⏳ Pending Units\n\n{dealer.pending_units:,}"
        elif metric == MetricType.PENDING_DN:
            answer = f"⏳ Pending DNs\n\n{dealer.pending_dn:,}"
        elif metric == MetricType.CRITICAL_PENDING:
            answer = f"🔴 Critical Pending\n\n{dealer.critical_pending} DNs (>7 days)"
        elif metric == MetricType.OVERDUE_PENDING:
            answer = f"🔴 Overdue Pending\n\n{dealer.overdue_pending} DNs (>14 days)"
        elif metric == MetricType.PENDING_AGE:
            answer = f"⏰ Pending Age\n\nOldest: {dealer.oldest_pending_days} days\nAverage: {dealer.pending_average_days:.1f} days"
        else:
            # Full pending dashboard
            answer = (
                f"⚠️ Pending Dashboard\n\n"
                f"Pending DNs: {dealer.pending_dn:,}\n"
                f"Pending Units: {dealer.pending_units:,}\n"
                f"Pending Revenue: PKR {dealer.pending_revenue:,.2f}\n"
                f"Pending Rate: {dealer.pending_pct:.1f}%\n"
                f"Average Days: {dealer.pending_average_days:.1f}\n"
                f"Oldest: {dealer.oldest_pending_dn} ({dealer.oldest_pending_days} days)\n"
                f"Critical: {dealer.critical_pending} (>7 days)\n"
                f"Overdue: {dealer.overdue_pending} (>14 days)"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_product(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer product questions."""
        if metric == MetricType.TOP_PRODUCT:
            answer = f"🏆 Top Product\n\n{dealer.top_product}"
        elif metric == MetricType.TOP_MODEL:
            answer = f"🏆 Top Model\n\n{dealer.top_model}"
        elif metric == MetricType.TOP_MATERIAL:
            answer = f"🏆 Top Material\n\n{dealer.top_material}"
        elif metric == MetricType.STRONGEST_CATEGORY:
            answer = f"📊 Strongest Category\n\n{dealer.strongest_product_category}"
        elif metric == MetricType.WEAKEST_CATEGORY:
            answer = f"📊 Weakest Category\n\n{dealer.weakest_product_category}"
        elif metric == MetricType.TOP_DIVISION:
            answer = f"🏆 Top Division\n\n{dealer.top_division}"
        else:
            # Full product summary
            answer = (
                f"📦 Product Performance\n\n"
                f"Top Product: {dealer.top_product}\n"
                f"Top Model: {dealer.top_model}\n"
                f"Top Material: {dealer.top_material}\n"
                f"Top Division: {dealer.top_division}\n"
                f"Strongest Category: {dealer.strongest_product_category}\n"
                f"Weakest Category: {dealer.weakest_product_category}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_warehouse(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer warehouse questions."""
        distance_text = f"{dealer.distance.distance_km:,.1f} KM" if dealer.distance.distance_km else "Unknown"
        
        if metric == MetricType.WAREHOUSE:
            answer = f"🏭 Warehouse\n\n{dealer.warehouse}"
        elif metric == MetricType.WAREHOUSE_CODE:
            answer = f"🏭 Warehouse Code\n\n{dealer.warehouse_code}"
        elif metric == MetricType.WAREHOUSE_UTILIZATION:
            answer = f"📊 Warehouse Utilization\n\n{dealer.warehouse_utilization:.1f}%"
        elif metric == MetricType.DISTANCE:
            answer = f"📍 Distance\n\n{distance_text}"
        elif metric == MetricType.DRIVING_TIME:
            answer = f"🚗 Driving Time\n\n{dealer.distance.estimated_driving_time}"
        elif metric == MetricType.ESTIMATED_DELIVERY:
            answer = f"📦 Estimated Delivery\n\n{dealer.distance.estimated_delivery_time}"
        else:
            # Full warehouse summary
            answer = (
                f"🏭 Warehouse Information\n\n"
                f"Warehouse: {dealer.warehouse} ({dealer.warehouse_code})\n"
                f"Distance: {distance_text}\n"
                f"Driving Time: {dealer.distance.estimated_driving_time}\n"
                f"Estimated Delivery: {dealer.distance.estimated_delivery_time}\n"
                f"Utilization: {dealer.warehouse_utilization:.1f}%"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_ranking(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer ranking questions."""
        if metric == MetricType.NATIONAL_RANK:
            answer = f"🏆 National Rank\n\n#{dealer.national_rank or 'N/A'}"
        elif metric == MetricType.REGIONAL_RANK:
            answer = f"🏆 Regional Rank\n\n#{dealer.regional_rank or 'N/A'}"
        elif metric == MetricType.REVENUE_RANK:
            answer = f"🏆 Revenue Rank\n\n#{dealer.revenue_rank or 'N/A'}"
        elif metric == MetricType.DELIVERY_RANK:
            answer = f"🏆 Delivery Rank\n\n#{dealer.delivery_rank or 'N/A'}"
        elif metric == MetricType.DN_RANK:
            answer = f"🏆 DN Rank\n\n#{dealer.dn_rank or 'N/A'}"
        elif metric == MetricType.UNIT_RANK:
            answer = f"🏆 Unit Rank\n\n#{dealer.unit_rank or 'N/A'}"
        elif metric == MetricType.PENDING_RANK:
            answer = f"🏆 Pending Rank\n\n#{dealer.pending_rank or 'N/A'}"
        elif metric == MetricType.POD_RANK:
            answer = f"🏆 POD Rank\n\n#{dealer.pod_rank or 'N/A'}"
        else:
            # Full ranking summary
            answer = (
                f"🏆 Dealer Rankings\n\n"
                f"National: #{dealer.national_rank or 'N/A'}\n"
                f"Regional: #{dealer.regional_rank or 'N/A'}\n"
                f"Revenue: #{dealer.revenue_rank or 'N/A'}\n"
                f"Delivery: #{dealer.delivery_rank or 'N/A'}\n"
                f"DN: #{dealer.dn_rank or 'N/A'}\n"
                f"Units: #{dealer.unit_rank or 'N/A'}\n"
                f"Pending: #{dealer.pending_rank or 'N/A'}\n"
                f"POD: #{dealer.pod_rank or 'N/A'}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }
    
    def _answer_health(self, dealer: DealerDashboard, metric: Optional[MetricType]) -> Dict:
        """Answer business health questions."""
        if metric == MetricType.BUSINESS_SCORE:
            answer = f"💳 Business Score\n\n{dealer.business_score:.1f}/100"
        elif metric == MetricType.OVERALL_STATUS:
            status_emoji = "🟢" if dealer.overall_status == "Excellent" else "🟡" if dealer.overall_status == "Good" else "🟠" if dealer.overall_status == "Watch" else "🔴"
            answer = f"{status_emoji} Overall Status\n\n{dealer.overall_status}"
        elif metric == MetricType.EXECUTIVE_SUMMARY:
            answer = f"📝 Executive Summary\n\n{dealer.executive_summary}"
        else:
            # Full health summary
            status_emoji = "🟢" if dealer.overall_status == "Excellent" else "🟡" if dealer.overall_status == "Good" else "🟠" if dealer.overall_status == "Watch" else "🔴"
            answer = (
                f"💳 Business Health\n\n"
                f"Score: {dealer.business_score:.1f}/100\n"
                f"Status: {status_emoji} {dealer.overall_status}\n\n"
                f"📝 Executive Summary\n"
                f"{dealer.executive_summary}\n\n"
                f"💡 Key Insights\n"
                f"{chr(10).join(f'• {insight}' for insight in dealer.insights[:5])}\n\n"
                f"💡 Recommendations\n"
                f"{chr(10).join(f'• {rec}' for rec in dealer.recommendations[:3])}"
            )
        
        return {
            "answer": answer,
            "whatsapp_message": answer,
            "data": dealer
        }


@dataclass
class DistanceAnalytics:
    warehouse: str
    dealer_city: str
    distance_km: Optional[float] = None
    estimated_driving_minutes: Optional[int] = None
    estimated_driving_time: str = "Unknown"
    estimated_delivery_time: str = "Unknown"
    source: str = "unavailable"


@dataclass
class DealerDashboard:
    dealer_name: str
    dealer_code: str
    customer_code: str
    city: str
    warehouse: str
    warehouse_code: str
    sales_office: str
    sales_manager: str
    division: str
    total_dn: int
    completed_dn: int
    pending_dn: int
    total_units: int
    total_revenue: float
    average_revenue_per_dn: float
    average_units_per_dn: float
    first_delivery_date: str
    latest_delivery_date: str
    average_delivery_days: float
    average_pod_days: float
    average_total_cycle_time: float
    delivery_success_pct: float
    pgi_success_pct: float
    pod_success_pct: float
    pending_pct: float
    distance: DistanceAnalytics
    delivery_location: str = "Unknown"
    revenue_rank: Optional[int] = None
    delivery_rank: Optional[int] = None
    busiest_month: str = "Unknown"
    strongest_product_category: str = "Unknown"
    weakest_product_category: str = "Unknown"
    revenue_growth_pct: Optional[float] = None
    insights: list[str] = field(default_factory=list)

    delivered_units: int = 0
    pending_units: int = 0
    delivered_revenue: float = 0.0
    pending_revenue: float = 0.0
    pgi_pending_dn: int = 0
    pod_pending_dn: int = 0
    delivery_pending_dn: int = 0

    oldest_pending_dn: str = "N/A"
    oldest_pending_days: int = 0
    newest_dn: str = "N/A"
    highest_revenue_dn: str = "N/A"
    lowest_revenue_dn: str = "N/A"
    highest_unit_dn: str = "N/A"
    lowest_unit_dn: str = "N/A"
    average_revenue_per_unit: float = 0.0

    warehouse_utilization: float = 0.0
    delivery_coverage: float = 0.0
    top_product: str = "Unknown"
    top_model: str = "Unknown"
    top_material: str = "Unknown"

    current_month_revenue: float = 0.0
    previous_month_revenue: float = 0.0
    monthly_growth: float = 0.0
    current_month_dn: int = 0
    previous_month_dn: int = 0
    current_month_units: int = 0
    previous_month_units: int = 0
    best_month: str = "Unknown"
    worst_month: str = "Unknown"

    pending_average_days: float = 0.0
    critical_pending: int = 0
    overdue_pending: int = 0
    national_rank: Optional[int] = None
    unit_rank: Optional[int] = None
    dn_rank: Optional[int] = None
    pod_rank: Optional[int] = None
    pending_rank: Optional[int] = None
    regional_rank: Optional[int] = None
    fastest_delivery_days: float = 0.0
    slowest_delivery_days: float = 0.0
    latest_pgi_date: str = "N/A"
    latest_pod_date: str = "N/A"
    same_day_deliveries: int = 0
    next_day_deliveries: int = 0
    top_division: str = "Unknown"
    recommendations: list[str] = field(default_factory=list)
    business_score: float = 0.0
    overall_status: str = "Needs Attention"
    executive_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_whatsapp_message(self) -> str:
        distance = "Unknown" if self.distance.distance_km is None else f"{self.distance.distance_km:,.1f} KM"
        insights = "\n".join(f"\u2022 {item}" for item in self.insights[:10]) or "\u2022 No significant exception detected."
        recommendations = "\n".join(f"\u2022 {item}" for item in self.recommendations[:5]) or "\u2022 Continue monitoring delivery performance."
        return "\n".join(
            (
                "\U0001f3e2 Dealer Dashboard",
                "\u2501" * 18,
                "\U0001f464 Dealer Information",
                f"Dealer Name: {self.dealer_name}",
                f"Dealer Code: {self.dealer_code}",
                f"Customer Code: {self.customer_code}",
                f"City: {self.city}",
                f"Delivery Location: {self.delivery_location}",
                f"Sales Office: {self.sales_office}",
                f"Sales Manager: {self.sales_manager}",
                f"Division: {self.division}",
                "",
                "\U0001f3ed Warehouse",
                f"Warehouse: {self.warehouse} ({self.warehouse_code})",
                f"Distance: {distance}",
                f"Driving Time: {self.distance.estimated_driving_time}",
                f"Estimated Delivery: {self.distance.estimated_delivery_time}",
                "",
                "\U0001f4ca Business Summary",
                f"Revenue: PKR {self.total_revenue:,.2f}",
                f"Average Revenue/DN: PKR {self.average_revenue_per_dn:,.2f}",
                f"Units: {self.total_units:,}",
                f"Average Units/DN: {self.average_units_per_dn:,.2f}",
                f"Total DNs: {self.total_dn:,}",
                f"Completed DNs: {self.completed_dn:,}",
                f"Pending DNs: {self.pending_dn:,}",
                "",
                "\U0001f69a Delivery Performance",
                f"Delivery Success: {self.delivery_success_pct:.2f}%",
                f"PGI Success: {self.pgi_success_pct:.2f}%",
                f"POD Success: {self.pod_success_pct:.2f}%",
                f"Pending: {self.pending_pct:.2f}%",
                f"Average Delivery: {self.average_delivery_days:.2f} Days",
                f"Average POD: {self.average_pod_days:.2f} Days",
                f"Average Total Cycle: {self.average_total_cycle_time:.2f} Days",
                f"Fastest / Slowest: {self.fastest_delivery_days:.0f} / {self.slowest_delivery_days:.0f} Days",
                "",
                "\U0001f4c5 Date Summary",
                f"First DN: {self.first_delivery_date}",
                f"Latest DN: {self.latest_delivery_date}",
                f"Latest PGI: {self.latest_pgi_date}",
                f"Latest POD: {self.latest_pod_date}",
                "",
                "\U0001f4e6 Product Performance",
                f"Top Product: {self.top_product}",
                f"Top Model: {self.top_model}",
                f"Top Material: {self.top_material}",
                f"Strongest Category: {self.strongest_product_category}",
                f"Weakest Category: {self.weakest_product_category}",
                "",
                "\U0001f3c6 Dealer Ranking",
                f"Revenue Rank: {self.revenue_rank or 'N/A'}",
                f"Delivery Rank: {self.delivery_rank or 'N/A'}",
                f"Regional Rank: {self.regional_rank or 'N/A'}",
                f"National Rank: {self.national_rank or 'N/A'}",
                "",
                "\u26a0 Pending Dashboard",
                f"Pending Revenue: PKR {self.pending_revenue:,.2f}",
                f"Pending Units: {self.pending_units:,}",
                f"Pending DNs: {self.pending_dn:,}",
                f"Average Pending: {self.pending_average_days:.1f} Days",
                f"Oldest Pending: {self.oldest_pending_dn} ({self.oldest_pending_days} Days)",
                "",
                "\U0001f7e2 Business Health",
                f"Overall Score: {self.business_score:.1f}/100",
                f"Business Status: {self.overall_status}",
                "",
                "\U0001f4a1 Key Insights",
                insights,
                "",
                "\U0001f4cc Recommendations",
                recommendations,
                "",
                "\U0001f4dd Executive Summary",
                self.executive_summary or "Performance is stable; continue monitoring pending deliveries and POD closure.",
            )
        )

    def __str__(self) -> str:
        return self.to_whatsapp_message()


@dataclass
class DealerComparison:
    dealers: list[DealerDashboard]
    revenue_leader: str
    units_leader: str
    dn_leader: str
    delivery_leader: str
    summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerRanking:
    sort_by: str
    order: str
    dealers: list[DealerDashboard]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DealerSearchResult:
    original_message: str
    extracted_dealer: str
    normalized_dealer: str
    dealer_found: Optional[str] = None
    dealer_code: Optional[str] = None
    customer_code: Optional[str] = None
    alias_used: Optional[str] = None
    rapidfuzz_score: Optional[float] = None
    semantic_score: Optional[float] = None
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False
    cache_used: bool = False
    exception: Optional[str] = None


class CityCoordinateService:
    """Cached coordinates; distances are calculated, never hardcoded."""

    COORDINATES: Dict[str, tuple[float, float]] = {
        "abbottabad": (34.1688, 73.2215), "attock": (33.7667, 72.3667),
        "bahawalpur": (29.3956, 71.6836), "bannu": (32.9861, 70.6042),
        "dera ghazi khan": (30.0489, 70.6455), "dera ismail khan": (31.8315, 70.9017),
        "faisalabad": (31.4504, 73.1350), "gilgit": (35.9208, 74.3144),
        "gujranwala": (32.1877, 74.1945), "gujrat": (32.5731, 74.1005),
        "haripur": (33.9946, 72.9106), "hyderabad": (25.3960, 68.3578),
        "islamabad": (33.6844, 73.0479), "jacobabad": (28.2819, 68.4382),
        "jhelum": (32.9405, 73.7276), "karachi": (24.8607, 67.0011),
        "kasur": (31.1187, 74.4508), "kohat": (33.5834, 71.4332),
        "lahore": (31.5204, 74.3587), "larkana": (27.5570, 68.2028),
        "mardan": (34.1989, 72.0231), "mansehra": (34.3302, 73.1968),
        "mirpur": (33.1484, 73.7517), "multan": (30.1575, 71.5249),
        "muzaffarabad": (34.3700, 73.4711), "nawabshah": (26.2442, 68.4100),
        "okara": (30.8138, 73.4534), "peshawar": (34.0151, 71.5249),
        "quetta": (30.1798, 66.9750), "rahim yar khan": (28.4212, 70.2989),
        "rawalpindi": (33.5651, 73.0169), "sahiwal": (30.6682, 73.1114),
        "sargodha": (32.0836, 72.6711), "sheikhupura": (31.7167, 73.9850),
        "sialkot": (32.4945, 74.5229), "skardu": (35.2971, 75.6333),
        "sukkur": (27.7244, 68.8228), "swat": (35.2227, 72.4258),
        "wah cantt": (33.7715, 72.7511), "taxila": (33.7463, 72.8397),
    }
    
    _normalize_cache: Dict[str, str] = {}
    _city_cache: Dict[str, Optional[tuple[float, float]]] = {}

    def __init__(self) -> None:
        self._names = tuple(self.COORDINATES)

    @staticmethod
    def normalize(city: Any) -> str:
        if not city:
            return ""
        
        value = str(city).lower()
        
        cached = CityCoordinateService._normalize_cache.get(value)
        if cached is not None:
            return cached
        
        value = value.replace("city", "").strip(" ,.-")
        
        aliases = {
            "rwp": "rawalpindi", "isb": "islamabad", "lhr": "lahore", 
            "khi": "karachi", "fsd": "faisalabad", "hyd": "hyderabad", 
            "ryk": "rahim yar khan", "dik": "dera ismail khan"
        }
        result = aliases.get(value, value)
        CityCoordinateService._normalize_cache[value] = result
        return result

    def get(self, city: Any) -> Optional[tuple[float, float]]:
        if not city:
            return None
            
        normalized = self.normalize(city)
        if normalized in self._city_cache:
            return self._city_cache[normalized]
        
        if normalized in self.COORDINATES:
            self._city_cache[normalized] = self.COORDINATES[normalized]
            return self.COORDINATES[normalized]
        
        match = process.extractOne(normalized, self._names, scorer=fuzz.WRatio, score_cutoff=82)
        if match:
            result = self.COORDINATES[match[0]]
            self._city_cache[normalized] = result
            return result
        
        self._city_cache[normalized] = None
        return None


class DistanceService:
    def __init__(self, coordinates: CityCoordinateService) -> None:
        self.coordinates = coordinates
        self.cache: TTLCache[str, DistanceAnalytics] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._lock = threading.RLock()
        self._ors = None
        if ORS_API_KEY and Client:
            try:
                self._ors = Client(key=ORS_API_KEY, timeout=2)
            except Exception:
                logger.exception("ORS client initialization failed")

    @staticmethod
    def delivery_estimate(km: Optional[float]) -> str:
        if km is None:
            return "Unknown"
        if km <= 80:
            return "Same Day"
        if km <= 200:
            return "Next Day"
        if km <= 400:
            return "1-2 Days"
        if km <= 700:
            return "2-3 Days"
        return "3-5 Days"

    @staticmethod
    def driving_time(minutes: Optional[int]) -> str:
        if minutes is None:
            return "Unknown"
        hours, mins = divmod(max(0, minutes), 60)
        return f"{hours} hr {mins} min" if hours and mins else (f"{hours} hr" if hours else f"{mins} min")

    @staticmethod
    def _haversine(origin: tuple[float, float], destination: tuple[float, float]) -> float:
        lat1, lon1, lat2, lon2 = map(math.radians, (*origin, *destination))
        value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        return 6371.0088 * 2 * math.asin(math.sqrt(value))

    def calculate(self, warehouse: Any, dealer_city: Any) -> DistanceAnalytics:
        warehouse_name, city_name = _text(warehouse), _text(dealer_city)
        key = f"{self.coordinates.normalize(warehouse_name)}|{self.coordinates.normalize(city_name)}"
        
        with self._lock:
            cached = self.cache.get(key)
        if cached:
            return cached
        
        origin, destination = self.coordinates.get(warehouse_name), self.coordinates.get(city_name)
        if not origin or not destination:
            result = DistanceAnalytics(warehouse_name, city_name)
        else:
            km: Optional[float] = None
            minutes: Optional[int] = None
            source = "great-circle"
            
            if self._ors:
                try:
                    route = self._ors.directions(
                        [(origin[1], origin[0]), (destination[1], destination[0])], 
                        profile="driving-car"
                    )
                    summary = route["routes"][0]["summary"]
                    km = float(summary["distance"]) / 1000
                    minutes = int(round(float(summary["duration"]) / 60))
                    source = "openrouteservice"
                except Exception:
                    logger.warning("ORS route failed for %s to %s; using great-circle", warehouse_name, city_name, exc_info=True)
            
            if km is None:
                km = float(great_circle(origin, destination).km) if great_circle else self._haversine(origin, destination)
                km *= 1.20
                minutes = int(round(km / 55 * 60))
            
            result = DistanceAnalytics(
                warehouse_name, city_name, 
                round(km, 1), minutes, 
                self.driving_time(minutes), 
                self.delivery_estimate(km), 
                source
            )
        
        with self._lock:
            self.cache[key] = result
        return result


class DealerAnalyticsService:
    """Enterprise Dealer Intelligence Engine with Question Answering."""

    SORT_ALIASES = {
        "revenue": "total_revenue", "units": "total_units", "dn": "total_dn", "dn_count": "total_dn",
        "average_delivery": "average_delivery_days", "fastest_delivery": "average_delivery_days",
        "highest_pod": "pod_success_pct", "lowest_pending": "pending_pct", "best_revenue_growth": "revenue_growth_pct",
        "highest_pending": "pending_pct", "lowest_revenue": "total_revenue", "lowest_units": "total_units",
        "slowest_delivery": "average_delivery_days", "poor_pod": "pod_success_pct",
    }
    
    STOP_PHRASES = frozenset({
        "tell me about", "dealer dashboard", "dealer profile", "dealer performance",
        "dealer statistics", "dealer revenue", "dealer distance", "dealer pending",
        "dealer status", "dealer pod", "dealer pgi", "show", "display", "dealer",
        "profile", "statistics", "performance", "status", "revenue", "distance",
        "pending", "dashboard", "about", "of", "the", "company", "private",
        "limited", "pvt", "ltd",
    })
    
    DEALER_ALIASES = {
        "mian": "Mian Group Chakwal",
        "mgc": "Mian Group Chakwal",
        "mian chakwal": "Mian Group Chakwal",
        "mian wah": "Mian Group Chakwal",
        "mian chakwal wah": "Mian Group Chakwal",
        "mian group chakwal wah": "Mian Group Chakwal",
        "taj": "Taj Electronics",
        "taj haripur": "Taj Electronics Haripur",
    }
    
    _normalize_regex = re.compile(r'[^a-z0-9\s]')

    def __init__(self) -> None:
        self._service_name = "dealer_analytics"
        self._version = "5.0.0-intelligence"
        self._startup_time = datetime.utcnow().isoformat()
        self._initialization_errors: list[str] = []
        
        try:
            self._coordinates = CityCoordinateService()
        except Exception as error:
            logger.exception("Coordinate service initialization failed")
            self._initialization_errors.append(str(error))
            self._coordinates = CityCoordinateService.__new__(CityCoordinateService)
            self._coordinates._names = tuple()
            self._coordinates.COORDINATES = {}
            self._coordinates._city_cache = {}
        
        try:
            self._distance = DistanceService(self._coordinates)
        except Exception as error:
            logger.exception("Distance service initialization failed")
            self._initialization_errors.append(str(error))
            self._distance = None
        
        self._dealer_cache: TTLCache[str, DealerSearchResult] = TTLCache(maxsize=4096, ttl=CACHE_TTL)
        self._candidate_cache: TTLCache[str, list[dict[str, str]]] = TTLCache(maxsize=1, ttl=1800)
        self._extended_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=1800)
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=4096, ttl=600)
        self._ranking_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=128, ttl=600)
        self._search_lock = threading.RLock()
        self._last_diagnostic: dict[str, Any] = {}
        self._aggregate_cache: TTLCache[str, list[Any]] = TTLCache(maxsize=1024, ttl=300)
        
        # Initialize Question Engine
        self._question_engine = DealerQuestionEngine(self)

    @staticmethod
    def _session() -> Session:
        return SessionLocal()

    @staticmethod
    def _dealer_filter(identifier: str) -> Any:
        token = identifier.strip()
        return or_(
            func.lower(func.trim(DeliveryReport.customer_name)) == token.lower(),
            DeliveryReport.dealer_code == token,
            DeliveryReport.customer_code == token,
        )

    @classmethod
    def _normalize_dealer_text(cls, value: Any) -> str:
        if not value:
            return ""
        
        text_value = unicodedata.normalize("NFKD", _text(value, "").lower())
        text_value = cls._normalize_regex.sub(" ", text_value)
        text_value = _WHITESPACE_PATTERN.sub(" ", text_value).strip()
        
        for phrase in cls.STOP_PHRASES:
            if phrase in text_value:
                text_value = text_value.replace(phrase, " ")
        
        return _WHITESPACE_PATTERN.sub(" ", text_value).strip()

    def _dealer_candidates(self, session: Session) -> tuple[list[dict[str, str]], bool]:
        with self._search_lock:
            cached = self._candidate_cache.get("all")
        if cached is not None:
            return cached, True
        
        started = time.perf_counter()
        query = text("""
            SELECT DISTINCT 
                customer_name, 
                dealer_code, 
                customer_code 
            FROM delivery_reports 
            WHERE customer_name IS NOT NULL 
              AND customer_name != ''
        """)
        
        rows = session.execute(query).fetchall()
        
        candidates = [
            {
                "name": _text(row.customer_name),
                "dealer_code": _text(row.dealer_code, ""),
                "customer_code": _text(row.customer_code, ""),
                "normalized": self._normalize_dealer_text(row.customer_name),
            }
            for row in rows if _text(row.customer_name, "")
        ]
        
        with self._search_lock:
            self._candidate_cache["all"] = candidates
        
        logger.info("Dealer candidates loaded: %s in %.2fms", 
                   len(candidates), (time.perf_counter() - started) * 1000)
        return candidates, False

    def _resolve_dealer(self, session: Session, message: str) -> DealerSearchResult:
        started = time.perf_counter()
        original = _text(message, "")
        normalized = self._normalize_dealer_text(original)
        alias = self.DEALER_ALIASES.get(normalized)
        search_text = alias or normalized
        cache_key = search_text.lower()
        
        with self._search_lock:
            cached = self._dealer_cache.get(cache_key)
        if cached:
            result = DealerSearchResult(**asdict(cached))
            result.original_message, result.cache_used = original, True
            return result
        
        result = DealerSearchResult(original, search_text, normalized, alias_used=alias)
        
        try:
            candidates, cache_used = self._dealer_candidates(session)
            result.cache_used = cache_used
            
            token = original.strip()
            for item in candidates:
                if token == item["dealer_code"] or token == item["customer_code"]:
                    result.dealer_found = item["name"]
                    result.dealer_code = item["dealer_code"]
                    result.customer_code = item["customer_code"]
                    self._cache_result(cache_key, result)
                    return result
            
            norm_search = self._normalize_dealer_text(search_text)
            exact_matches = [item for item in candidates if item["normalized"] == norm_search]
            if exact_matches:
                best = exact_matches[0]
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
                result.rapidfuzz_score = 100.0
                self._cache_result(cache_key, result)
                return result
            
            if search_text:
                contains_matches = [
                    item for item in candidates 
                    if search_text in item["normalized"] or item["normalized"] in search_text
                ]
                if len(contains_matches) == 1:
                    best = contains_matches[0]
                    result.dealer_found = best["name"]
                    result.dealer_code = best["dealer_code"]
                    result.customer_code = best["customer_code"]
                    result.rapidfuzz_score = 95.0
                    self._cache_result(cache_key, result)
                    return result
            
            choices = {index: item["normalized"] for index, item in enumerate(candidates)}
            matches = process.extract(search_text, choices, scorer=fuzz.WRatio, limit=5)
            scored = [(candidates[index], float(score)) for _, score, index in matches]
            
            result.suggestions = [
                {"dealer_name": item["name"], "similarity": round(score, 2), "dealer_code": item["dealer_code"]}
                for item, score in scored
            ]
            
            if scored:
                result.rapidfuzz_score = round(scored[0][1], 2)
            
            confident = [entry for entry in scored if entry[1] >= 85]
            if len(confident) == 1 or (len(confident) > 1 and confident[0][1] - confident[1][1] >= 5):
                best = confident[0][0]
                result.dealer_found = best["name"]
                result.dealer_code = best["dealer_code"]
                result.customer_code = best["customer_code"]
            elif confident:
                result.ambiguous = True
            
            self._cache_result(cache_key, result)
            
        except Exception as error:
            result.exception = str(error)
            logger.exception("Dealer resolution failed for %s", original)
        
        self._last_diagnostic = {
            **asdict(result), 
            "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)
        }
        return result

    def _cache_result(self, key: str, result: DealerSearchResult) -> None:
        with self._search_lock:
            self._dealer_cache[key] = result

    @staticmethod
    def _suggestion_response(search: DealerSearchResult) -> dict[str, Any]:
        suggestions = search.suggestions[:5]
        if search.ambiguous:
            lines = ["Multiple Dealers Found", ""]
            for index, item in enumerate(suggestions, 1):
                lines.extend((str(index), item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            lines.append("Reply with dealer number.")
            code = "MULTIPLE_DEALERS_FOUND"
        else:
            lines = ["Did you mean", ""]
            for item in suggestions:
                lines.extend((item["dealer_name"], f'{item["similarity"]:.0f}%', ""))
            code = "DEALER_SUGGESTIONS"
        
        message = "\n".join(lines).strip()
        return {
            "success": False, 
            "error_code": code, 
            "message": message, 
            "response": message, 
            "formatted_response": message, 
            "whatsapp_message": message, 
            "suggestions": suggestions, 
            "search": search
        }

    @staticmethod
    def _dealer_key(row: Any) -> str:
        return _text(row.dealer_code, _text(row.customer_code, _text(row.dealer_name)))

    def _aggregate_query(self, session: Session, dealer: Optional[str] = None) -> list[Any]:
        cache_key = dealer or "all"
        cached = self._aggregate_cache.get(cache_key)
        if cached is not None:
            return cached
        
        started = time.perf_counter()
        
        completed = or_(DeliveryReport.pending_flag.is_(False), _status_complete(DeliveryReport.delivery_status), DeliveryReport.pod_date.isnot(None))
        pending = or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None))
        pgi_pending = DeliveryReport.good_issue_date.is_(None)
        pod_pending = and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None))
        
        query = session.query(
            func.coalesce(DeliveryReport.customer_name, "Unknown").label("dealer_name"),
            func.coalesce(DeliveryReport.dealer_code, "Unknown").label("dealer_code"),
            func.coalesce(DeliveryReport.customer_code, "Unknown").label("customer_code"),
            func.max(DeliveryReport.ship_to_city).label("city"), 
            func.max(DeliveryReport.delivery_location).label("delivery_location"), 
            func.max(DeliveryReport.warehouse).label("warehouse"),
            func.max(DeliveryReport.warehouse_code).label("warehouse_code"), 
            func.max(DeliveryReport.sales_office).label("sales_office"),
            func.max(DeliveryReport.sales_manager).label("sales_manager"), 
            func.max(DeliveryReport.division).label("division"),
            func.count(distinct(DeliveryReport.dn_no)).label("total_dn"),
            func.count(distinct(case((completed, DeliveryReport.dn_no)))).label("completed_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("pending_dn"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("total_revenue"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
            func.coalesce(func.sum(case((completed, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("delivered_revenue"),
            func.coalesce(func.sum(case((pending, DeliveryReport.dn_amount), else_=0.0)), 0.0).label("pending_revenue"),
            func.count(distinct(case((pgi_pending, DeliveryReport.dn_no)))).label("pgi_pending_dn"),
            func.count(distinct(case((pod_pending, DeliveryReport.dn_no)))).label("pod_pending_dn"),
            func.count(distinct(case((pending, DeliveryReport.dn_no)))).label("delivery_pending_dn"),
            func.min(case((pending, DeliveryReport.dn_create_date))).label("oldest_pending_date"),
            func.avg(case((pending, func.current_date() - DeliveryReport.dn_create_date))).label("pending_average_days"),
            func.min(DeliveryReport.dn_create_date).label("first_delivery_date"),
            func.max(DeliveryReport.dn_create_date).label("latest_delivery_date"),
            func.max(DeliveryReport.good_issue_date).label("latest_pgi_date"),
            func.max(DeliveryReport.pod_date).label("latest_pod_date"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("avg_delivery"),
            func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.isnot(None)), DeliveryReport.pod_date - DeliveryReport.good_issue_date))).label("avg_pod"),
            func.avg(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.pod_date - DeliveryReport.dn_create_date))).label("avg_cycle"),
            func.min(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("fastest_delivery"),
            func.max(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("slowest_delivery"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 0, DeliveryReport.dn_no)))).label("same_day_deliveries"),
            func.count(distinct(case((DeliveryReport.good_issue_date - DeliveryReport.dn_create_date == 1, DeliveryReport.dn_no)))).label("next_day_deliveries"),
            func.count(distinct(case((_status_complete(DeliveryReport.delivery_status), DeliveryReport.dn_no)))).label("delivery_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pgi_status), DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no)))).label("pgi_success"),
            func.count(distinct(case((or_(_status_complete(DeliveryReport.pod_status), DeliveryReport.pod_date.isnot(None)), DeliveryReport.dn_no)))).label("pod_success"),
        ).filter(DeliveryReport.customer_name.isnot(None))
        
        if dealer:
            query = query.filter(self._dealer_filter(dealer))
        
        result = query.group_by(
            DeliveryReport.customer_name, 
            DeliveryReport.dealer_code, 
            DeliveryReport.customer_code
        ).all()
        
        self._aggregate_cache[cache_key] = result
        logger.debug("Aggregate query: %.2fms", (time.perf_counter() - started) * 1000)
        return result

    @staticmethod
    def _days(value: Any) -> float:
        if value is None:
            return 0.0
        if hasattr(value, "days"):
            return round(float(value.days), 2)
        return round(_number(value), 2)

    def _row_to_dashboard(self, row: Any, include_distance: bool = True) -> DealerDashboard:
        total = int(row.total_dn or 0)
        
        dashboard = DealerDashboard(
            dealer_name=_text(row.dealer_name), 
            dealer_code=_text(row.dealer_code), 
            customer_code=_text(row.customer_code),
            city=_text(row.city), 
            delivery_location=_text(getattr(row, "delivery_location", None)), 
            warehouse=_text(row.warehouse), 
            warehouse_code=_text(row.warehouse_code),
            sales_office=_text(row.sales_office), 
            sales_manager=_text(row.sales_manager), 
            division=_text(row.division),
            total_dn=total, 
            completed_dn=int(row.completed_dn or 0), 
            pending_dn=int(row.pending_dn or 0),
            total_units=int(row.total_units or 0), 
            total_revenue=round(_number(row.total_revenue), 2),
            average_revenue_per_dn=round(_number(row.total_revenue) / total, 2) if total else 0,
            average_units_per_dn=round(_number(row.total_units) / total, 2) if total else 0,
            first_delivery_date=_date_text(row.first_delivery_date), 
            latest_delivery_date=_date_text(row.latest_delivery_date),
            average_delivery_days=self._days(row.avg_delivery), 
            average_pod_days=self._days(row.avg_pod),
            average_total_cycle_time=self._days(row.avg_cycle), 
            delivery_success_pct=_percent(row.delivery_success, total),
            pgi_success_pct=_percent(row.pgi_success, total), 
            pod_success_pct=_percent(row.pod_success, total),
            pending_pct=_percent(row.pending_dn, total),
            distance=(self._safe_distance(row.warehouse, row.city) if include_distance else DistanceAnalytics(_text(row.warehouse), _text(row.city))),
            delivered_units=int(getattr(row, "delivered_units", 0) or 0),
            pending_units=int(getattr(row, "pending_units", 0) or 0),
            delivered_revenue=round(_number(getattr(row, "delivered_revenue", 0)), 2),
            pending_revenue=round(_number(getattr(row, "pending_revenue", 0)), 2),
            pgi_pending_dn=int(getattr(row, "pgi_pending_dn", 0) or 0),
            pod_pending_dn=int(getattr(row, "pod_pending_dn", 0) or 0),
            delivery_pending_dn=int(getattr(row, "delivery_pending_dn", 0) or 0),
            oldest_pending_days=max(0, (date.today() - row.oldest_pending_date).days) if getattr(row, "oldest_pending_date", None) else 0,
            pending_average_days=self._days(getattr(row, "pending_average_days", 0)),
            average_revenue_per_unit=round(_number(row.total_revenue) / _number(row.total_units), 2) if _number(row.total_units) else 0.0,
            delivery_coverage=_percent(row.completed_dn, total),
            latest_pgi_date=_date_text(getattr(row, "latest_pgi_date", None)),
            latest_pod_date=_date_text(getattr(row, "latest_pod_date", None)),
            fastest_delivery_days=self._days(getattr(row, "fastest_delivery", 0)),
            slowest_delivery_days=self._days(getattr(row, "slowest_delivery", 0)),
            same_day_deliveries=int(getattr(row, "same_day_deliveries", 0) or 0),
            next_day_deliveries=int(getattr(row, "next_day_deliveries", 0) or 0),
        )
        
        dashboard.insights = self._basic_insights(dashboard)
        return dashboard

    def _safe_distance(self, warehouse: Any, city: Any) -> DistanceAnalytics:
        try:
            if self._distance is not None:
                return self._distance.calculate(warehouse, city)
        except Exception:
            logger.exception("Distance calculation failed for %s to %s", warehouse, city)
        return DistanceAnalytics(_text(warehouse), _text(city))

    def _apply_extended_analytics(self, session: Session, item: DealerDashboard) -> None:
        identity = item.dealer_code if item.dealer_code != "Unknown" else item.customer_code
        identity = identity if identity != "Unknown" else item.dealer_name
        cache_key = str(identity).lower()
        
        cached = self._extended_cache.get(cache_key)
        if cached:
            for key, value in cached.items():
                setattr(item, key, value)
            self._apply_business_health(item)
            item.insights, item.recommendations = self._business_insights(item)
            return
        
        condition = self._dealer_filter(str(identity))
        values: dict[str, Any] = {}
        
        # Parallel queries
        futures = {}
        futures['dn'] = _executor.submit(self._get_dn_analytics, session, condition)
        futures['monthly'] = _executor.submit(self._get_monthly_analytics, session, condition)
        futures['product'] = _executor.submit(self._get_product_analytics, session, condition)
        futures['division'] = _executor.submit(self._get_division_analytics, session, condition)
        
        for key, future in futures.items():
            try:
                result = future.result(timeout=1)
                if result:
                    values.update(result)
            except Exception:
                pass
        
        self._apply_dealer_rankings(session, item, values)
        
        for key, value in values.items():
            setattr(item, key, value)
        
        self._apply_business_health(item)
        self._extended_cache[cache_key] = values
        item.insights, item.recommendations = self._business_insights(item)

    def _get_dn_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        dn_rows = session.query(
            DeliveryReport.dn_no.label("dn"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.min(DeliveryReport.dn_create_date).label("created"),
            func.max(DeliveryReport.good_issue_date).label("issued"),
            func.max(DeliveryReport.pod_date).label("pod"),
            func.max(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), 1), else_=0)).label("pending"),
        ).filter(condition).group_by(DeliveryReport.dn_no).all()
        
        if not dn_rows:
            return {}
        
        by_revenue = sorted(dn_rows, key=lambda row: _number(row.revenue))
        by_units = sorted(dn_rows, key=lambda row: _number(row.units))
        by_date = sorted(dn_rows, key=lambda row: row.created or date.min)
        pending_rows = [row for row in dn_rows if int(row.pending or 0)]
        
        delivery_days = []
        for row in dn_rows:
            if row.created and row.issued and row.issued >= row.created:
                delivery_days.append((row.issued - row.created).days)
        
        values = {
            "highest_revenue_dn": _text(by_revenue[-1].dn, "N/A"),
            "lowest_revenue_dn": _text(by_revenue[0].dn, "N/A"),
            "highest_unit_dn": _text(by_units[-1].dn, "N/A"),
            "lowest_unit_dn": _text(by_units[0].dn, "N/A"),
            "newest_dn": _text(by_date[-1].dn, "N/A"),
            "fastest_delivery_days": float(min(delivery_days)) if delivery_days else 0.0,
            "slowest_delivery_days": float(max(delivery_days)) if delivery_days else 0.0,
        }
        
        if pending_rows:
            oldest = min(pending_rows, key=lambda row: row.created or date.max)
            ages = [max(0, (date.today() - row.created).days) for row in pending_rows if row.created]
            values.update({
                "oldest_pending_dn": _text(oldest.dn, "N/A"),
                "oldest_pending_days": max(ages) if ages else 0,
                "pending_average_days": round(sum(ages) / len(ages), 2) if ages else 0.0,
                "critical_pending": sum(1 for age in ages if age > 7),
                "overdue_pending": sum(1 for age in ages if age > 14),
            })
        
        return values

    def _get_monthly_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        monthly = session.query(
            func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("month"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(distinct(DeliveryReport.dn_no)).label("dns"),
        ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("month").all()
        
        if not monthly:
            return {}
        
        month_map = {row.month: row for row in monthly}
        current = date.today().strftime("%Y-%m")
        previous_date = date.today().replace(day=1)
        previous_date = (previous_date.replace(year=previous_date.year - 1, month=12) 
                       if previous_date.month == 1 
                       else previous_date.replace(month=previous_date.month - 1))
        previous = previous_date.strftime("%Y-%m")
        
        current_row, previous_row = month_map.get(current), month_map.get(previous)
        current_revenue = _number(current_row.revenue) if current_row else 0.0
        previous_revenue = _number(previous_row.revenue) if previous_row else 0.0
        growth = ((current_revenue - previous_revenue) * 100 / previous_revenue) if previous_revenue else (100.0 if current_revenue else 0.0)
        
        best = max(monthly, key=lambda row: _number(row.revenue))
        worst = min(monthly, key=lambda row: _number(row.revenue))
        
        return {
            "current_month_revenue": round(current_revenue, 2), 
            "previous_month_revenue": round(previous_revenue, 2),
            "monthly_growth": round(growth, 2), 
            "current_month_units": int(current_row.units or 0) if current_row else 0,
            "previous_month_units": int(previous_row.units or 0) if previous_row else 0,
            "current_month_dn": int(current_row.dns or 0) if current_row else 0,
            "previous_month_dn": int(previous_row.dns or 0) if previous_row else 0,
            "best_month": _text(best.month), 
            "worst_month": _text(worst.month), 
            "busiest_month": _text(best.month),
            "revenue_growth_pct": round(growth, 2),
        }

    def _get_product_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        top_product = self._get_top_value(session, condition, DeliveryReport.customer_model)
        top_material = self._get_top_value(session, condition, DeliveryReport.material_no)
        return {
            "top_product": top_product,
            "top_model": top_product,
            "top_material": top_material,
        }

    def _get_division_analytics(self, session: Session, condition: Any) -> dict[str, Any]:
        division_rows = session.query(
            DeliveryReport.division.label("value"),
            func.sum(DeliveryReport.dn_amount).label("revenue"),
        ).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).all()
        
        if not division_rows:
            return {
                "top_division": "Unknown",
                "strongest_product_category": "Unknown",
                "weakest_product_category": "Unknown",
            }
        
        return {
            "top_division": _text(division_rows[0].value),
            "strongest_product_category": _text(division_rows[0].value),
            "weakest_product_category": _text(division_rows[-1].value) if len(division_rows) > 1 else _text(division_rows[0].value),
        }

    def _get_top_value(self, session: Session, condition: Any, column: Any) -> str:
        row = session.query(
            column.label("value"), 
            func.sum(DeliveryReport.dn_amount).label("revenue")
        ).filter(condition, column.isnot(None)).group_by(column).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        return _text(row.value) if row else "Unknown"

    def _apply_dealer_rankings(self, session: Session, item: DealerDashboard, values: dict) -> None:
        cache_key = f"rankings_{item.dealer_code}"
        cached_rankings = self._ranking_cache.get(cache_key)
        if cached_rankings:
            values.update(cached_rankings)
            return
        
        ranking_rows = session.query(
            DeliveryReport.customer_name.label("name"), 
            DeliveryReport.dealer_code.label("code"),
            func.max(DeliveryReport.ship_to_city).label("city"),
            func.coalesce(func.sum(DeliveryReport.dn_amount), 0.0).label("revenue"),
            func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("units"),
            func.count(distinct(DeliveryReport.dn_no)).label("dns"),
            func.avg(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.good_issue_date - DeliveryReport.dn_create_date))).label("delivery"),
            func.count(distinct(case((DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_no)))).label("pod"),
            func.count(distinct(case((or_(DeliveryReport.pending_flag.is_(True), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no)))).label("pending"),
        ).filter(DeliveryReport.customer_name.isnot(None)).group_by(
            DeliveryReport.customer_name, 
            DeliveryReport.dealer_code
        ).all()
        
        target = next(
            (row for row in ranking_rows 
             if _text(row.code, "") == item.dealer_code or _text(row.name, "") == item.dealer_name), 
            None
        )
        
        if not target:
            return
        
        def rank_for(rows: list, key_func, reverse: bool = True) -> int:
            sorted_rows = sorted(rows, key=key_func, reverse=reverse)
            for idx, row in enumerate(sorted_rows, 1):
                if row is target:
                    return idx
            return len(rows)
        
        rankings = {
            "revenue_rank": rank_for(ranking_rows, lambda r: _number(r.revenue), True),
            "unit_rank": rank_for(ranking_rows, lambda r: _number(r.units), True),
            "dn_rank": rank_for(ranking_rows, lambda r: int(r.dns or 0), True),
            "delivery_rank": rank_for(
                ranking_rows, 
                lambda r: self._days(r.delivery) if r.delivery is not None else float("inf"), 
                False
            ),
            "pod_rank": rank_for(ranking_rows, lambda r: _percent(r.pod, r.dns), True),
            "pending_rank": rank_for(ranking_rows, lambda r: _percent(r.pending, r.dns), False),
        }
        
        composite = sorted(
            ranking_rows, 
            key=lambda r: (_number(r.revenue), _percent(r.pod, r.dns)), 
            reverse=True
        )
        rankings["national_rank"] = next(
            (idx for idx, row in enumerate(composite, 1) if row is target), 
            len(composite)
        )
        
        regional = [row for row in ranking_rows if _text(row.city, "").lower() == item.city.lower()]
        regional.sort(key=lambda r: _number(r.revenue), reverse=True)
        rankings["regional_rank"] = next(
            (idx for idx, row in enumerate(regional, 1) if row is target), 
            len(regional) or 1
        )
        
        values.update(rankings)
        self._ranking_cache[cache_key] = rankings

    @staticmethod
    def _apply_business_health(item: DealerDashboard) -> None:
        score = (
            item.delivery_success_pct * 0.35
            + item.pgi_success_pct * 0.20
            + item.pod_success_pct * 0.25
            + max(0.0, 100.0 - item.pending_pct) * 0.20
        )
        item.business_score = round(max(0.0, min(100.0, score)), 1)
        item.overall_status = "Excellent" if score >= 90 else ("Good" if score >= 75 else ("Watch" if score >= 60 else "Needs Attention"))
        trend = "growing" if item.monthly_growth >= 0 else "declining"
        action = "maintain current controls" if item.overall_status in {"Excellent", "Good"} else "prioritize pending DN and POD closure"
        item.executive_summary = (
            f"{item.dealer_name} is {trend} with a {item.business_score:.1f}/100 business score. "
            f"Delivery success is {item.delivery_success_pct:.1f}% and {item.pending_dn} DNs remain pending; {action}."
        )

    @staticmethod
    def _business_insights(item: DealerDashboard) -> tuple[list[str], list[str]]:
        trend = "increasing" if item.monthly_growth >= 0 else "decreasing"
        insights = [
            f"Dealer revenue is {trend} ({item.monthly_growth:+.1f}% month over month).",
            f"Dealer has {item.pending_dn:,} pending DNs and {item.pending_units:,} pending units.",
            f"Pending revenue is PKR {item.pending_revenue:,.2f}.",
            f"Delivery success is {item.delivery_success_pct:.1f}% with average delivery of {item.average_delivery_days:.1f} days.",
            f"POD completion is {item.pod_success_pct:.1f}% and PGI completion is {item.pgi_success_pct:.1f}%.",
            f"Warehouse {item.warehouse} serves this dealer and contributes {item.warehouse_utilization:.1f}% of its unit throughput.",
            f"{item.top_model} is the leading model; top material is {item.top_material}.",
            f"Best revenue month is {item.best_month}; national rank is {item.national_rank or 'N/A'}.",
        ]
        if item.oldest_pending_days:
            insights.append(f"Oldest pending DN {item.oldest_pending_dn} is {item.oldest_pending_days} days old.")
        
        recommendations = []
        if item.overdue_pending:
            recommendations.append(f"Escalate {item.overdue_pending} DNs pending for more than 14 days.")
        if item.pod_success_pct < 90:
            recommendations.append("Prioritize POD collection and closure.")
        if item.pgi_pending_dn:
            recommendations.append(f"Review {item.pgi_pending_dn} DNs awaiting PGI.")
        if not recommendations:
            recommendations.append("Maintain the current delivery and POD control process.")
        
        return insights, recommendations

    @staticmethod
    def _basic_insights(item: DealerDashboard) -> list[str]:
        insights = []
        if item.delivery_success_pct >= 95:
            insights.append("Dealer has excellent delivery performance.")
        if item.pending_pct >= 25:
            insights.append("Dealer has high pending deliveries requiring attention.")
        if item.pod_success_pct < 80:
            insights.append("Dealer has low POD completion.")
        if item.average_delivery_days and item.average_delivery_days <= 2:
            insights.append("Dealer receives deliveries quickly.")
        if item.distance.distance_km is not None:
            insights.append(f"Dealer is {item.distance.distance_km:,.1f} KM from the primary warehouse.")
        return insights

    def _enrich_profile(self, session: Session, item: DealerDashboard) -> None:
        condition = self._dealer_filter(item.dealer_code if item.dealer_code != "Unknown" else item.dealer_name)
        
        month = session.query(
            func.to_char(DeliveryReport.dn_create_date, "YYYY-MM").label("period"), 
            func.sum(DeliveryReport.dn_amount).label("revenue")
        ).filter(condition, DeliveryReport.dn_create_date.isnot(None)).group_by("period").order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).first()
        
        products = session.query(
            DeliveryReport.division.label("category"), 
            func.sum(DeliveryReport.dn_amount).label("revenue")
        ).filter(condition, DeliveryReport.division.isnot(None)).group_by(DeliveryReport.division).order_by(
            func.sum(DeliveryReport.dn_amount).desc()
        ).all()
        
        item.busiest_month = _text(month.period) if month else "Unknown"
        if products:
            item.strongest_product_category = _text(products[0].category)
            item.weakest_product_category = _text(products[-1].category)
            item.insights.append(f"Strongest product category is {item.strongest_product_category}.")
        if item.busiest_month != "Unknown":
            item.insights.append(f"Dealer's busiest month is {item.busiest_month}.")

    # ============================================================
    # PUBLIC API METHODS
    # ============================================================

    def answer_dealer_question(self, question: str, dealer_name: str = "", **kwargs) -> Dict[str, Any]:
        """
        Answer any dealer-related question naturally through WhatsApp.
        
        Examples:
            "What is dealer revenue?"
            "Show pending DNs for Taj Electronics"
            "Tell me about Mian Group Chakwal"
            "Dealer dashboard for Taj Electronics"
            "What is the business score?"
            "Who is the sales manager?"
            "Top product for this dealer"
            "Warehouse distance"
            "National rank"
            "Pending percentage"
            "Delivery success rate"
        """
        return self._question_engine.answer_question(question, dealer_name, **kwargs)

    def get_dealer_dashboard(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        """Get complete dealer dashboard."""
        start_time = time.perf_counter()
        
        identifier = dealer_name or kwargs.get("dealer") or kwargs.get("dealer_code") or kwargs.get("customer_code") or ""
        if not identifier:
            return {"success": False, "error_code": "DEALER_REQUIRED", "message": "Please provide a dealer name or code."}
        
        try:
            with self._session() as session:
                search = self._resolve_dealer(session, str(identifier))
                if search.exception:
                    return {"success": False, "error_code": "SEARCH_ERROR", "message": "Dealer search is temporarily unavailable.", "error": search.exception}
                if not search.dealer_found:
                    return self._suggestion_response(search)
                
                resolved_identity = search.dealer_code or search.customer_code or search.dealer_found
                dashboard_key = str(resolved_identity).lower()
                
                cached_dashboard = self._dashboard_cache.get(dashboard_key)
                if cached_dashboard:
                    return cached_dashboard
                
                rows = self._aggregate_query(session, resolved_identity)
                if not rows:
                    return self._suggestion_response(search)
                
                data = self._row_to_dashboard(rows[0])
                
                try:
                    self._apply_extended_analytics(session, data)
                except Exception:
                    logger.exception("Extended dealer analytics failed")
                    data.insights, data.recommendations = self._business_insights(data)
                
                try:
                    formatted = data.to_whatsapp_message()
                except Exception:
                    formatted = f"Dealer Dashboard\nDealer: {data.dealer_name}\nRevenue: {data.total_revenue:,.2f}\nUnits: {data.total_units:,}\nDN: {data.total_dn:,}"
                
                response = {
                    "success": True, 
                    "data": data, 
                    "dashboard": data, 
                    "search": search, 
                    "whatsapp_message": formatted, 
                    "formatted_response": formatted, 
                    "message": formatted, 
                    "response": formatted,
                    "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
                }
                
                self._dashboard_cache[dashboard_key] = response
                return response
                
        except Exception as error:
            logger.exception("Dealer dashboard query failed")
            return {
                "success": False, 
                "error_code": "DATABASE_UNAVAILABLE", 
                "message": "Dealer database is currently unavailable.", 
                "error": str(error),
                "execution_time_ms": round((time.perf_counter() - start_time) * 1000, 2)
            }

    def diagnose_dealer_search(self, message: str = "", **kwargs: Any) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                result = self._resolve_dealer(session, message or kwargs.get("dealer_name") or kwargs.get("dealer") or "")
                rows = len(self._aggregate_query(session, result.dealer_code or result.customer_code or result.dealer_found)) if result.dealer_found else 0
            
            output = asdict(result)
            output.update({
                "rows_returned": rows, 
                "distance_calculated": False, 
                "distance_source": "Unknown", 
                "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)
            })
            return {"success": result.exception is None, "diagnostic": output}
        except Exception as error:
            logger.exception("Dealer diagnostics failed")
            return {"success": False, "diagnostic": {"original_message": message, "any_exception": str(error), "execution_time_ms": round((time.perf_counter() - started) * 1000, 2)}}

    def get_dealer_profile(self, dealer_name: str = "", **kwargs: Any) -> dict[str, Any]:
        try:
            result = self.get_dealer_dashboard(dealer_name, **kwargs)
            if not result.get("success"):
                return result
            
            with self._session() as session:
                self._enrich_profile(session, result["data"])
            
            result["profile"] = result["data"]
            result["whatsapp_message"] = result["data"].to_whatsapp_message()
            result["message"] = result["whatsapp_message"]
            result["response"] = result["whatsapp_message"]
            return result
        except Exception as error:
            logger.exception("Dealer profile failed")
            return {"success": False, "error_code": "PROFILE_ERROR", "message": "Dealer profile is temporarily unavailable.", "error": str(error)}

    def compare_dealers(self, dealer_names: Any = None, dealer_two: Optional[str] = None, **kwargs: Any) -> dict[str, Any]:
        try:
            values = dealer_names or kwargs.get("dealers") or kwargs.get("dealer1") or []
            if isinstance(values, str):
                values = [values]
            values = list(values)
            second = dealer_two or kwargs.get("dealer2")
            if second:
                values.append(second)
            values = list(dict.fromkeys(str(value) for value in values if value))
            
            if len(values) < 2:
                return {"success": False, "error_code": "TWO_DEALERS_REQUIRED", "message": "Please provide at least two dealers."}
            
            dashboards = []
            for value in values[:10]:
                result = self.get_dealer_dashboard(value)
                if result.get("success"):
                    dashboards.append(result["data"])
            
            if len(dashboards) < 2:
                return {"success": False, "error_code": "DEALERS_NOT_FOUND", "message": "At least two matching dealers are required."}
            
            comparison = DealerComparison(
                dashboards, 
                max(dashboards, key=lambda x: x.total_revenue).dealer_name,
                max(dashboards, key=lambda x: x.total_units).dealer_name, 
                max(dashboards, key=lambda x: x.total_dn).dealer_name,
                min(dashboards, key=lambda x: x.average_delivery_days or float("inf")).dealer_name,
                [
                    f"{max(dashboards, key=lambda x: x.total_revenue).dealer_name} leads revenue.",
                    f"{min(dashboards, key=lambda x: x.pending_pct).dealer_name} has the lowest pending rate."
                ],
            )
            return {"success": True, "data": comparison, "comparison": comparison}
        except Exception as error:
            logger.exception("Dealer comparison failed")
            return {"success": False, "error_code": "COMPARISON_ERROR", "message": "Dealer comparison is temporarily unavailable.", "error": str(error)}

    def _rank(self, sort_by: str, limit: int, bottom: bool) -> dict[str, Any]:
        try:
            cache_key = f"{sort_by.lower()}|{int(limit)}|{int(bottom)}"
            cached = self._ranking_cache.get(cache_key)
            if cached:
                return cached
            
            with self._session() as session:
                items = [self._row_to_dashboard(row, include_distance=False) for row in self._aggregate_query(session)]
            
            key_name = self.SORT_ALIASES.get(sort_by.lower().replace(" ", "_"), "total_revenue")
            naturally_low = key_name in {"average_delivery_days", "pending_pct", "total_revenue", "total_units", "pod_success_pct"}
            reverse = (not bottom and not (key_name in {"average_delivery_days", "pending_pct"})) or (bottom and key_name in {"average_delivery_days", "pending_pct"})
            
            items.sort(
                key=lambda value: getattr(value, key_name, 0) if getattr(value, key_name, None) is not None else 0, 
                reverse=reverse
            )
            
            ranking = DealerRanking(sort_by, "bottom" if bottom else "top", items[: max(1, min(int(limit), 100))])
            response = {"success": True, "data": ranking, "dealers": ranking.dealers, "count": len(ranking.dealers)}
            self._ranking_cache[cache_key] = response
            return response
        except (SQLAlchemyError, ValueError) as error:
            logger.exception("Dealer ranking failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is currently unavailable.", "error": str(error)}

    def get_top_dealers(self, limit: int = 10, sort_by: str = "revenue", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), False)
        except Exception as error:
            logger.exception("Top dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def get_bottom_dealers(self, limit: int = 10, sort_by: str = "highest_pending", **kwargs: Any) -> dict[str, Any]:
        try:
            return self._rank(str(kwargs.get("metric", sort_by)), int(kwargs.get("count", limit)), True)
        except Exception as error:
            logger.exception("Bottom dealer request failed")
            return {"success": False, "error_code": "RANKING_ERROR", "message": "Dealer ranking is temporarily unavailable.", "error": str(error)}

    def health_check(self) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with self._session() as session:
                rows = session.query(func.count(DeliveryReport.id)).scalar() or 0
            return {
                "healthy": True, 
                "service": self._service_name, 
                "version": self._version, 
                "database": "connected", 
                "records": int(rows), 
                "latency_ms": round((time.perf_counter() - started) * 1000, 2), 
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as error:
            logger.exception("Dealer analytics health check failed")
            return {
                "healthy": False, 
                "service": self._service_name, 
                "version": self._version, 
                "database": "disconnected", 
                "error": str(error), 
                "timestamp": datetime.utcnow().isoformat()
            }

    def validation_query(self) -> dict[str, Any]:
        try:
            with self._session() as session:
                records = session.query(
                    func.count(distinct(func.coalesce(DeliveryReport.dealer_code, DeliveryReport.customer_code, DeliveryReport.customer_name)))
                ).scalar() or 0
            return {"success": True, "records": int(records), "error": None}
        except Exception as error:
            return {"success": False, "records": 0, "error": str(error)}

    def get_service_metadata(self) -> dict[str, Any]:
        return {
            "service_name": self._service_name, 
            "version": self._version, 
            "status": "DEGRADED" if self._initialization_errors else "READY", 
            "source": "PostgreSQL DeliveryReport", 
            "distance_provider": "OpenRouteService" if self._distance and self._distance._ors else "geopy great-circle", 
            "startup_time": self._startup_time, 
            "initialization_errors": self._initialization_errors
        }


_service: Optional[DealerAnalyticsService] = None
_service_lock = threading.Lock()


def get_dealer_analytics_service() -> DealerAnalyticsService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                try:
                    _service = DealerAnalyticsService()
                except Exception:
                    logger.exception("DealerAnalyticsService initialization failed")
                    _service = DealerAnalyticsService.__new__(DealerAnalyticsService)
                    _service._service_name = "dealer_analytics"
                    _service._version = "5.0.0-intelligence-degraded"
                    _service._startup_time = datetime.utcnow().isoformat()
                    _service._initialization_errors = ["Service initialized in emergency degraded mode"]
                    _service._coordinates = CityCoordinateService()
                    _service._distance = None
                    _service._dealer_cache = TTLCache(maxsize=4096, ttl=CACHE_TTL)
                    _service._candidate_cache = TTLCache(maxsize=1, ttl=1800)
                    _service._extended_cache = TTLCache(maxsize=4096, ttl=1800)
                    _service._dashboard_cache = TTLCache(maxsize=4096, ttl=600)
                    _service._ranking_cache = TTLCache(maxsize=128, ttl=600)
                    _service._search_lock = threading.RLock()
                    _service._last_diagnostic = {}
                    _service._aggregate_cache = TTLCache(maxsize=1024, ttl=300)
                    _service._question_engine = DealerQuestionEngine(_service)
    return _service


__all__ = [
    "DealerAnalyticsService", 
    "DealerDashboard", 
    "DealerComparison", 
    "DealerRanking", 
    "DealerSearchResult", 
    "DistanceAnalytics", 
    "CityCoordinateService", 
    "get_dealer_analytics_service",
    "DealerQuestionEngine",
    "DealerQuestion",
    "DealerAnswer",
    "DealerIntent",
    "MetricType",
    "IntentResolver"
]
