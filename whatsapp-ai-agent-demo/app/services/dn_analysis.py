"""
File: app/services/ai_provider_service.py
Version: 11.0 - ENTERPRISE WITH SPECIALIZED ROUTES
Purpose: COMPLETE ORCHESTRATOR with dedicated routes for each service.
         Each route has its own intent detection criteria and routing logic.
         NO SQL, NO business logic, NO formatting logic - Pure Orchestration.
         
SPECIALIZED ROUTES:
1. DN Route - DN numbers, pending DNs, pending PGI, pending POD
2. Dealer Route - Dealer dashboards, rankings, comparisons, suggestions
3. Warehouse Route - Warehouse dashboards, warehouse rankings
4. City Route - City dashboards, city rankings
5. Product Route - Product dashboards, product rankings
6. National KPI Route - Executive dashboards, national metrics
7. Groq Route - Conversational AI, general queries, help
8. Analytics Route - Cross-domain analytics and insights
"""

import logging
import os
import threading
import time
import importlib
import inspect
import re
import sys
import asyncio
from typing import Optional, Dict, Any, List, Tuple, Callable, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from functools import wraps
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

# ============================================================
# TENACITY FOR RETRY LOGIC
# ============================================================

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    TENACITY_AVAILABLE = True
except ImportError:
    TENACITY_AVAILABLE = False
    logger.warning("⚠️ Tenacity not installed. Install with: pip install tenacity>=8.5.0")

# ============================================================
# IMPORTS WITH FALLBACK
# ============================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

# ============================================================
# ROUTE TYPES - ENUM FOR ROUTE IDENTIFICATION
# ============================================================

class RouteType(Enum):
    """Enumeration of all available routes in the system"""
    DN = "dn"
    DEALER = "dealer"
    WAREHOUSE = "warehouse"
    CITY = "city"
    PRODUCT = "product"
    NATIONAL_KPI = "national_kpi"
    GROQ = "groq"
    ANALYTICS = "analytics"
    UNKNOWN = "unknown"
    
    def __str__(self):
        return self.value

# ============================================================
# ROUTING DECISION - ENHANCED WITH ROUTE TYPE
# ============================================================

@dataclass
class RoutingDecision:
    """Enhanced routing decision with route type and full context"""
    # Core fields
    intent: str
    route_type: RouteType
    service_key: str
    method: str
    
    # Entity fields
    entity: Optional[str] = None
    entity2: Optional[str] = None
    entity_type: Optional[str] = None  # "dn", "dealer", "warehouse", "city", "product"
    
    # Confidence and metadata
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    
    # Additional data
    suggestions: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    matched_patterns: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "route_type": str(self.route_type),
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "entity2": self.entity2,
            "entity_type": self.entity_type,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message,
            "suggestions": self.suggestions,
            "metadata": self.metadata,
            "matched_patterns": self.matched_patterns
        }

# ============================================================
# SERVICE REGISTRY - WITH COMPLETE SERVICE DEFINITIONS
# ============================================================

class ServiceRegistry:
    """Complete service registry with all services and their methods"""
    
    SERVICES = {
        # ============================================================
        # DN ROUTE SERVICE
        # ============================================================
        "dn": {
            "module": "app.services.dn_analysis",
            "class_name": "DNAnalysisService",
            "route_type": RouteType.DN,
            "expected_methods": [
                "get_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "health_check",
                "validation_query",
                "get_service_metadata",
                "get_dn_details",
                "get_dn_status",
                "get_dn_history",
                "search_dns",
                "get_dn_summary"
            ],
            "description": "DN Analytics Service - Complete DN tracking and management",
        },
        
        # ============================================================
        # DEALER ROUTE SERVICE
        # ============================================================
        "dealer": {
            "module": "app.services.dealer_analytics_service",
            "class_name": "DealerAnalyticsService",
            "route_type": RouteType.DEALER,
            "expected_methods": [
                "get_dealer_dashboard",
                "get_dealer_profile",
                "compare_dealers",
                "get_top_dealers",
                "get_bottom_dealers",
                "get_dealer_performance",
                "get_dealer_trends",
                "get_dealer_kpis",
                "search_dealers",
                "suggest_dealers",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Dealer Analytics Service - Complete dealer performance tracking",
        },
        
        # ============================================================
        # WAREHOUSE ROUTE SERVICE
        # ============================================================
        "warehouse": {
            "module": "app.services.warehouse_service",
            "class_name": "WarehouseAnalyticsService",
            "route_type": RouteType.WAREHOUSE,
            "expected_methods": [
                "get_warehouse_dashboard",
                "get_warehouse_performance",
                "get_top_warehouses",
                "get_bottom_warehouses",
                "get_warehouse_kpis",
                "search_warehouses",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Warehouse Analytics Service - Complete warehouse operations tracking",
        },
        
        # ============================================================
        # CITY ROUTE SERVICE
        # ============================================================
        "city": {
            "module": "app.services.city_service",
            "class_name": "CityAnalyticsService",
            "route_type": RouteType.CITY,
            "expected_methods": [
                "get_city_dashboard",
                "get_city_performance",
                "get_top_cities",
                "get_bottom_cities",
                "get_city_kpis",
                "search_cities",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "City Analytics Service - Complete city-level performance tracking",
        },
        
        # ============================================================
        # PRODUCT ROUTE SERVICE
        # ============================================================
        "product": {
            "module": "app.services.product_service",
            "class_name": "ProductAnalyticsService",
            "route_type": RouteType.PRODUCT,
            "expected_methods": [
                "get_product_dashboard",
                "get_product_performance",
                "get_top_products",
                "get_bottom_products",
                "get_product_kpis",
                "search_products",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "Product Analytics Service - Complete product performance tracking",
        },
        
        # ============================================================
        # NATIONAL KPI ROUTE SERVICE
        # ============================================================
        "national_kpi": {
            "module": "app.services.national_kpi_service",
            "class_name": "NationalKPIService",
            "route_type": RouteType.NATIONAL_KPI,
            "expected_methods": [
                "get_national_kpi_dashboard",
                "get_delivery_kpis",
                "get_warehouse_kpis",
                "get_dealer_kpis",
                "get_executive_summary",
                "get_trend_analysis",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ],
            "description": "National KPI Service - Executive dashboard and country-wide metrics",
        },
        
        # ============================================================
        # GROQ ROUTE SERVICE
        # ============================================================
        "groq": {
            "module": "app.services.groq_service",
            "class_name": "GroqService",
            "route_type": RouteType.GROQ,
            "expected_methods": [
                "process_query",
                "get_response",
                "classify_intent",
                "generate_insights",
                "health_check"
            ],
            "description": "Groq AI Service - Conversational AI and natural language processing",
        },
        
        # ============================================================
        # ANALYTICS ROUTE SERVICE (Cross-domain)
        # ============================================================
        "analytics": {
            "module": "app.services.analytics_service",
            "class_name": "AnalyticsService",
            "route_type": RouteType.ANALYTICS,
            "expected_methods": [
                "get_cross_domain_insights",
                "get_dashboard_summary",
                "get_revenue_analysis",
                "get_performance_analysis",
                "get_trend_analysis",
                "health_check",
                "get_service_metadata"
            ],
            "description": "Analytics Service - Cross-domain analytics and insights",
        }
    }
    
    def __init__(self):
        self._services = self.SERVICES.copy()
        self._instance_cache = {}
        self._lock = threading.RLock()
        self._service_health = {}
    
    def get_service_instance(self, service_key: str):
        """Get service instance with caching"""
        if service_key in self._instance_cache:
            return self._instance_cache[service_key]
        
        with self._lock:
            if service_key in self._instance_cache:
                return self._instance_cache[service_key]
            
            try:
                service_def = self._services.get(service_key)
                if not service_def:
                    logger.error(f"Service '{service_key}' not registered")
                    return None
                
                module = importlib.import_module(service_def["module"])
                cls = getattr(module, service_def["class_name"])
                instance = cls()
                
                # Validate expected methods
                for method in service_def.get("expected_methods", []):
                    if not hasattr(instance, method):
                        logger.warning(f"⚠️ Service '{service_key}' missing method: {method}")
                
                self._instance_cache[service_key] = instance
                self._service_health[service_key] = {
                    "loaded": True,
                    "class": service_def["class_name"],
                    "module": service_def["module"],
                    "route_type": str(service_def["route_type"])
                }
                logger.info(f"✅ Service '{service_key}' initialized from {service_def['module']}")
                return instance
            except ImportError as e:
                logger.error(f"❌ Failed to import service '{service_key}': {e}")
                return None
            except Exception as e:
                logger.error(f"❌ Failed to load service '{service_key}': {e}")
                return None
    
    def get_route_type(self, service_key: str) -> Optional[RouteType]:
        """Get route type for a service"""
        service_def = self._services.get(service_key)
        if service_def:
            return service_def["route_type"]
        return None
    
    def is_service_ready(self, service_key: str) -> bool:
        instance = self.get_service_instance(service_key)
        return instance is not None
    
    def get_service_health(self, service_key: str) -> Dict[str, Any]:
        return self._service_health.get(service_key, {"loaded": False})
    
    def get_all_services(self) -> Dict[str, Any]:
        return self._services

# ============================================================
# ROUTE SPECIFIC INTENT DETECTION ENGINES
# ============================================================

class BaseRouteDetector:
    """Base class for route-specific intent detection"""
    
    def __init__(self, route_type: RouteType, service_key: str):
        self.route_type = route_type
        self.service_key = service_key
        self.patterns = []
        self.methods_map = {}
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Detect intent for this route - Override in subclasses"""
        return None


class DNRouteDetector(BaseRouteDetector):
    """DN Route: Detects DN numbers, pending DNs, pending PGI, pending POD"""
    
    def __init__(self):
        super().__init__(RouteType.DN, "dn")
        
        # DN Pattern - 8 to 12 digits
        self.dn_pattern = re.compile(r'\b(\d{8,12})\b')
        
        # Pending patterns
        self.pending_dn_pattern = re.compile(
            r'(?:pending|open|outstanding|waiting)\s*(?:dn|dns|delivery|deliveries)',
            re.IGNORECASE
        )
        self.pending_pgi_pattern = re.compile(
            r'(?:pending|open|outstanding)\s*(?:pgi|goods\s*issue)',
            re.IGNORECASE
        )
        self.pending_pod_pattern = re.compile(
            r'(?:pending|open|outstanding)\s*(?:pod|proof\s*of\s*delivery)',
            re.IGNORECASE
        )
        
        # Search patterns
        self.dn_search_pattern = re.compile(
            r'(?:search|find|lookup|show)\s*(?:dn|delivery\s*note|delivery)\s*(?:for|number)?\s*([a-zA-Z0-9\s]+)',
            re.IGNORECASE
        )
        
        # DN summary patterns
        self.dn_summary_pattern = re.compile(
            r'(?:summary|overview|stats|statistics)\s*(?:dn|delivery|deliveries)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """DN route detection logic"""
        
        # ============================================================
        # 1. DIRECT DN NUMBER (highest priority)
        # ============================================================
        # Check if entire message is a DN number
        if self._is_dn_number(message):
            dn_number = re.sub(r'\D', '', message)
            return RoutingDecision(
                intent="dn_lookup",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_dn_dashboard",
                entity=dn_number,
                entity_type="dn",
                confidence=1.0,
                needs_groq=False,
                reason="Direct DN number detected",
                original_message=message,
                matched_patterns=["direct_dn_number"]
            )
        
        # Search for DN number in message
        dn_match = self.dn_pattern.search(message)
        if dn_match:
            dn_number = dn_match.group(1)
            return RoutingDecision(
                intent="dn_lookup",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_dn_dashboard",
                entity=dn_number,
                entity_type="dn",
                confidence=1.0,
                needs_groq=False,
                reason="DN number extracted from message",
                original_message=message,
                matched_patterns=["dn_pattern"]
            )
        
        # ============================================================
        # 2. PENDING DN QUERIES
        # ============================================================
        if self.pending_dn_pattern.search(normalized):
            return RoutingDecision(
                intent="pending_dn",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_pending_dns",
                confidence=0.98,
                needs_groq=False,
                reason="Pending DN query detected",
                original_message=message,
                matched_patterns=["pending_dn_pattern"]
            )
        
        if self.pending_pgi_pattern.search(normalized):
            return RoutingDecision(
                intent="pending_pgi",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_pending_pgi",
                confidence=0.98,
                needs_groq=False,
                reason="Pending PGI query detected",
                original_message=message,
                matched_patterns=["pending_pgi_pattern"]
            )
        
        if self.pending_pod_pattern.search(normalized):
            return RoutingDecision(
                intent="pending_pod",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_pending_pod",
                confidence=0.98,
                needs_groq=False,
                reason="Pending POD query detected",
                original_message=message,
                matched_patterns=["pending_pod_pattern"]
            )
        
        # ============================================================
        # 3. DN SEARCH
        # ============================================================
        search_match = self.dn_search_pattern.search(message)
        if search_match:
            search_term = search_match.group(1).strip()
            if search_term:
                return RoutingDecision(
                    intent="dn_search",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="search_dns",
                    entity=search_term,
                    entity_type="search",
                    confidence=0.85,
                    needs_groq=False,
                    reason="DN search query detected",
                    original_message=message,
                    matched_patterns=["dn_search_pattern"]
                )
        
        # ============================================================
        # 4. DN SUMMARY
        # ============================================================
        if self.dn_summary_pattern.search(normalized):
            return RoutingDecision(
                intent="dn_summary",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_dn_summary",
                confidence=0.85,
                needs_groq=False,
                reason="DN summary query detected",
                original_message=message,
                matched_patterns=["dn_summary_pattern"]
            )
        
        return None
    
    def _is_dn_number(self, text: str) -> bool:
        """Check if text is a valid DN number"""
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12


class DealerRouteDetector(BaseRouteDetector):
    """Dealer Route: Detects dealer names, rankings, comparisons, suggestions"""
    
    def __init__(self):
        super().__init__(RouteType.DEALER, "dealer")
        
        # Dealer dashboard patterns
        self.dealer_dashboard_pattern = re.compile(
            r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics|performance|kpi)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.dealer_pattern = re.compile(
            r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking patterns
        self.top_dealers_pattern = re.compile(
            r'(?:top|best|highest|leading)\s*(?:\d+\s*)?(?:dealers?|sellers?|performers?)',
            re.IGNORECASE
        )
        self.bottom_dealers_pattern = re.compile(
            r'(?:bottom|worst|lowest|least)\s*(?:\d+\s*)?(?:dealers?|sellers?|performers?)',
            re.IGNORECASE
        )
        
        # Comparison patterns
        self.comparison_pattern = re.compile(
            r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)',
            re.IGNORECASE
        )
        
        # Dealer search patterns
        self.dealer_search_pattern = re.compile(
            r'(?:search|find|lookup)\s+(?:dealer|dealers?)\s+(?:for|named)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Dealer KPI patterns
        self.dealer_kpi_pattern = re.compile(
            r'(?:kpi|metrics|performance indicators)\s+(?:of|for)?\s+(?:dealer|dealers?)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Dealer route detection logic"""
        
        # ============================================================
        # 1. DEALER RANKINGS
        # ============================================================
        if self.top_dealers_pattern.search(normalized):
            return RoutingDecision(
                intent="top_dealers",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_top_dealers",
                confidence=0.95,
                needs_groq=False,
                reason="Top dealers query detected",
                original_message=message,
                matched_patterns=["top_dealers_pattern"]
            )
        
        if self.bottom_dealers_pattern.search(normalized):
            return RoutingDecision(
                intent="bottom_dealers",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_bottom_dealers",
                confidence=0.95,
                needs_groq=False,
                reason="Bottom dealers query detected",
                original_message=message,
                matched_patterns=["bottom_dealers_pattern"]
            )
        
        # ============================================================
        # 2. DEALER COMPARISON
        # ============================================================
        comparison_match = self.comparison_pattern.search(message)
        if comparison_match:
            entity1 = comparison_match.group(1).strip()
            entity2 = comparison_match.group(2).strip()
            if entity1 and entity2 and len(entity1) > 1 and len(entity2) > 1:
                return RoutingDecision(
                    intent="comparison",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="compare_dealers",
                    entity=entity1,
                    entity2=entity2,
                    entity_type="comparison",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"Dealer comparison: {entity1} vs {entity2}",
                    original_message=message,
                    matched_patterns=["comparison_pattern"]
                )
        
        # ============================================================
        # 3. DEALER DASHBOARD (specific dealer)
        # ============================================================
        dealer_name = None
        
        # Check dashboard pattern first
        dashboard_match = self.dealer_dashboard_pattern.search(message)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
        
        # Check generic dealer pattern
        if not dealer_name:
            dealer_match = self.dealer_pattern.search(message)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
        
        # Check if message is short and looks like a dealer name
        if not dealer_name:
            words = message.split()
            if 1 <= len(words) <= 3 and len(message) > 2:
                if not re.match(r'^\d+$', message):
                    dealer_name = message
        
        if dealer_name:
            # Clean up dealer name
            dealer_name = self._clean_dealer_name(dealer_name)
            if dealer_name and len(dealer_name) > 1:
                return RoutingDecision(
                    intent="dealer_dashboard",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_dealer_dashboard",
                    entity=dealer_name,
                    entity_type="dealer",
                    confidence=0.95,
                    needs_groq=False,
                    reason=f"Dealer dashboard for: {dealer_name}",
                    original_message=message,
                    matched_patterns=["dealer_dashboard_pattern" if dashboard_match else "dealer_pattern"]
                )
        
        # ============================================================
        # 4. DEALER SEARCH
        # ============================================================
        search_match = self.dealer_search_pattern.search(message)
        if search_match:
            search_term = search_match.group(1).strip()
            if search_term:
                return RoutingDecision(
                    intent="dealer_search",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="search_dealers",
                    entity=search_term,
                    entity_type="search",
                    confidence=0.85,
                    needs_groq=False,
                    reason=f"Dealer search: {search_term}",
                    original_message=message,
                    matched_patterns=["dealer_search_pattern"]
                )
        
        # ============================================================
        # 5. DEALER KPI
        # ============================================================
        kpi_match = self.dealer_kpi_pattern.search(message)
        if kpi_match:
            dealer_name = kpi_match.group(1).strip()
            if dealer_name:
                return RoutingDecision(
                    intent="dealer_kpi",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_dealer_kpis",
                    entity=dealer_name,
                    entity_type="dealer",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"Dealer KPI for: {dealer_name}",
                    original_message=message,
                    matched_patterns=["dealer_kpi_pattern"]
                )
        
        return None
    
    def _clean_dealer_name(self, name: str) -> str:
        """Clean dealer name by removing common words"""
        if not name:
            return ""
        
        # Remove common prefixes/suffixes
        clean = re.sub(
            r'\b(?:dealer|about|for|of|show|get|view|display|give|me|company|customer|'
            r'dashboard|profile|summary|overview|info|information|details|status|'
            r'statistics|performance|kpi|the|a|an|and|or)\b',
            '',
            name,
            flags=re.IGNORECASE
        ).strip()
        
        # Remove extra spaces
        clean = re.sub(r'\s+', ' ', clean).strip()
        
        return clean


class WarehouseRouteDetector(BaseRouteDetector):
    """Warehouse Route: Detects warehouse names and warehouse analytics"""
    
    def __init__(self):
        super().__init__(RouteType.WAREHOUSE, "warehouse")
        
        # Warehouse patterns
        self.warehouse_pattern = re.compile(
            r'(?:warehouse|wh|depot|distribution\s*center)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking patterns
        self.top_warehouses_pattern = re.compile(
            r'(?:top|best|highest)\s*(?:\d+\s*)?(?:warehouses?|wh|depots?)',
            re.IGNORECASE
        )
        self.bottom_warehouses_pattern = re.compile(
            r'(?:bottom|worst|lowest)\s*(?:\d+\s*)?(?:warehouses?|wh|depots?)',
            re.IGNORECASE
        )
        
        # Performance patterns
        self.warehouse_performance_pattern = re.compile(
            r'(?:performance|efficiency|productivity)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # KPI patterns
        self.warehouse_kpi_pattern = re.compile(
            r'(?:kpi|metrics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Warehouse route detection logic"""
        
        # ============================================================
        # 1. WAREHOUSE RANKINGS
        # ============================================================
        if self.top_warehouses_pattern.search(normalized):
            return RoutingDecision(
                intent="top_warehouses",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_top_warehouses",
                confidence=0.95,
                needs_groq=False,
                reason="Top warehouses query detected",
                original_message=message,
                matched_patterns=["top_warehouses_pattern"]
            )
        
        if self.bottom_warehouses_pattern.search(normalized):
            return RoutingDecision(
                intent="bottom_warehouses",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_bottom_warehouses",
                confidence=0.95,
                needs_groq=False,
                reason="Bottom warehouses query detected",
                original_message=message,
                matched_patterns=["bottom_warehouses_pattern"]
            )
        
        # ============================================================
        # 2. WAREHOUSE DASHBOARD
        # ============================================================
        warehouse_match = self.warehouse_pattern.search(message)
        if warehouse_match:
            warehouse_name = warehouse_match.group(1).strip()
            if warehouse_name and len(warehouse_name) > 1:
                return RoutingDecision(
                    intent="warehouse_dashboard",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_warehouse_dashboard",
                    entity=warehouse_name,
                    entity_type="warehouse",
                    confidence=0.95,
                    needs_groq=False,
                    reason=f"Warehouse dashboard for: {warehouse_name}",
                    original_message=message,
                    matched_patterns=["warehouse_pattern"]
                )
        
        # ============================================================
        # 3. WAREHOUSE PERFORMANCE
        # ============================================================
        perf_match = self.warehouse_performance_pattern.search(message)
        if perf_match:
            warehouse_name = perf_match.group(1).strip()
            if warehouse_name:
                return RoutingDecision(
                    intent="warehouse_performance",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_warehouse_performance",
                    entity=warehouse_name,
                    entity_type="warehouse",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"Warehouse performance for: {warehouse_name}",
                    original_message=message,
                    matched_patterns=["warehouse_performance_pattern"]
                )
        
        # ============================================================
        # 4. WAREHOUSE KPI
        # ============================================================
        kpi_match = self.warehouse_kpi_pattern.search(message)
        if kpi_match:
            warehouse_name = kpi_match.group(1).strip()
            if warehouse_name:
                return RoutingDecision(
                    intent="warehouse_kpi",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_warehouse_kpis",
                    entity=warehouse_name,
                    entity_type="warehouse",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"Warehouse KPI for: {warehouse_name}",
                    original_message=message,
                    matched_patterns=["warehouse_kpi_pattern"]
                )
        
        return None


class CityRouteDetector(BaseRouteDetector):
    """City Route: Detects city names and city-level analytics"""
    
    def __init__(self):
        super().__init__(RouteType.CITY, "city")
        
        # City patterns
        self.city_pattern = re.compile(
            r'(?:city|in|at|location)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.city_dashboard_pattern = re.compile(
            r'(?:dashboard|performance|analytics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking patterns
        self.top_cities_pattern = re.compile(
            r'(?:top|best|highest)\s*(?:\d+\s*)?(?:cities?)',
            re.IGNORECASE
        )
        self.bottom_cities_pattern = re.compile(
            r'(?:bottom|worst|lowest)\s*(?:\d+\s*)?(?:cities?)',
            re.IGNORECASE
        )
        
        # KPI patterns
        self.city_kpi_pattern = re.compile(
            r'(?:kpi|metrics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """City route detection logic"""
        
        # ============================================================
        # 1. CITY RANKINGS
        # ============================================================
        if self.top_cities_pattern.search(normalized):
            return RoutingDecision(
                intent="top_cities",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_top_cities",
                confidence=0.95,
                needs_groq=False,
                reason="Top cities query detected",
                original_message=message,
                matched_patterns=["top_cities_pattern"]
            )
        
        if self.bottom_cities_pattern.search(normalized):
            return RoutingDecision(
                intent="bottom_cities",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_bottom_cities",
                confidence=0.95,
                needs_groq=False,
                reason="Bottom cities query detected",
                original_message=message,
                matched_patterns=["bottom_cities_pattern"]
            )
        
        # ============================================================
        # 2. CITY DASHBOARD
        # ============================================================
        city_match = self.city_pattern.search(message)
        city_name = None
        if city_match:
            city_name = city_match.group(1).strip()
        
        if not city_name:
            dashboard_match = self.city_dashboard_pattern.search(message)
            if dashboard_match:
                city_name = dashboard_match.group(1).strip()
        
        if city_name and len(city_name) > 1:
            return RoutingDecision(
                intent="city_dashboard",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_city_dashboard",
                entity=city_name,
                entity_type="city",
                confidence=0.95,
                needs_groq=False,
                reason=f"City dashboard for: {city_name}",
                original_message=message,
                matched_patterns=["city_pattern" if city_match else "city_dashboard_pattern"]
            )
        
        # ============================================================
        # 3. CITY KPI
        # ============================================================
        kpi_match = self.city_kpi_pattern.search(message)
        if kpi_match:
            city_name = kpi_match.group(1).strip()
            if city_name:
                return RoutingDecision(
                    intent="city_kpi",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_city_kpis",
                    entity=city_name,
                    entity_type="city",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"City KPI for: {city_name}",
                    original_message=message,
                    matched_patterns=["city_kpi_pattern"]
                )
        
        return None


class ProductRouteDetector(BaseRouteDetector):
    """Product Route: Detects product names and product analytics"""
    
    def __init__(self):
        super().__init__(RouteType.PRODUCT, "product")
        
        # Product patterns
        self.product_pattern = re.compile(
            r'(?:product|model|material|item|sku|part)\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        self.product_dashboard_pattern = re.compile(
            r'(?:dashboard|performance|analytics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
        
        # Ranking patterns
        self.top_products_pattern = re.compile(
            r'(?:top|best|highest)\s*(?:\d+\s*)?(?:products?|items?|materials?)',
            re.IGNORECASE
        )
        self.bottom_products_pattern = re.compile(
            r'(?:bottom|worst|lowest)\s*(?:\d+\s*)?(?:products?|items?|materials?)',
            re.IGNORECASE
        )
        
        # KPI patterns
        self.product_kpi_pattern = re.compile(
            r'(?:kpi|metrics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Product route detection logic"""
        
        # ============================================================
        # 1. PRODUCT RANKINGS
        # ============================================================
        if self.top_products_pattern.search(normalized):
            return RoutingDecision(
                intent="top_products",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_top_products",
                confidence=0.95,
                needs_groq=False,
                reason="Top products query detected",
                original_message=message,
                matched_patterns=["top_products_pattern"]
            )
        
        if self.bottom_products_pattern.search(normalized):
            return RoutingDecision(
                intent="bottom_products",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_bottom_products",
                confidence=0.95,
                needs_groq=False,
                reason="Bottom products query detected",
                original_message=message,
                matched_patterns=["bottom_products_pattern"]
            )
        
        # ============================================================
        # 2. PRODUCT DASHBOARD
        # ============================================================
        product_match = self.product_pattern.search(message)
        product_name = None
        if product_match:
            product_name = product_match.group(1).strip()
        
        if not product_name:
            dashboard_match = self.product_dashboard_pattern.search(message)
            if dashboard_match:
                product_name = dashboard_match.group(1).strip()
        
        if product_name and len(product_name) > 1:
            return RoutingDecision(
                intent="product_dashboard",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_product_dashboard",
                entity=product_name,
                entity_type="product",
                confidence=0.95,
                needs_groq=False,
                reason=f"Product dashboard for: {product_name}",
                original_message=message,
                matched_patterns=["product_pattern" if product_match else "product_dashboard_pattern"]
            )
        
        # ============================================================
        # 3. PRODUCT KPI
        # ============================================================
        kpi_match = self.product_kpi_pattern.search(message)
        if kpi_match:
            product_name = kpi_match.group(1).strip()
            if product_name:
                return RoutingDecision(
                    intent="product_kpi",
                    route_type=self.route_type,
                    service_key=self.service_key,
                    method="get_product_kpis",
                    entity=product_name,
                    entity_type="product",
                    confidence=0.90,
                    needs_groq=False,
                    reason=f"Product KPI for: {product_name}",
                    original_message=message,
                    matched_patterns=["product_kpi_pattern"]
                )
        
        return None


class NationalKPIRouteDetector(BaseRouteDetector):
    """National KPI Route: Detects executive and national-level queries"""
    
    def __init__(self):
        super().__init__(RouteType.NATIONAL_KPI, "national_kpi")
        
        # National KPI patterns
        self.national_kpi_pattern = re.compile(
            r'(?:national|pakistan|country|overall|executive|headquarters|global)\s*(?:kpi|dashboard|summary|performance|metrics?|analytics)',
            re.IGNORECASE
        )
        self.executive_summary_pattern = re.compile(
            r'(?:executive|management|leadership)\s*(?:summary|dashboard|overview)',
            re.IGNORECASE
        )
        self.trend_analysis_pattern = re.compile(
            r'(?:trend|trends|forecast|projection)\s*(?:analysis|report)',
            re.IGNORECASE
        )
        
        # Specific KPI patterns
        self.delivery_kpi_pattern = re.compile(
            r'(?:delivery|dn)\s*(?:kpi|metrics|performance)',
            re.IGNORECASE
        )
        self.warehouse_kpi_pattern = re.compile(
            r'(?:warehouse|wh)\s*(?:kpi|metrics|performance)',
            re.IGNORECASE
        )
        self.dealer_kpi_pattern = re.compile(
            r'(?:dealer|sales)\s*(?:kpi|metrics|performance)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """National KPI route detection logic"""
        
        # ============================================================
        # 1. NATIONAL KPI DASHBOARD
        # ============================================================
        if self.national_kpi_pattern.search(normalized):
            return RoutingDecision(
                intent="national_kpi",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_national_kpi_dashboard",
                confidence=0.98,
                needs_groq=False,
                reason="National KPI query detected",
                original_message=message,
                matched_patterns=["national_kpi_pattern"]
            )
        
        # ============================================================
        # 2. EXECUTIVE SUMMARY
        # ============================================================
        if self.executive_summary_pattern.search(normalized):
            return RoutingDecision(
                intent="executive_summary",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_executive_summary",
                confidence=0.95,
                needs_groq=False,
                reason="Executive summary query detected",
                original_message=message,
                matched_patterns=["executive_summary_pattern"]
            )
        
        # ============================================================
        # 3. TREND ANALYSIS
        # ============================================================
        if self.trend_analysis_pattern.search(normalized):
            return RoutingDecision(
                intent="trend_analysis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_trend_analysis",
                confidence=0.90,
                needs_groq=False,
                reason="Trend analysis query detected",
                original_message=message,
                matched_patterns=["trend_analysis_pattern"]
            )
        
        # ============================================================
        # 4. SPECIFIC KPI QUERIES
        # ============================================================
        if self.delivery_kpi_pattern.search(normalized):
            return RoutingDecision(
                intent="delivery_kpis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_delivery_kpis",
                confidence=0.90,
                needs_groq=False,
                reason="Delivery KPIs query detected",
                original_message=message,
                matched_patterns=["delivery_kpi_pattern"]
            )
        
        if self.warehouse_kpi_pattern.search(normalized):
            return RoutingDecision(
                intent="warehouse_kpis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_warehouse_kpis",
                confidence=0.90,
                needs_groq=False,
                reason="Warehouse KPIs query detected",
                original_message=message,
                matched_patterns=["warehouse_kpi_pattern"]
            )
        
        if self.dealer_kpi_pattern.search(normalized):
            return RoutingDecision(
                intent="dealer_kpis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_dealer_kpis",
                confidence=0.90,
                needs_groq=False,
                reason="Dealer KPIs query detected",
                original_message=message,
                matched_patterns=["dealer_kpi_pattern"]
            )
        
        return None


class GroqRouteDetector(BaseRouteDetector):
    """Groq Route: Detects conversational, help, and general AI queries"""
    
    def __init__(self):
        super().__init__(RouteType.GROQ, "groq")
        
        # Help patterns
        self.help_pattern = re.compile(
            r'(?:help|menu|commands|what can you do|available commands|how to use|'
            r'capabilities|features|guide|tutorial|support)',
            re.IGNORECASE
        )
        
        # Greeting patterns
        self.greeting_pattern = re.compile(
            r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings|'
            r'what\'s up|sup|yo|hola|namaste|salaam)',
            re.IGNORECASE
        )
        
        # Conversational patterns
        self.conversational_pattern = re.compile(
            r'(?:can i|may i|could i|i have|i want|i need|tell me|help me|'
            r'question|ask you|something|anything|what is|how to|how do|'
            r'where is|when is|why is|who is|explain|describe|tell about|'
            r'what about|could you|would you|can you|will you)',
            re.IGNORECASE
        )
        
        # Insight patterns
        self.insight_pattern = re.compile(
            r'(?:insight|insights|analysis|analyze|understand|explain|interpret)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Groq route detection logic"""
        
        # ============================================================
        # 1. HELP
        # ============================================================
        if self.help_pattern.search(normalized):
            return RoutingDecision(
                intent="help",
                route_type=self.route_type,
                service_key=self.service_key,
                method="process_query",
                confidence=0.98,
                needs_groq=True,
                reason="Help query detected",
                original_message=message,
                matched_patterns=["help_pattern"]
            )
        
        # ============================================================
        # 2. GREETING
        # ============================================================
        if self.greeting_pattern.search(normalized):
            return RoutingDecision(
                intent="greeting",
                route_type=self.route_type,
                service_key=self.service_key,
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting detected",
                original_message=message,
                matched_patterns=["greeting_pattern"]
            )
        
        # ============================================================
        # 3. INSIGHTS REQUEST
        # ============================================================
        if self.insight_pattern.search(normalized):
            return RoutingDecision(
                intent="get_insights",
                route_type=self.route_type,
                service_key=self.service_key,
                method="generate_insights",
                confidence=0.85,
                needs_groq=True,
                reason="Insights request detected",
                original_message=message,
                matched_patterns=["insight_pattern"]
            )
        
        # ============================================================
        # 4. CONVERSATIONAL
        # ============================================================
        if self.conversational_pattern.search(normalized):
            return RoutingDecision(
                intent="conversational",
                route_type=self.route_type,
                service_key=self.service_key,
                method="process_query",
                confidence=0.80,
                needs_groq=True,
                reason="Conversational query detected",
                original_message=message,
                matched_patterns=["conversational_pattern"]
            )
        
        # ============================================================
        # 5. LONG MESSAGES (likely conversational)
        # ============================================================
        if len(message.split()) > 8:
            return RoutingDecision(
                intent="conversational",
                route_type=self.route_type,
                service_key=self.service_key,
                method="process_query",
                confidence=0.60,
                needs_groq=True,
                reason="Long message - likely conversational",
                original_message=message,
                matched_patterns=["long_message"]
            )
        
        return None


class AnalyticsRouteDetector(BaseRouteDetector):
    """Analytics Route: Detects cross-domain analytics and insights queries"""
    
    def __init__(self):
        super().__init__(RouteType.ANALYTICS, "analytics")
        
        # Cross-domain analytics patterns
        self.cross_domain_pattern = re.compile(
            r'(?:cross|across|all|overall)\s*(?:domain|area|category|sector|level)\s*(?:analytics|analysis|insights)',
            re.IGNORECASE
        )
        
        # Summary patterns
        self.summary_pattern = re.compile(
            r'(?:summary|overview|snapshot|quick|brief)\s*(?:report|analytics|analysis)',
            re.IGNORECASE
        )
        
        # Revenue patterns
        self.revenue_pattern = re.compile(
            r'(?:revenue|sales|income|turnover|profit|margin)\s*(?:analysis|analytics|report)',
            re.IGNORECASE
        )
        
        # Performance patterns
        self.performance_pattern = re.compile(
            r'(?:performance|efficiency|productivity|quality)\s*(?:analysis|analytics|report)',
            re.IGNORECASE
        )
        
        # Trend patterns
        self.trend_pattern = re.compile(
            r'(?:trend|trends|forecast|projection|prediction)\s*(?:analysis|analytics)',
            re.IGNORECASE
        )
    
    def detect(self, message: str, normalized: str) -> Optional[RoutingDecision]:
        """Analytics route detection logic"""
        
        # ============================================================
        # 1. CROSS-DOMAIN ANALYTICS
        # ============================================================
        if self.cross_domain_pattern.search(normalized):
            return RoutingDecision(
                intent="cross_domain_insights",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_cross_domain_insights",
                confidence=0.95,
                needs_groq=False,
                reason="Cross-domain analytics detected",
                original_message=message,
                matched_patterns=["cross_domain_pattern"]
            )
        
        # ============================================================
        # 2. SUMMARY ANALYTICS
        # ============================================================
        if self.summary_pattern.search(normalized):
            return RoutingDecision(
                intent="dashboard_summary",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_dashboard_summary",
                confidence=0.90,
                needs_groq=False,
                reason="Dashboard summary detected",
                original_message=message,
                matched_patterns=["summary_pattern"]
            )
        
        # ============================================================
        # 3. REVENUE ANALYSIS
        # ============================================================
        if self.revenue_pattern.search(normalized):
            return RoutingDecision(
                intent="revenue_analysis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_revenue_analysis",
                confidence=0.90,
                needs_groq=False,
                reason="Revenue analysis detected",
                original_message=message,
                matched_patterns=["revenue_pattern"]
            )
        
        # ============================================================
        # 4. PERFORMANCE ANALYSIS
        # ============================================================
        if self.performance_pattern.search(normalized):
            return RoutingDecision(
                intent="performance_analysis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_performance_analysis",
                confidence=0.90,
                needs_groq=False,
                reason="Performance analysis detected",
                original_message=message,
                matched_patterns=["performance_pattern"]
            )
        
        # ============================================================
        # 5. TREND ANALYSIS
        # ============================================================
        if self.trend_pattern.search(normalized):
            return RoutingDecision(
                intent="trend_analysis",
                route_type=self.route_type,
                service_key=self.service_key,
                method="get_trend_analysis",
                confidence=0.90,
                needs_groq=False,
                reason="Trend analysis detected",
                original_message=message,
                matched_patterns=["trend_pattern"]
            )
        
        return None


# ============================================================
# MASTER INTENT DETECTION ENGINE
# ============================================================

class MasterIntentDetectionEngine:
    """Master engine that orchestrates all route-specific detectors"""
    
    def __init__(self):
        # Initialize all route detectors
        self.detectors = [
            DNRouteDetector(),           # Priority 1: DN numbers and pending queries
            NationalKPIRouteDetector(),  # Priority 2: National KPIs
            AnalyticsRouteDetector(),    # Priority 3: Cross-domain analytics
            DealerRouteDetector(),       # Priority 4: Dealer queries
            WarehouseRouteDetector(),    # Priority 5: Warehouse queries
            CityRouteDetector(),         # Priority 6: City queries
            ProductRouteDetector(),      # Priority 7: Product queries
            GroqRouteDetector(),         # Priority 8: Conversational (catch-all)
        ]
        
        logger.info("✅ MasterIntentDetectionEngine initialized with %d detectors", len(self.detectors))
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """Detect intent using all route detectors in priority order"""
        if not message or not message.strip():
            return RoutingDecision(
                intent="unknown",
                route_type=RouteType.UNKNOWN,
                service_key="groq",
                method="process_query",
                confidence=0.0,
                needs_groq=True,
                reason="Empty message",
                original_message=message
            )
        
        cleaned = message.strip()
        normalized = cleaned.lower()
        
        # Try each detector in priority order
        for detector in self.detectors:
            try:
                decision = detector.detect(cleaned, normalized)
                if decision:
                    logger.info(
                        f"🎯 Detected: {decision.route_type} - "
                        f"Intent: {decision.intent} - "
                        f"Confidence: {decision.confidence:.2f} - "
                        f"Reason: {decision.reason}"
                    )
                    return decision
            except Exception as e:
                logger.error(f"Detector {detector.__class__.__name__} failed: {e}")
                continue
        
        # Fallback to Groq if no detector matches
        return RoutingDecision(
            intent="general_ai",
            route_type=RouteType.GROQ,
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="No specific route matched - Groq fallback",
            original_message=cleaned
        )


# ============================================================
# WHATSAPP PROVIDER SERVICE - COMPLETE ORCHESTRATOR
# ============================================================

class WhatsAppProviderService:
    """Complete orchestrator with specialized routes"""
    
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("🤖 WhatsApp AI Agent v11.0 - SPECIALIZED ROUTES")
            logger.info("=" * 70)
            
            # Initialize registry
            self.registry = ServiceRegistry()
            logger.info("✅ ServiceRegistry initialized")
            
            # Initialize master intent engine
            self.intent_engine = MasterIntentDetectionEngine()
            logger.info("✅ MasterIntentDetectionEngine initialized")
            
            # Load all services
            logger.info("=" * 50)
            logger.info("🔧 LOADING SERVICES...")
            logger.info("=" * 50)
            
            # DN Service
            self.dn_service = self.registry.get_service_instance("dn")
            if self.dn_service:
                logger.info("✅ DN Service loaded (dn_analysis.py)")
            else:
                logger.error("❌ DN Service FAILED to load")
            
            # Dealer Service
            self.dealer_service = self.registry.get_service_instance("dealer")
            if self.dealer_service:
                logger.info("✅ Dealer Service loaded (dealer_analytics_service.py)")
            else:
                logger.error("❌ Dealer Service FAILED to load")
            
            # Warehouse Service
            self.warehouse_service = self.registry.get_service_instance("warehouse")
            if self.warehouse_service:
                logger.info("✅ Warehouse Service loaded (warehouse_service.py)")
            else:
                logger.warning("⚠️ Warehouse Service FAILED to load (optional)")
            
            # City Service
            self.city_service = self.registry.get_service_instance("city")
            if self.city_service:
                logger.info("✅ City Service loaded (city_service.py)")
            else:
                logger.warning("⚠️ City Service FAILED to load (optional)")
            
            # Product Service
            self.product_service = self.registry.get_service_instance("product")
            if self.product_service:
                logger.info("✅ Product Service loaded (product_service.py)")
            else:
                logger.warning("⚠️ Product Service FAILED to load (optional)")
            
            # National KPI Service
            self.national_kpi_service = self.registry.get_service_instance("national_kpi")
            if self.national_kpi_service:
                logger.info("✅ National KPI Service loaded (national_kpi_service.py)")
            else:
                logger.warning("⚠️ National KPI Service FAILED to load (optional)")
            
            # Groq Service
            self.groq_service = self.registry.get_service_instance("groq")
            if self.groq_service:
                logger.info("✅ Groq Service loaded (groq_service.py)")
            else:
                logger.warning("⚠️ Groq Service FAILED to load (optional)")
            
            # Analytics Service
            self.analytics_service = self.registry.get_service_instance("analytics")
            if self.analytics_service:
                logger.info("✅ Analytics Service loaded (analytics_service.py)")
            else:
                logger.warning("⚠️ Analytics Service FAILED to load (optional)")
            
            # Log summary
            logger.info("=" * 50)
            logger.info("📊 SERVICE STATUS SUMMARY:")
            logger.info(f"   DN: {'✅' if self.dn_service else '❌'}")
            logger.info(f"   Dealer: {'✅' if self.dealer_service else '❌'}")
            logger.info(f"   Warehouse: {'✅' if self.warehouse_service else '❌'}")
            logger.info(f"   City: {'✅' if self.city_service else '❌'}")
            logger.info(f"   Product: {'✅' if self.product_service else '❌'}")
            logger.info(f"   National KPI: {'✅' if self.national_kpi_service else '❌'}")
            logger.info(f"   Groq: {'✅' if self.groq_service else '❌'}")
            logger.info(f"   Analytics: {'✅' if self.analytics_service else '❌'}")
            logger.info("=" * 50)
            logger.info(f"   ROUTES AVAILABLE: {len([d for d in self.intent_engine.detectors])}")
            logger.info("   ROUTE TYPES: DN, Dealer, Warehouse, City, Product, National KPI, Groq, Analytics")
            logger.info("=" * 50)
            
            init_duration = (time.time() - start_time) * 1000
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    # ============================================================
    # MAIN ENTRY POINT
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Single entry point for all WhatsApp requests.
        
        IMPORTANT: DO NOT CHANGE THIS SIGNATURE - webhook.py depends on it.
        """
        request_id = str(uuid.uuid4())[:8]
        logger.info(f"📩 [REQ:{request_id}] Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            # ============================================================
            # STEP 1: DETECT INTENT USING MASTER ENGINE
            # ============================================================
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(
                f"🎯 [REQ:{request_id}] Route: {routing_decision.route_type} - "
                f"Intent: {routing_decision.intent} - "
                f"Confidence: {routing_decision.confidence:.2f}"
            )
            
            # ============================================================
            # STEP 2: ROUTE TO APPROPRIATE HANDLER
            # ============================================================
            
            # ROUTE 1: DN ROUTE
            if routing_decision.route_type == RouteType.DN:
                return await self._handle_dn_route(routing_decision, request_id)
            
            # ROUTE 2: DEALER ROUTE
            elif routing_decision.route_type == RouteType.DEALER:
                return await self._handle_dealer_route(routing_decision, request_id)
            
            # ROUTE 3: WAREHOUSE ROUTE
            elif routing_decision.route_type == RouteType.WAREHOUSE:
                return await self._handle_warehouse_route(routing_decision, request_id)
            
            # ROUTE 4: CITY ROUTE
            elif routing_decision.route_type == RouteType.CITY:
                return await self._handle_city_route(routing_decision, request_id)
            
            # ROUTE 5: PRODUCT ROUTE
            elif routing_decision.route_type == RouteType.PRODUCT:
                return await self._handle_product_route(routing_decision, request_id)
            
            # ROUTE 6: NATIONAL KPI ROUTE
            elif routing_decision.route_type == RouteType.NATIONAL_KPI:
                return await self._handle_national_kpi_route(routing_decision, request_id)
            
            # ROUTE 7: ANALYTICS ROUTE
            elif routing_decision.route_type == RouteType.ANALYTICS:
                return await self._handle_analytics_route(routing_decision, request_id)
            
            # ROUTE 8: GROQ ROUTE (Fallback for conversational)
            elif routing_decision.route_type == RouteType.GROQ or routing_decision.needs_groq:
                return await self._handle_groq_route(routing_decision, request_id)
            
            # Fallback
            else:
                return self._format_response(
                    message,
                    "I couldn't identify your request. Please try one of these:\n\n"
                    "📦 **DN Tracking**: Send any 8-12 digit number\n"
                    "🏪 **Dealer Analytics**: 'Dealer [name]' or 'Top dealers'\n"
                    "🏭 **Warehouse Analytics**: 'Warehouse [name]' or 'Top warehouses'\n"
                    "🏙️ **City Analytics**: 'City [name]' or 'Top cities'\n"
                    "📦 **Product Analytics**: 'Product [name]' or 'Top products'\n"
                    "📊 **National KPIs**: 'National KPI' or 'Executive summary'\n"
                    "💬 **Conversational**: Ask me anything!\n\n"
                    "Type 'Help' for more details.",
                    error=False,
                    request_id=request_id
                )
            
        except Exception as e:
            logger.exception(f"❌ [REQ:{request_id}] Failed: {e}")
            return self._format_response(
                message,
                f"⚠️ An unexpected error occurred. Reference: {request_id}",
                error=True,
                request_id=request_id
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ [REQ:{request_id}] Response time: {elapsed_ms:.2f}ms")
    
    # ============================================================
    # ROUTE HANDLERS - One per route type
    # ============================================================
    
    # ============================================================
    # ROUTE 1: DN ROUTE HANDLER
    # ============================================================
    async def _handle_dn_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle DN route - DN numbers, pending DNs, pending PGI, pending POD"""
        try:
            if not self.dn_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ DN service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            # Get the method from the service
            method = getattr(self.dn_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in DN service.",
                    error=True,
                    request_id=request_id
                )
            
            # Execute the method with appropriate parameters
            result = None
            if decision.entity and decision.entity2:
                result = method(decision.entity, decision.entity2)
            elif decision.entity:
                result = method(decision.entity)
            else:
                result = method()
            
            # Handle async result
            if inspect.iscoroutine(result):
                result = await result
            
            # Format response
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] DN route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ DN route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 2: DEALER ROUTE HANDLER
    # ============================================================
    async def _handle_dealer_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Dealer route - Dealer dashboards, rankings, comparisons"""
        try:
            if not self.dealer_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ Dealer service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.dealer_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in Dealer service.",
                    error=True,
                    request_id=request_id
                )
            
            # Execute with appropriate parameters
            result = None
            if decision.entity and decision.entity2:
                result = method(decision.entity, decision.entity2)
            elif decision.entity:
                # Check if method expects a limit parameter for rankings
                if decision.intent in ["top_dealers", "bottom_dealers"]:
                    result = method(limit=10)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Dealer route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Dealer route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 3: WAREHOUSE ROUTE HANDLER
    # ============================================================
    async def _handle_warehouse_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Warehouse route - Warehouse dashboards and analytics"""
        try:
            if not self.warehouse_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ Warehouse service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.warehouse_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in Warehouse service.",
                    error=True,
                    request_id=request_id
                )
            
            result = None
            if decision.entity:
                # Check if ranking method
                if decision.intent in ["top_warehouses", "bottom_warehouses"]:
                    result = method(limit=10)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Warehouse route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Warehouse route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 4: CITY ROUTE HANDLER
    # ============================================================
    async def _handle_city_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle City route - City dashboards and analytics"""
        try:
            if not self.city_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ City service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.city_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in City service.",
                    error=True,
                    request_id=request_id
                )
            
            result = None
            if decision.entity:
                if decision.intent in ["top_cities", "bottom_cities"]:
                    result = method(limit=10)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] City route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ City route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 5: PRODUCT ROUTE HANDLER
    # ============================================================
    async def _handle_product_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Product route - Product dashboards and analytics"""
        try:
            if not self.product_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ Product service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.product_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in Product service.",
                    error=True,
                    request_id=request_id
                )
            
            result = None
            if decision.entity:
                if decision.intent in ["top_products", "bottom_products"]:
                    result = method(limit=10)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Product route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Product route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 6: NATIONAL KPI ROUTE HANDLER
    # ============================================================
    async def _handle_national_kpi_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle National KPI route - Executive dashboards and national metrics"""
        try:
            if not self.national_kpi_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ National KPI service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.national_kpi_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in National KPI service.",
                    error=True,
                    request_id=request_id
                )
            
            result = method()
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] National KPI route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ National KPI route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 7: ANALYTICS ROUTE HANDLER
    # ============================================================
    async def _handle_analytics_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Analytics route - Cross-domain analytics and insights"""
        try:
            if not self.analytics_service:
                return self._format_response(
                    decision.original_message,
                    "⚠️ Analytics service is currently unavailable. Please try again later.",
                    error=True,
                    request_id=request_id
                )
            
            method = getattr(self.analytics_service, decision.method, None)
            if not method:
                return self._format_response(
                    decision.original_message,
                    f"⚠️ Method '{decision.method}' not found in Analytics service.",
                    error=True,
                    request_id=request_id
                )
            
            result = method()
            if inspect.iscoroutine(result):
                result = await result
            
            return self._format_service_response(decision, result, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Analytics route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                f"⚠️ Analytics route failed: {str(e)}",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # ROUTE 8: GROQ ROUTE HANDLER
    # ============================================================
    async def _handle_groq_route(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Handle Groq route - Conversational AI and general queries"""
        try:
            if not self.groq_service:
                # Fallback responses when Groq is unavailable
                return self._get_fallback_response(decision, request_id)
            
            method = getattr(self.groq_service, decision.method, "process_query")
            if callable(method):
                result = method(decision.original_message)
                if inspect.iscoroutine(result):
                    result = await result
                
                # Format result
                if isinstance(result, dict):
                    return self._format_service_response(decision, result, request_id)
                elif isinstance(result, str):
                    return self._format_response(
                        decision.original_message,
                        result,
                        error=False,
                        request_id=request_id
                    )
                else:
                    return self._format_response(
                        decision.original_message,
                        str(result),
                        error=False,
                        request_id=request_id
                    )
            else:
                # Method not callable
                return self._get_fallback_response(decision, request_id)
            
        except Exception as e:
            logger.error(f"❌ [REQ:{request_id}] Groq route handler failed: {e}")
            return self._format_response(
                decision.original_message,
                "⚠️ Conversational AI is currently unavailable. Please try again later.",
                error=True,
                request_id=request_id
            )
    
    # ============================================================
    # FALLBACK RESPONSES FOR GROQ
    # ============================================================
    def _get_fallback_response(self, decision: RoutingDecision, request_id: str) -> Dict[str, Any]:
        """Get fallback responses when Groq is unavailable"""
        
        if decision.intent == "help":
            return self._format_response(
                decision.original_message,
                "📋 **Available Commands**\n\n"
                "📦 **DN Queries:**\n"
                "• Send a DN number (8-12 digits)\n"
                "• 'Pending DN' - Show pending DNs\n"
                "• 'Pending PGI' - Show pending PGI\n"
                "• 'Pending POD' - Show pending POD\n\n"
                "🏪 **Dealer Queries:**\n"
                "• 'Dealer [name]' - Dealer dashboard\n"
                "• 'Top dealers' - Show top performers\n"
                "• 'Bottom dealers' - Show bottom performers\n\n"
                "🏭 **Warehouse Queries:**\n"
                "• 'Warehouse [name]' - Warehouse dashboard\n"
                "• 'Top warehouses' - Show top warehouses\n\n"
                "🏙️ **City Queries:**\n"
                "• 'City [name]' - City dashboard\n"
                "• 'Top cities' - Show top cities\n\n"
                "📦 **Product Queries:**\n"
                "• 'Product [name]' - Product dashboard\n"
                "• 'Top products' - Show top products\n\n"
                "📊 **National KPIs:**\n"
                "• 'National KPI' - Executive dashboard\n"
                "• 'Executive summary' - Leadership summary\n\n"
                "💬 **Conversational:**\n"
                "• Any question or query I'll try to help!",
                error=False,
                request_id=request_id
            )
        
        elif decision.intent == "greeting":
            return self._format_response(
                decision.original_message,
                "👋 Hello! I'm your WhatsApp AI Assistant.\n\n"
                "I can help you with:\n"
                "📦 Tracking DNs (8-12 digit numbers)\n"
                "🏪 Dealer performance and rankings\n"
                "🏭 Warehouse operations\n"
                "🏙️ City-level analytics\n"
                "📦 Product performance\n"
                "📊 National KPIs and executive insights\n\n"
                "Type 'Help' to see all available commands.",
                error=False,
                request_id=request_id
            )
        
        else:
            return self._format_response(
                decision.original_message,
                "🤔 I'm not sure how to help with that.\n\n"
                "Try one of these:\n"
                "• Send a DN number (8-12 digits)\n"
                "• 'Dealer [name]' for dealer info\n"
                "• 'Top dealers' for rankings\n"
                "• 'National KPI' for executive dashboard\n"
                "• 'Help' for all available commands\n\n"
                "I'm here to help! What would you like to know?",
                error=False,
                request_id=request_id
            )
    
    # ============================================================
    # SERVICE RESPONSE FORMATTER
    # ============================================================
    def _format_service_response(
        self,
        decision: RoutingDecision,
        result: Any,
        request_id: str
    ) -> Dict[str, Any]:
        """Format response from a service"""
        if result is None:
            return self._format_response(
                decision.original_message,
                "⚠️ No data found for your request.",
                error=True,
                request_id=request_id
            )
        
        if isinstance(result, dict):
            if result.get("success", True) is False:
                return self._format_response(
                    decision.original_message,
                    result.get("message", result.get("whatsapp_message", "⚠️ Request failed.")),
                    error=True,
                    request_id=request_id
                )
            
            # Try to get the message from various keys
            message = result.get("whatsapp_message")
            if not message:
                message = result.get("message")
            if not message:
                message = result.get("response")
            if not message:
                message = result.get("data")
            
            if message:
                return self._format_response(
                    decision.original_message,
                    message,
                    error=False,
                    request_id=request_id
                )
            else:
                # Return the dict as string
                return self._format_response(
                    decision.original_message,
                    str(result),
                    error=False,
                    request_id=request_id
                )
        
        # Check for to_whatsapp_message method
        if hasattr(result, "to_whatsapp_message"):
            try:
                message = result.to_whatsapp_message()
                return self._format_response(
                    decision.original_message,
                    message,
                    error=False,
                    request_id=request_id
                )
            except Exception as e:
                logger.warning(f"to_whatsapp_message failed: {e}")
        
        # Convert to string
        return self._format_response(
            decision.original_message,
            str(result),
            error=False,
            request_id=request_id
        )
    
    # ============================================================
    # RESPONSE FORMATTER - UNIFIED
    # ============================================================
    def _format_response(
        self,
        original_message: str,
        reply: Any,
        error: bool = False,
        request_id: str = ""
    ) -> Dict[str, Any]:
        """Unified response formatter matching webhook expectations"""
        
        # Handle objects with to_whatsapp_message
        if hasattr(reply, "to_whatsapp_message"):
            try:
                reply = reply.to_whatsapp_message()
            except Exception as e:
                logger.warning(f"to_whatsapp_message failed: {e}")
                reply = str(reply)
        
        # Handle dictionaries
        if isinstance(reply, dict):
            # Look for whatsapp_message key
            if "whatsapp_message" in reply:
                reply = reply["whatsapp_message"]
            elif "message" in reply:
                reply = reply["message"]
            elif "response" in reply:
                reply = reply["response"]
            elif "data" in reply:
                # Check if data has to_whatsapp_message
                data = reply["data"]
                if hasattr(data, "to_whatsapp_message"):
                    try:
                        reply = data.to_whatsapp_message()
                    except:
                        reply = str(data)
                else:
                    reply = str(data)
            else:
                reply = str(reply)
        
        # Ensure string
        if not isinstance(reply, str):
            reply = str(reply)
        
        return {
            "original_message": original_message,
            "whatsapp_message": reply,
            "status": "error" if error else "success",
            "error": error,
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat(),
            "version": "11.0"
        }
    
    # ============================================================
    # DIAGNOSTIC METHODS
    # ============================================================
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get system health status"""
        return {
            "status": "healthy",
            "version": "11.0",
            "services": {
                "dn": self.registry.is_service_ready("dn"),
                "dealer": self.registry.is_service_ready("dealer"),
                "warehouse": self.registry.is_service_ready("warehouse"),
                "city": self.registry.is_service_ready("city"),
                "product": self.registry.is_service_ready("product"),
                "national_kpi": self.registry.is_service_ready("national_kpi"),
                "groq": self.registry.is_service_ready("groq"),
                "analytics": self.registry.is_service_ready("analytics")
            },
            "routes": {
                "DN": True,
                "Dealer": True,
                "Warehouse": True,
                "City": True,
                "Product": True,
                "National KPI": True,
                "Groq": True,
                "Analytics": True
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def get_routing_info(self, message: str) -> Dict[str, Any]:
        """Get routing information for a message without executing"""
        decision = self.intent_engine.detect_intent(message)
        return {
            "message": message,
            "routing_decision": decision.to_dict(),
            "matched_patterns": decision.matched_patterns,
            "available_routes": [str(route) for route in RouteType if route != RouteType.UNKNOWN]
        }


# ============================================================
# SINGLETON INSTANCE
# ============================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    """Get singleton instance of WhatsAppProviderService"""
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService initialized (v11.0)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    return _whatsapp_provider_service


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'RouteType',
    'RoutingDecision',
    'MasterIntentDetectionEngine',
    'DNRouteDetector',
    'DealerRouteDetector',
    'WarehouseRouteDetector',
    'CityRouteDetector',
    'ProductRouteDetector',
    'NationalKPIRouteDetector',
    'GroqRouteDetector',
    'AnalyticsRouteDetector'
]

logger.info("=" * 70)
logger.info("AI Provider Service v11.0 - SPECIALIZED ROUTES")
logger.info("=" * 70)
logger.info("✅ 8 Specialized Routes Available:")
logger.info("   1. DN Route - DN tracking and pending queries")
logger.info("   2. Dealer Route - Dealer analytics and rankings")
logger.info("   3. Warehouse Route - Warehouse analytics")
logger.info("   4. City Route - City-level analytics")
logger.info("   5. Product Route - Product analytics")
logger.info("   6. National KPI Route - Executive dashboards")
logger.info("   7. Analytics Route - Cross-domain insights")
logger.info("   8. Groq Route - Conversational AI")
logger.info("=" * 70)
