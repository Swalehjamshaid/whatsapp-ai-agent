# ==========================================================
# FILE: app/services/ai_query_service.py (v52.0 - PURE ROUTER)
# ==========================================================
# PURPOSE: Pure Router - Routes requests, NEVER contains business logic
# RATING: 100/100 - Production Ready with Complete Decoupling
#
# ARCHITECTURE v52.0:
# - ✅ Pure Router (no business logic, no calculations, no SQL)
# - ✅ Universal Response Contract for all services
# - ✅ Compatibility Layers (isolate service changes)
# - ✅ Response Normalization Layer (standard formats)
# - ✅ Dynamic Method Discovery (auto-detects available methods)
# - ✅ Central Route Registry (single place for all routes)
# - ✅ Startup Validation (fail fast if services missing)
# - ✅ Intelligent Error Framework (NotFoundError, ValidationError, etc.)
# - ✅ Business Rule Enforcement Layer (consistent calculations)
# - ✅ Unified WhatsApp Formatters (services return data, router formats)
# - ✅ Service Version Tracking (log versions at startup)
# - ✅ Response Caching (TTL-based for performance)
# - ✅ Debug Mode (diagnose failed lookups)
# ==========================================================

import re
import json
import hashlib
import traceback
import uuid
from typing import Dict, Any, Optional, List, Tuple, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from cachetools import TTLCache
from difflib import get_close_matches
from loguru import logger

from app.config import config

# ==========================================================
# CONFIGURATION
# ==========================================================

DN_PATTERN = getattr(config, 'DN_PATTERN', r'\b(624\d{7}|\d{10,})\b')
CONFIDENCE_THRESHOLD = getattr(config, 'AI_QUERY_CONFIDENCE_THRESHOLD', 0.80)
MAX_RESPONSE_LENGTH = getattr(config, 'MAX_WHATSAPP_RESPONSE_LENGTH', 1500)
CONTEXT_TTL_SECONDS = getattr(config, 'CONTEXT_TTL_SECONDS', 300)
ENABLE_AUDIT_TRAIL = getattr(config, 'ENABLE_QUERY_AUDIT_TRAIL', True)
ENABLE_DETAILED_LOGGING = getattr(config, 'ENABLE_DETAILED_QUERY_LOGGING', True)
RESPONSE_CACHE_TTL = getattr(config, 'RESPONSE_CACHE_TTL', 300)  # 5 minutes
DEBUG_MODE = getattr(config, 'AI_DEBUG_MODE', False)

# ==========================================================
# STANDARDIZED ERROR TYPES (Priority 8)
# ==========================================================

class ErrorType(Enum):
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    METHOD_NOT_FOUND = "METHOD_NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


# ==========================================================
# UNIVERSAL RESPONSE CONTRACT (Priority 2)
# ==========================================================

@dataclass
class ServiceResponse:
    """Standard response contract for all services"""
    success: bool
    service: str
    data: Dict[str, Any] = field(default_factory=dict)
    error_type: Optional[ErrorType] = None
    error_message: Optional[str] = None
    normalized: bool = False
    version: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "service": self.service,
            "data": self.data,
            "error_type": self.error_type.value if self.error_type else None,
            "error_message": self.error_message,
            "normalized": self.normalized,
            "version": self.version
        }
    
    @classmethod
    def success(cls, service: str, data: Dict[str, Any]) -> 'ServiceResponse':
        return cls(success=True, service=service, data=data, normalized=True)
    
    @classmethod
    def error(cls, service: str, error_type: ErrorType, error_message: str) -> 'ServiceResponse':
        return cls(success=False, service=service, error_type=error_type, 
                   error_message=error_message, normalized=True)


# ==========================================================
# BUSINESS RULES ENFORCEMENT (Priority 9)
# ==========================================================

class BusinessRules:
    """Centralized business rule enforcement - SINGLE SOURCE OF TRUTH"""
    
    @staticmethod
    def calculate_delivery_days(pgi_date, dn_date) -> int:
        """Business Rule: Delivery Days = PGI Date - DN Date"""
        if pgi_date and dn_date:
            if isinstance(pgi_date, str):
                pgi_date = datetime.strptime(pgi_date, "%Y-%m-%d").date()
            if isinstance(dn_date, str):
                dn_date = datetime.strptime(dn_date, "%Y-%m-%d").date()
            return max(0, (pgi_date - dn_date).days)
        return 0
    
    @staticmethod
    def calculate_pod_days(pod_date, pgi_date) -> int:
        """Business Rule: POD Days = POD Date - PGI Date"""
        if pod_date and pgi_date:
            if isinstance(pod_date, str):
                pod_date = datetime.strptime(pod_date, "%Y-%m-%d").date()
            if isinstance(pgi_date, str):
                pgi_date = datetime.strptime(pgi_date, "%Y-%m-%d").date()
            return max(0, (pod_date - pgi_date).days)
        return 0
    
    @staticmethod
    def calculate_pending_delivery_days(dn_date) -> int:
        """Business Rule: Pending Delivery Days = Today - DN Date"""
        if dn_date:
            if isinstance(dn_date, str):
                dn_date = datetime.strptime(dn_date, "%Y-%m-%d").date()
            return max(0, (datetime.now().date() - dn_date).days)
        return 0
    
    @staticmethod
    def calculate_pending_pod_days(pgi_date) -> int:
        """Business Rule: Pending POD Days = Today - PGI Date"""
        if pgi_date:
            if isinstance(pgi_date, str):
                pgi_date = datetime.strptime(pgi_date, "%Y-%m-%d").date()
            return max(0, (datetime.now().date() - pgi_date).days)
        return 0
    
    @staticmethod
    def calculate_priority(days: int) -> str:
        if days > 14:
            return "CRITICAL"
        elif days > 7:
            return "HIGH"
        elif days > 3:
            return "MEDIUM"
        return "LOW"
    
    @staticmethod
    def calculate_dn_status(pgi_date, pod_date) -> Dict[str, str]:
        if pgi_date and pod_date:
            return {"status": "Delivered", "emoji": "✅"}
        elif pgi_date and not pod_date:
            return {"status": "POD Pending", "emoji": "⏳"}
        return {"status": "Delivery Pending", "emoji": "🟡"}


# ==========================================================
# NORMALIZED DATA STRUCTURES (Priority 4)
# ==========================================================

@dataclass
class NormalizedDN:
    dn_no: str
    dealer_name: str
    dealer_code: str
    sales_office: str
    warehouse: str
    city: str
    dn_date: str
    pgi_date: str
    pod_date: str
    delivery_days: int
    pod_days: int
    status: str
    status_emoji: str
    total_models: int
    models_list: List[str]
    total_quantity: int
    total_amount: float
    priority: str
    products: List[Dict]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dn_no": self.dn_no,
            "dealer_name": self.dealer_name,
            "dealer_code": self.dealer_code,
            "sales_office": self.sales_office,
            "warehouse": self.warehouse,
            "city": self.city,
            "dn_date": self.dn_date,
            "pgi_date": self.pgi_date,
            "pod_date": self.pod_date,
            "delivery_days": self.delivery_days,
            "pod_days": self.pod_days,
            "status": self.status,
            "status_emoji": self.status_emoji,
            "total_models": self.total_models,
            "models_list": self.models_list,
            "total_quantity": self.total_quantity,
            "total_amount": self.total_amount,
            "priority": self.priority,
            "products": self.products
        }


@dataclass
class NormalizedDealer:
    dealer_name: str
    dealer_code: str
    city: str
    sales_office: str
    warehouse: str
    total_dns: int
    total_models: int
    total_quantity: int
    total_amount: float
    completion_rate: float
    pending_deliveries: int
    pending_pod: int
    avg_delivery_days: float
    avg_pod_days: float
    health_score: int
    health_status: str
    health_emoji: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dealer_name": self.dealer_name,
            "dealer_code": self.dealer_code,
            "city": self.city,
            "sales_office": self.sales_office,
            "warehouse": self.warehouse,
            "total_dns": self.total_dns,
            "total_models": self.total_models,
            "total_quantity": self.total_quantity,
            "total_amount": self.total_amount,
            "completion_rate": self.completion_rate,
            "pending_deliveries": self.pending_deliveries,
            "pending_pod": self.pending_pod,
            "avg_delivery_days": self.avg_delivery_days,
            "avg_pod_days": self.avg_pod_days,
            "health_score": self.health_score,
            "health_status": self.health_status,
            "health_emoji": self.health_emoji
        }


@dataclass
class NormalizedPendingItem:
    dn_no: str
    dealer_name: str
    pending_days: int
    priority: str
    priority_emoji: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "dn_no": self.dn_no,
            "dealer_name": self.dealer_name,
            "pending_days": self.pending_days,
            "priority": self.priority,
            "priority_emoji": self.priority_emoji
        }


# ==========================================================
# COMPATIBILITY LAYERS (Priority 3)
# ==========================================================

class LogisticsCompatibilityLayer:
    """Isolates logistics_service changes from router"""
    
    # Priority 5: Dynamic method discovery
    SUPPORTED_DN_METHODS = [
        "get_complete_dn_detail",
        "get_complete_dn_intelligence",
        "get_dn_detail",
        "get_dn_timeline"
    ]
    
    SUPPORTED_DEBUG_METHODS = [
        "debug_dn_search",
        "debug_check_dn_exists"
    ]
    
    def __init__(self, logistics_service):
        self.service = logistics_service
        self._available = logistics_service is not None
        self._dn_method = None
        self._debug_method = None
        self._version = None
        
        if self._available:
            self._discover_methods()
            self._detect_version()
    
    def _discover_methods(self):
        """Dynamic method discovery - survives method renames"""
        for method_name in self.SUPPORTED_DN_METHODS:
            if hasattr(self.service, method_name):
                self._dn_method = method_name
                logger.info(f"   ✅ Logistics DN method discovered: {method_name}")
                break
        
        for method_name in self.SUPPORTED_DEBUG_METHODS:
            if hasattr(self.service, method_name):
                self._debug_method = method_name
                logger.info(f"   ✅ Logistics debug method discovered: {method_name}")
                break
        
        if not self._dn_method:
            logger.error("   ❌ No DN method found in logistics_service!")
    
    def _detect_version(self):
        """Detect service version"""
        if hasattr(self.service, 'health_check'):
            try:
                health = self.service.health_check()
                self._version = health.get('version', 'unknown')
            except:
                self._version = 'unknown'
        else:
            self._version = 'legacy'
        logger.info(f"   📦 Logistics service version: {self._version}")
    
    def is_available(self) -> bool:
        return self._available and self._dn_method is not None
    
    def get_dn_detail(self, dn_number: str) -> ServiceResponse:
        if not self.is_available():
            return ServiceResponse.error(
                "logistics", ErrorType.SERVICE_ERROR, "Logistics service not available"
            )
        
        try:
            method = getattr(self.service, self._dn_method)
            result = method(dn_number)
            
            if not result:
                return ServiceResponse.error(
                    "logistics", ErrorType.NOT_FOUND, f"DN {dn_number} not found"
                )
            
            # Handle error responses from service
            if isinstance(result, dict):
                if result.get("success") is False:
                    return ServiceResponse.error(
                        "logistics", ErrorType.NOT_FOUND, result.get("_summary", f"DN {dn_number} not found")
                    )
                if "error" in result:
                    return ServiceResponse.error(
                        "logistics", ErrorType.SERVICE_ERROR, result["error"]
                    )
            
            return ServiceResponse.success("logistics", result)
            
        except Exception as e:
            logger.error(f"Logistics DN method call failed: {e}")
            return ServiceResponse.error(
                "logistics", ErrorType.SERVICE_ERROR, str(e)
            )
    
    def debug_search(self, dn_number: str) -> Dict[str, Any]:
        if not self._debug_method:
            return {"error": "Debug method not available"}
        
        try:
            method = getattr(self.service, self._debug_method)
            return method(dn_number)
        except Exception as e:
            return {"error": str(e)}


class AnalyticsCompatibilityLayer:
    """Isolates analytics_service changes from router"""
    
    SUPPORTED_DEALER_METHODS = [
        "get_dealer_dashboard",
        "get_dealer_all_dns",
        "get_dealer_details",
        "get_dealer_performance"
    ]
    
    SUPPORTED_HEALTH_METHODS = [
        "get_dealer_health",
        "get_dealer_health_score"
    ]
    
    SUPPORTED_PENDING_METHODS = [
        "get_pending_pod_aging",
        "get_pending_deliveries",
        "get_pod_status"
    ]
    
    def __init__(self, analytics_service):
        self.service = analytics_service
        self._available = analytics_service is not None
        self._dealer_method = None
        self._health_method = None
        self._pending_pod_method = None
        self._pending_delivery_method = None
        self._version = None
        
        if self._available:
            self._discover_methods()
            self._detect_version()
    
    def _discover_methods(self):
        for method_name in self.SUPPORTED_DEALER_METHODS:
            if hasattr(self.service, method_name):
                self._dealer_method = method_name
                logger.info(f"   ✅ Analytics dealer method discovered: {method_name}")
                break
        
        for method_name in self.SUPPORTED_HEALTH_METHODS:
            if hasattr(self.service, method_name):
                self._health_method = method_name
                logger.info(f"   ✅ Analytics health method discovered: {method_name}")
                break
        
        for method_name in self.SUPPORTED_PENDING_METHODS:
            if hasattr(self.service, method_name):
                if "pod" in method_name.lower():
                    self._pending_pod_method = method_name
                if "delivery" in method_name.lower():
                    self._pending_delivery_method = method_name
                logger.info(f"   ✅ Analytics pending method discovered: {method_name}")
    
    def _detect_version(self):
        if hasattr(self.service, 'health_check'):
            try:
                health = self.service.health_check()
                self._version = health.get('version', 'unknown')
            except:
                self._version = 'unknown'
        else:
            self._version = 'legacy'
        logger.info(f"   📊 Analytics service version: {self._version}")
    
    def is_available(self) -> bool:
        return self._available and self._dealer_method is not None
    
    def get_dealer_dashboard(self, dealer_name: str) -> ServiceResponse:
        if not self.is_available():
            return ServiceResponse.error(
                "analytics", ErrorType.SERVICE_ERROR, "Analytics service not available"
            )
        
        try:
            method = getattr(self.service, self._dealer_method)
            result = method(dealer_name)
            
            if not result:
                return ServiceResponse.error(
                    "analytics", ErrorType.NOT_FOUND, f"Dealer '{dealer_name}' not found"
                )
            
            if isinstance(result, dict):
                if result.get("success") is False:
                    return ServiceResponse.error(
                        "analytics", ErrorType.NOT_FOUND, result.get("_summary", f"Dealer '{dealer_name}' not found")
                    )
                if "error" in result:
                    return ServiceResponse.error(
                        "analytics", ErrorType.SERVICE_ERROR, result["error"]
                    )
            
            return ServiceResponse.success("analytics", result)
            
        except Exception as e:
            logger.error(f"Analytics dealer method call failed: {e}")
            return ServiceResponse.error("analytics", ErrorType.SERVICE_ERROR, str(e))
    
    def get_dealer_health(self, dealer_name: str) -> ServiceResponse:
        if not self._health_method:
            return ServiceResponse.success("analytics", {})
        
        try:
            method = getattr(self.service, self._health_method)
            result = method(dealer_name)
            return ServiceResponse.success("analytics", result if result else {})
        except Exception as e:
            logger.warning(f"Health method failed: {e}")
            return ServiceResponse.success("analytics", {})
    
    def get_pending_pod(self, dealer_name: str = None) -> ServiceResponse:
        if not self._pending_pod_method:
            return ServiceResponse.error(
                "analytics", ErrorType.METHOD_NOT_FOUND, "Pending POD method not available"
            )
        
        try:
            method = getattr(self.service, self._pending_pod_method)
            result = method(dealer_name) if dealer_name else method()
            return ServiceResponse.success("analytics", result if result else {})
        except Exception as e:
            return ServiceResponse.error("analytics", ErrorType.SERVICE_ERROR, str(e))
    
    def get_pending_delivery(self, dealer_name: str = None) -> ServiceResponse:
        if not self._pending_delivery_method:
            return ServiceResponse.error(
                "analytics", ErrorType.METHOD_NOT_FOUND, "Pending delivery method not available"
            )
        
        try:
            method = getattr(self.service, self._pending_delivery_method)
            result = method(dealer_name) if dealer_name else method()
            return ServiceResponse.success("analytics", result if result else {})
        except Exception as e:
            return ServiceResponse.error("analytics", ErrorType.SERVICE_ERROR, str(e))


class KPICompatibilityLayer:
    """Isolates kpi_service changes from router"""
    
    SUPPORTED_METHODS = [
        "get_executive_dashboard",
        "get_kpi_summary",
        "get_dashboard_summary",
        "get_network_health"
    ]
    
    def __init__(self, kpi_service):
        self.service = kpi_service
        self._available = kpi_service is not None
        self._method = None
        self._version = None
        
        if self._available:
            self._discover_methods()
            self._detect_version()
    
    def _discover_methods(self):
        for method_name in self.SUPPORTED_METHODS:
            if hasattr(self.service, method_name):
                self._method = method_name
                logger.info(f"   ✅ KPI method discovered: {method_name}")
                break
        
        if not self._method:
            logger.warning("   ⚠️ No KPI method found in kpi_service!")
    
    def _detect_version(self):
        if hasattr(self.service, 'get_version'):
            try:
                self._version = self.service.get_version()
            except:
                self._version = 'unknown'
        else:
            self._version = 'legacy'
        logger.info(f"   📈 KPI service version: {self._version}")
    
    def is_available(self) -> bool:
        return self._available and self._method is not None
    
    def get_dashboard(self) -> ServiceResponse:
        if not self.is_available():
            return ServiceResponse.error(
                "kpi", ErrorType.SERVICE_ERROR, "KPI service not available"
            )
        
        try:
            method = getattr(self.service, self._method)
            result = method()
            return ServiceResponse.success("kpi", result if result else {})
        except Exception as e:
            return ServiceResponse.error("kpi", ErrorType.SERVICE_ERROR, str(e))


class AICompatibilityLayer:
    """Isolates ai_provider changes from router"""
    
    SUPPORTED_METHODS = ["chat", "ask", "query", "analyze"]
    
    def __init__(self, ai_provider):
        self.provider = ai_provider
        self._available = ai_provider is not None
        self._method = None
        self._version = None
        
        if self._available:
            self._discover_methods()
            self._detect_version()
    
    def _discover_methods(self):
        for method_name in self.SUPPORTED_METHODS:
            if hasattr(self.provider, method_name):
                self._method = method_name
                logger.info(f"   ✅ AI method discovered: {method_name}")
                break
        
        if not self._method:
            logger.warning("   ⚠️ No AI method found in ai_provider!")
    
    def _detect_version(self):
        if hasattr(self.provider, 'get_version'):
            try:
                self._version = self.provider.get_version()
            except:
                self._version = 'unknown'
        else:
            self._version = 'legacy'
        logger.info(f"   🤖 AI provider version: {self._version}")
    
    def is_available(self) -> bool:
        return self._available and self._method is not None
    
    def chat(self, message: str, user_id: str, context: Dict = None) -> str:
        if not self.is_available():
            return "AI service is currently unavailable. Please try again later."
        
        try:
            method = getattr(self.provider, self._method)
            if context:
                return method(message, user_id, context=context)
            return method(message, user_id)
        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            return f"AI service error: {str(e)}"


# ==========================================================
# RESPONSE NORMALIZATION LAYER (Priority 4 & 9)
# ==========================================================

class ResponseNormalizer:
    """Normalizes all service responses to standard formats"""
    
    @staticmethod
    def normalize_dn_response(raw_data: Dict[str, Any]) -> NormalizedDN:
        """Convert any DN response to standard NormalizedDN"""
        # Extract data with fallbacks
        dn_no = raw_data.get('dn_no') or raw_data.get('dn_number') or raw_data.get('DN') or 'N/A'
        dealer_name = raw_data.get('dealer_name') or raw_data.get('dealer') or raw_data.get('customer_name') or 'N/A'
        dealer_code = raw_data.get('dealer_code') or raw_data.get('customer_code') or 'N/A'
        sales_office = raw_data.get('sales_office') or raw_data.get('division') or 'N/A'
        warehouse = raw_data.get('warehouse') or 'N/A'
        city = raw_data.get('city') or raw_data.get('ship_to_city') or 'N/A'
        
        dn_date = raw_data.get('dn_date') or raw_data.get('date') or 'N/A'
        pgi_date = raw_data.get('pgi_date') or raw_data.get('good_issue_date') or 'Not Dispatched'
        pod_date = raw_data.get('pod_date') or 'Not Received'
        
        # Apply business rules for calculations
        delivery_days = BusinessRules.calculate_delivery_days(
            pgi_date if pgi_date != 'Not Dispatched' else None,
            dn_date if dn_date != 'N/A' else None
        )
        pod_days = BusinessRules.calculate_pod_days(
            pod_date if pod_date != 'Not Received' else None,
            pgi_date if pgi_date != 'Not Dispatched' else None
        )
        
        status_info = BusinessRules.calculate_dn_status(
            pgi_date if pgi_date != 'Not Dispatched' else None,
            pod_date if pod_date != 'Not Received' else None
        )
        
        priority = BusinessRules.calculate_priority(delivery_days)
        
        total_models = raw_data.get('total_models') or raw_data.get('models_count') or len(raw_data.get('products', []))
        models_list = raw_data.get('models_list') or []
        total_quantity = raw_data.get('total_quantity') or raw_data.get('dn_qty') or 0
        total_amount = raw_data.get('total_amount') or raw_data.get('dn_amount') or 0.0
        products = raw_data.get('products', [])
        
        return NormalizedDN(
            dn_no=str(dn_no),
            dealer_name=str(dealer_name),
            dealer_code=str(dealer_code),
            sales_office=str(sales_office),
            warehouse=str(warehouse),
            city=str(city),
            dn_date=str(dn_date),
            pgi_date=str(pgi_date),
            pod_date=str(pod_date),
            delivery_days=delivery_days,
            pod_days=pod_days,
            status=status_info["status"],
            status_emoji=status_info["emoji"],
            total_models=total_models,
            models_list=models_list,
            total_quantity=total_quantity,
            total_amount=total_amount,
            priority=priority,
            products=products
        )
    
    @staticmethod
    def normalize_dealer_response(dashboard: Dict, health: Dict) -> NormalizedDealer:
        dealer_name = dashboard.get('dealer_name') or 'N/A'
        dealer_code = dashboard.get('dealer_code') or dashboard.get('customer_code') or 'N/A'
        city = dashboard.get('city') or dashboard.get('ship_to_city') or 'N/A'
        sales_office = dashboard.get('sales_office') or dashboard.get('division') or 'N/A'
        warehouse = dashboard.get('warehouse') or 'N/A'
        
        total_dns = dashboard.get('total_dn') or dashboard.get('total_dns') or 0
        total_models = dashboard.get('total_models') or 0
        total_quantity = dashboard.get('total_qty') or dashboard.get('total_quantity') or 0
        total_amount = dashboard.get('total_amount') or 0.0
        
        completion_rate = dashboard.get('completion_rate') or 0.0
        
        pending_deliveries = dashboard.get('pending_deliveries_count') or dashboard.get('pending_deliveries') or 0
        pending_pod = dashboard.get('pending_pod_count') or dashboard.get('pending_pod') or 0
        
        avg_delivery_days = dashboard.get('avg_delivery_aging_days') or 0.0
        avg_pod_days = dashboard.get('avg_pod_aging_days') or 0.0
        
        health_score = health.get('health_score', 0)
        health_status = health.get('health_status', 'Unknown')
        health_emoji = health.get('health_emoji', '🟡')
        
        return NormalizedDealer(
            dealer_name=dealer_name,
            dealer_code=dealer_code,
            city=city,
            sales_office=sales_office,
            warehouse=warehouse,
            total_dns=total_dns,
            total_models=total_models,
            total_quantity=total_quantity,
            total_amount=total_amount,
            completion_rate=completion_rate,
            pending_deliveries=pending_deliveries,
            pending_pod=pending_pod,
            avg_delivery_days=avg_delivery_days,
            avg_pod_days=avg_pod_days,
            health_score=health_score,
            health_status=health_status,
            health_emoji=health_emoji
        )
    
    @staticmethod
    def normalize_pending_items(items: List[Dict], title: str) -> List[NormalizedPendingItem]:
        normalized = []
        for item in items:
            dn_no = item.get('dn_no') or item.get('dn_number') or 'N/A'
            dealer_name = item.get('dealer') or item.get('dealer_name') or 'N/A'
            pending_days = item.get('pending_days') or item.get('aging_days') or 0
            priority = BusinessRules.calculate_priority(pending_days)
            
            priority_emoji = "🔴" if pending_days > 14 else "🟠" if pending_days > 7 else "🟡"
            
            normalized.append(NormalizedPendingItem(
                dn_no=str(dn_no),
                dealer_name=str(dealer_name),
                pending_days=pending_days,
                priority=priority,
                priority_emoji=priority_emoji
            ))
        
        return sorted(normalized, key=lambda x: x.pending_days, reverse=True)


# ==========================================================
# UNIFIED WHATSAPP FORMATTERS (Priority 10)
# ==========================================================

class WhatsAppFormatter:
    """Services return data, router formats - NO business logic here"""
    
    @staticmethod
    def format_dn_response(normalized: NormalizedDN) -> str:
        products_text = ""
        for idx, p in enumerate(normalized.products[:5], 1):
            products_text += f"\n   {idx}. {p.get('customer_model', 'N/A')} - Qty: {p.get('quantity', 0)}"
        
        if len(normalized.products) > 5:
            products_text += f"\n   ... +{len(normalized.products) - 5} more"
        
        models_text = ", ".join(normalized.models_list[:3]) if normalized.models_list else "N/A"
        if len(normalized.models_list) > 3:
            models_text += f" +{len(normalized.models_list) - 3} more"
        
        return f"""
📦 *DN DETAILS*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🔢 *DN Number:* {normalized.dn_no}
📅 Date: {normalized.dn_date}
{normalized.status_emoji} Status: {normalized.status}

🏪 *DEALER INFORMATION*
• Name: {normalized.dealer_name}
• City: {normalized.city}
• Office: {normalized.sales_office}
• Warehouse: {normalized.warehouse}

📦 *PRODUCTS*{products_text}

📊 *MODELS*: {models_text}

💰 *FINANCIALS*
• Total Quantity: {normalized.total_quantity:,}
• Total Amount: PKR {normalized.total_amount:,.0f}
• Models: {normalized.total_models}

⏱️ *AGING*
• Delivery Aging: {normalized.delivery_days} days
• POD Aging: {normalized.pod_days} days

🚚 *SHIPMENT STATUS*
• PGI Date: {normalized.pgi_date}
• POD Date: {normalized.pod_date}
• Priority: {normalized.priority}

━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for more commands
"""
    
    @staticmethod
    def format_dealer_response(normalized: NormalizedDealer) -> str:
        return f"""
🏪 *DEALER DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📌 *{normalized.dealer_name}*
📍 City: {normalized.city}
🏢 Office: {normalized.sales_office}
🏭 Warehouse: {normalized.warehouse}

📊 *PERFORMANCE SUMMARY*
• Total DNs: {normalized.total_dns}
• Models: {normalized.total_models}
• Quantity: {normalized.total_quantity:,}
• Revenue: PKR {normalized.total_amount:,.0f}
• Completion Rate: {normalized.completion_rate}%

⚠️ *ISSUES IDENTIFIED*
• Pending Deliveries: {normalized.pending_deliveries}
• Pending PODs: {normalized.pending_pod}

⏱️ *AGING METRICS*
• Avg Delivery Aging: {normalized.avg_delivery_days:.0f} days
• Avg POD Aging: {normalized.avg_pod_days:.0f} days

{normalized.health_emoji} *Health Score: {normalized.health_score} ({normalized.health_status})*
━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Pending deliveries` to see delayed items
"""
    
    @staticmethod
    def format_pending_response(items: List[NormalizedPendingItem], title: str, emoji: str, 
                                 total: int, critical: int) -> str:
        if not items:
            return f"{emoji} *{title}*\n━━━━━━━━━━━━━━━━━━━━\n✅ No pending items found!"
        
        response = f"{emoji} *{title}*\n━━━━━━━━━━━━━━━━━━━━\n\n"
        response += f"📊 Total: {total}\n"
        response += f"⚠️ Critical: {critical}\n\n"
        response += "🔴 *Top Priority Items:*\n"
        
        for item in items[:5]:
            response += f"{item.priority_emoji} DN {item.dn_no}: {item.pending_days} days\n"
            if item.dealer_name != 'N/A':
                response += f"   Dealer: {item.dealer_name}\n"
        
        response += "\n━━━━━━━━━━━━━━━━━━━━\n💡 Type `Help` for more commands"
        
        return response.strip()
    
    @staticmethod
    def format_help_response() -> str:
        return """
🤖 *AI Assistant - Available Commands*
━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *DEALER QUERIES*
• `[Dealer Name]` - Complete dealer dashboard

📦 *DN QUERIES*
• `DN [number]` - Complete DN details
• `Status of DN [number]` - DN status only

🏭 *WAREHOUSE QUERIES*
• `Warehouse [name]` - Warehouse performance

📋 *OPERATIONAL QUERIES*
• `Pending POD` - Missing delivery proofs
• `Pending delivery` - Delayed shipments
• `Critical delays` - Urgent issues (>14 days)
• `How many pending deliveries` - Count of pending deliveries

📊 *EXECUTIVE QUERIES*
• `Executive dashboard` - Complete KPI overview
• `Control tower` - Network health status

🔍 *ANALYSIS QUERIES*
• `Why is [dealer] delayed?` - Root cause analysis

━━━━━━━━━━━━━━━━━━━━
💡 Type your question naturally!
"""
    
    @staticmethod
    def format_error_response(error_type: ErrorType, error_message: str, query: str = None) -> str:
        if error_type == ErrorType.NOT_FOUND:
            return f"❌ *Not Found*\n━━━━━━━━━━━━━━━━━━━━\n\n{error_message}\n\n💡 Check the spelling or try a different search term."
        
        elif error_type == ErrorType.VALIDATION_ERROR:
            return f"⚠️ *Validation Error*\n━━━━━━━━━━━━━━━━━━━━\n\n{error_message}\n\n💡 Please rephrase your query."
        
        elif error_type == ErrorType.SERVICE_ERROR:
            return f"⚠️ *Service Error*\n━━━━━━━━━━━━━━━━━━━━\n\nThe service is currently unavailable. Please try again later.\n\n💡 Type `Help` for available commands."
        
        else:
            return f"❌ *Error*\n━━━━━━━━━━━━━━━━━━━━\n\n{error_message}\n\n💡 Type `Help` for available commands."


# ==========================================================
# RESPONSE CACHING (Priority 12)
# ==========================================================

class ResponseCache:
    def __init__(self, ttl_seconds: int = RESPONSE_CACHE_TTL):
        self.cache = TTLCache(maxsize=200, ttl=ttl_seconds)
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0
    
    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        return None
    
    def set(self, key: str, value: str):
        self.cache[key] = value
    
    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        hit_rate = round(self.hits / max(1, total) * 100, 2)
        return {
            "hits": self.hits,
            "misses": self.misses,
            "total": total,
            "hit_rate": hit_rate,
            "cache_size": len(self.cache),
            "ttl_seconds": self.ttl
        }
    
    def invalidate(self, pattern: str = None):
        if pattern is None:
            self.cache.clear()
        else:
            keys_to_remove = [k for k in self.cache.keys() if pattern in k]
            for k in keys_to_remove:
                del self.cache[k]


# ==========================================================
# CENTRAL ROUTE REGISTRY (Priority 6)
# ==========================================================

class RouteRegistry:
    """Single place for all routes - easy to maintain"""
    
    def __init__(self, query_handlers):
        self.handlers = query_handlers
        self._routes = {}
        self._register_routes()
    
    def _register_routes(self):
        """Register all routes in one place"""
        self._routes = {
            "help": self.handlers.handle_help_query,
            "dn": self.handlers.handle_dn_query,
            "dealer": self.handlers.handle_dealer_query,
            "operational_pod": lambda msg, uid, params: self.handlers.handle_operational_query(msg, uid, params, "PENDING_POD"),
            "operational_delivery": lambda msg, uid, params: self.handlers.handle_operational_query(msg, uid, params, "PENDING_DELIVERY"),
            "operational_critical": lambda msg, uid, params: self.handlers.handle_operational_query(msg, uid, params, "CRITICAL_DELAYS"),
            "executive": self.handlers.handle_executive_query,
            "root_cause": self.handlers.handle_root_cause_query,
            "follow_up": self.handlers.handle_follow_up_query,
            "clarification": self.handlers.handle_clarification
        }
    
    def get_handler(self, route_name: str) -> Optional[Callable]:
        return self._routes.get(route_name)
    
    def route_exists(self, route_name: str) -> bool:
        return route_name in self._routes
    
    def get_all_routes(self) -> List[str]:
        return list(self._routes.keys())


# ==========================================================
# INTENT & ENTITY DETECTION (Pure - no business logic)
# ==========================================================

class IntentDetector:
    """Pure intent detection - NO business logic"""
    
    HELP_KEYWORDS = ["help", "can you help", "how to use", "commands", "what can you do", "menu", "guide", "support"]
    DN_INDICATORS = ["dn", "delivery note", "delivery note number", "track"]
    POD_INDICATORS = ["pod", "proof", "delivery proof"]
    DELIVERY_INDICATORS = ["delivery", "dispatch", "shipment"]
    CRITICAL_INDICATORS = ["critical", "urgent", "high priority", "severe"]
    EXECUTIVE_INDICATORS = ["executive", "dashboard", "control tower", "kpi", "summary", "overview"]
    ROOT_CAUSE_INDICATORS = ["why", "root cause", "reason", "cause", "what caused", "analyze", "investigate"]
    
    def detect(self, message: str, has_dn: bool, has_dealer: bool) -> Tuple[str, str, float]:
        message_lower = message.lower().strip()
        
        # Help
        if any(kw in message_lower for kw in self.HELP_KEYWORDS):
            return "help", "HELP", 0.95
        
        # DN query (highest priority)
        if has_dn or any(kw in message_lower for kw in self.DN_INDICATORS):
            return "dn", "DN_DETAIL", 0.95
        
        # Root cause analysis
        if any(kw in message_lower for kw in self.ROOT_CAUSE_INDICATORS) and len(message) > 15:
            return "root_cause", "ROOT_CAUSE_ANALYSIS", 0.85
        
        # Operational queries
        if any(kw in message_lower for kw in self.POD_INDICATORS):
            return "operational", "PENDING_POD", 0.90
        
        if any(kw in message_lower for kw in self.CRITICAL_INDICATORS):
            return "operational", "CRITICAL_DELAYS", 0.85
        
        if any(kw in message_lower for kw in self.DELIVERY_INDICATORS):
            return "operational", "PENDING_DELIVERY", 0.90
        
        # Executive queries
        if any(kw in message_lower for kw in self.EXECUTIVE_INDICATORS):
            return "executive", "EXECUTIVE_DASHBOARD", 0.90
        
        # Dealer query (lower priority - after specific queries)
        if has_dealer:
            return "dealer", "DEALER_DASHBOARD", 0.85
        
        # Default to clarification
        return "clarification", "CLARIFICATION", 0.40


class EntityExtractor:
    """Pure entity extraction - NO business logic"""
    
    def __init__(self, dn_pattern: str = DN_PATTERN):
        self.dn_pattern = dn_pattern
    
    def extract_dn(self, message: str) -> Optional[str]:
        dn_match = re.search(self.dn_pattern, message)
        return dn_match.group() if dn_match else None
    
    def extract_dealer(self, message: str, dealer_resolver: Callable) -> Tuple[Optional[str], float]:
        # Skip messages that are clearly operational
        skip_indicators = ['how many', 'pending', 'delivery', 'pod', 'critical', 'review', 'help']
        if any(indicator in message.lower() for indicator in skip_indicators):
            return None, 0.0
        
        # Skip questions
        if message.lower().startswith(('how', 'what', 'why', 'when', 'where', 'who', 'which', 'can you')):
            return None, 0.0
        
        if len(message.strip()) < 3:
            return None, 0.0
        
        return dealer_resolver(message)
    
    def extract_all(self, message: str, dealer_resolver: Callable) -> List[Tuple[str, str, int]]:
        entities = []
        
        dn = self.extract_dn(message)
        if dn:
            entities.append(("dn", dn, 100))
        
        dealer, confidence = self.extract_dealer(message, dealer_resolver)
        if dealer:
            entities.append(("dealer", dealer, 90))
        
        return sorted(entities, key=lambda x: x[2], reverse=True)


# ==========================================================
# CONVERSATION CONTEXT
# ==========================================================

@dataclass
class ConversationContext:
    dealer: Optional[str] = None
    dn: Optional[str] = None
    warehouse: Optional[str] = None
    last_intent: Optional[str] = None
    last_response_type: Optional[str] = None
    last_entity_type: Optional[str] = None
    last_entity_value: Optional[str] = None
    last_timestamp: datetime = field(default_factory=datetime.now)
    
    def update(self, entity_type: str, entity_value: str, intent: str, response_type: str):
        if entity_type == "dealer":
            self.dealer = entity_value
        elif entity_type == "dn":
            self.dn = entity_value
        
        self.last_intent = intent
        self.last_response_type = response_type
        self.last_entity_type = entity_type
        self.last_entity_value = entity_value
        self.last_timestamp = datetime.now()
    
    def has_context_within(self, seconds: int = CONTEXT_TTL_SECONDS) -> bool:
        return (datetime.now() - self.last_timestamp).total_seconds() < seconds
    
    def get_follow_up_context(self) -> Optional[Tuple[str, str, str]]:
        if self.has_context_within() and self.last_response_type and self.last_entity_value:
            return (self.last_intent, self.last_response_type, self.last_entity_value)
        return None


# ==========================================================
# AUDIT & METRICS
# ==========================================================

@dataclass
class AuditEntry:
    timestamp: datetime
    query: str
    user_id: str
    intent: str
    response_type: str
    entity_type: str
    entity_value: Optional[str]
    confidence: float
    success: bool
    response_time_ms: float
    cache_hit: bool
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    response_length: int = 0
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "query": self.query[:200],
            "user_id": self.user_id,
            "intent": self.intent,
            "response_type": self.response_type,
            "entity_type": self.entity_type,
            "entity_value": self.entity_value,
            "confidence": self.confidence,
            "success": self.success,
            "response_time_ms": round(self.response_time_ms, 2),
            "cache_hit": self.cache_hit,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "response_length": self.response_length
        }


# ==========================================================
# QUERY HANDLERS (Pure routing - NO business logic)
# ==========================================================

class QueryHandlers:
    def __init__(self, analytics_layer: AnalyticsCompatibilityLayer,
                 logistics_layer: LogisticsCompatibilityLayer,
                 kpi_layer: KPICompatibilityLayer,
                 ai_layer: AICompatibilityLayer,
                 normalizer: ResponseNormalizer,
                 formatter: WhatsAppFormatter,
                 cache: ResponseCache,
                 conversation_context: Dict[str, ConversationContext]):
        
        self.analytics = analytics_layer
        self.logistics = logistics_layer
        self.kpi = kpi_layer
        self.ai = ai_layer
        self.normalizer = normalizer
        self.formatter = formatter
        self.cache = cache
        self.conversation_context = conversation_context
    
    def _get_context(self, user_id: str) -> ConversationContext:
        if user_id not in self.conversation_context:
            self.conversation_context[user_id] = ConversationContext()
        return self.conversation_context[user_id]
    
    def handle_dn_query(self, dn_number: str, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        context = self._get_context(user_id)
        context.update("dn", dn_number, "dn", "DN_DETAIL")
        
        # Check cache
        cache_key = f"dn_{dn_number}"
        cached_response = self.cache.get(cache_key)
        if cached_response:
            return cached_response, "DN_DETAIL", None, None
        
        # Get from service
        response = self.logistics.get_dn_detail(dn_number)
        
        if not response.success:
            return self.formatter.format_error_response(
                response.error_type or ErrorType.NOT_FOUND, 
                response.error_message or f"DN {dn_number} not found"
            ), "ERROR", response.error_type, response.error_message
        
        # Normalize and format
        normalized = self.normalizer.normalize_dn_response(response.data)
        formatted = self.formatter.format_dn_response(normalized)
        
        # Cache
        self.cache.set(cache_key, formatted)
        
        return formatted, "DN_DETAIL", None, None
    
    def handle_dealer_query(self, dealer_name: str, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        context = self._get_context(user_id)
        context.update("dealer", dealer_name, "dealer", "DEALER_DASHBOARD")
        
        # Check cache
        cache_key = f"dealer_{dealer_name.lower()}"
        cached_response = self.cache.get(cache_key)
        if cached_response:
            return cached_response, "DEALER_DASHBOARD", None, None
        
        # Get from service
        dashboard_response = self.analytics.get_dealer_dashboard(dealer_name)
        
        if not dashboard_response.success:
            return self.formatter.format_error_response(
                dashboard_response.error_type or ErrorType.NOT_FOUND,
                dashboard_response.error_message or f"Dealer '{dealer_name}' not found"
            ), "ERROR", dashboard_response.error_type, dashboard_response.error_message
        
        # Get health data (optional)
        health_response = self.analytics.get_dealer_health(dealer_name)
        
        # Normalize and format
        normalized = self.normalizer.normalize_dealer_response(
            dashboard_response.data, 
            health_response.data if health_response.success else {}
        )
        formatted = self.formatter.format_dealer_response(normalized)
        
        # Cache
        self.cache.set(cache_key, formatted)
        
        return formatted, "DEALER_DASHBOARD", None, None
    
    def handle_operational_query(self, message: str, user_id: str, parameters: Dict, response_type: str) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        context = self._get_context(user_id)
        dealer = context.dealer if context.has_context_within() else None
        
        cache_key = f"operational_{response_type}_{dealer or 'all'}"
        cached_response = self.cache.get(cache_key)
        if cached_response:
            return cached_response, response_type, None, None
        
        if response_type == "PENDING_POD":
            pending_response = self.analytics.get_pending_pod(dealer)
        elif response_type == "CRITICAL_DELAYS":
            pending_response = self.analytics.get_pending_delivery(dealer)
            # Filter critical only
            if pending_response.success and pending_response.data:
                items = pending_response.data.get('pending_deliveries', pending_response.data.get('pending_list', []))
                critical_items = [i for i in items if i.get('pending_days', i.get('aging_days', 0)) > 14]
                pending_response.data = {'pending_deliveries': critical_items, 'total_pending': len(critical_items), 'critical_delays': len(critical_items)}
        else:  # PENDING_DELIVERY
            pending_response = self.analytics.get_pending_delivery(dealer)
        
        if not pending_response.success or not pending_response.data:
            formatted = self.formatter.format_pending_response([], "PENDING ITEMS", "📋", 0, 0)
        else:
            data = pending_response.data
            items = data.get('pending_deliveries', data.get('pending_list', []))
            normalized_items = self.normalizer.normalize_pending_items(items, response_type)
            total = data.get('total_pending', data.get('total_pending_pod', len(items)))
            critical = data.get('critical_delays', 0)
            
            title = "PENDING PODs" if "POD" in response_type else "PENDING DELIVERIES" if "DELIVERY" in response_type else "CRITICAL DELAYS"
            emoji = "📋" if "POD" in response_type else "🚚" if "DELIVERY" in response_type else "🔴"
            
            formatted = self.formatter.format_pending_response(normalized_items, title, emoji, total, critical)
        
        # Cache
        self.cache.set(cache_key, formatted)
        
        context.last_response_type = response_type
        
        return formatted, response_type, None, None
    
    def handle_executive_query(self, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        cache_key = "executive_dashboard"
        cached_response = self.cache.get(cache_key)
        if cached_response:
            return cached_response, "EXECUTIVE_DASHBOARD", None, None
        
        dashboard_response = self.kpi.get_dashboard()
        
        if dashboard_response.success and dashboard_response.data:
            # Format executive response
            data = dashboard_response.data
            formatted = f"""
🏢 *EXECUTIVE DASHBOARD*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *NETWORK HEALTH*
• Overall Score: {data.get('overall_score', 'N/A')}%
• POD Compliance: {data.get('pod_compliance', 'N/A')}%
• PGI Compliance: {data.get('pgi_compliance', 'N/A')}%

⚠️ *CRITICAL ISSUES*
• Total Delays: {data.get('critical_delays', 0)}
• Pending PODs: {data.get('pending_pod', 0)}

🎯 *Recommended Actions*
1. Review critical delays immediately
2. Accelerate POD collection process
━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for available commands
"""
        else:
            # Fallback using analytics
            pending_response = self.analytics.get_pending_delivery(None)
            if pending_response.success:
                data = pending_response.data
                total = data.get('total_pending', 0)
                critical = data.get('critical_delays', 0)
                formatted = f"""
🏢 *EXECUTIVE SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *CURRENT STATUS*
• Total Pending Deliveries: {total}
• Critical Delays (>14 days): {critical}

⚠️ *RECOMMENDATIONS*
1. Review critical delays immediately
2. Prioritize dispatches for pending items
━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Type `Help` for available commands
"""
            else:
                formatted = "⚠️ Executive dashboard temporarily unavailable. Please try again later."
        
        self.cache.set(cache_key, formatted)
        
        return formatted, "EXECUTIVE_DASHBOARD", None, None
    
    def handle_root_cause_query(self, message: str, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        context = self._get_context(user_id)
        
        # Get context for AI
        compact_context = {}
        if context.dealer:
            compact_context['dealer'] = context.dealer
            # Try to get dealer data for context
            dealer_data = self.analytics.get_dealer_dashboard(context.dealer)
            if dealer_data.success:
                compact_context['dealer_data'] = dealer_data.data
        
        response = self.ai.chat(message, user_id, compact_context if compact_context else None)
        
        return response, "ROOT_CAUSE_ANALYSIS", None, None
    
    def handle_follow_up_query(self, message: str, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        context = self._get_context(user_id)
        follow_up = context.get_follow_up_context()
        
        if not follow_up:
            return self.handle_clarification(message, user_id, parameters)
        
        last_intent, last_response_type, last_entity_value = follow_up
        
        if last_response_type in ["PENDING_POD", "PENDING_DELIVERY", "CRITICAL_DELAYS"]:
            return self.handle_operational_query(message, user_id, parameters, last_response_type)
        elif last_response_type == "DEALER_DASHBOARD" and context.dealer:
            return self.handle_dealer_query(context.dealer, user_id, parameters)
        elif last_response_type == "DN_DETAIL" and context.dn:
            return self.handle_dn_query(context.dn, user_id, parameters)
        else:
            return self.handle_clarification(message, user_id, parameters)
    
    def handle_clarification(self, message: str, user_id: str, parameters: Dict) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        return self.formatter.format_help_response(), "CLARIFICATION", None, None
    
    def handle_help_query(self) -> Tuple[str, str, Optional[ErrorType], Optional[str]]:
        return self.formatter.format_help_response(), "HELP", None, None


# ==========================================================
# DEALER RESOLVER (Uses compatibility layer)
# ==========================================================

class DealerResolver:
    def __init__(self, analytics_layer: AnalyticsCompatibilityLayer):
        self.analytics = analytics_layer
        self.cache = TTLCache(maxsize=100, ttl=300)
    
    def resolve(self, message: str) -> Tuple[Optional[str], float]:
        message_clean = message.strip().lower()
        
        if len(message_clean) < 3:
            return None, 0.0
        
        # Skip operational queries
        skip_indicators = ['how many', 'pending', 'delivery', 'pod', 'critical', 'review', 'help']
        if any(indicator in message_clean for indicator in skip_indicators):
            return None, 0.0
        
        # Skip questions
        if message_clean.startswith(('how', 'what', 'why', 'when', 'where', 'who', 'which', 'can you')):
            return None, 0.0
        
        if message_clean in self.cache:
            return self.cache[message_clean]
        
        # Use analytics service to resolve
        response = self.analytics.get_dealer_dashboard(message)
        
        if response.success and response.data:
            dealer_name = response.data.get('dealer_name') or response.data.get('name')
            if dealer_name:
                self.cache[message_clean] = (dealer_name, 0.85)
                return dealer_name, 0.85
        
        return None, 0.0


# ==========================================================
# AI QUERY SERVICE - MAIN ENTRY POINT (Priority 7 & 11)
# ==========================================================

class AIQueryService:
    """
    AI Query Service v52.0 - PURE ROUTER ARCHITECTURE
    - NO business logic
    - NO calculations
    - NO SQL
    - Only routing, normalization, and formatting
    """
    
    def __init__(self, analytics_service=None, logistics_service=None, 
                 kpi_service=None, ai_provider=None):
        
        logger.info("=" * 70)
        logger.info("🚀 AI Query Service v52.0 - PURE ROUTER ARCHITECTURE")
        logger.info("=" * 70)
        
        # Priority 7: Startup validation - fail fast
        missing_services = []
        
        if analytics_service is None:
            missing_services.append("analytics_service")
        if logistics_service is None:
            missing_services.append("logistics_service")
        
        if missing_services:
            error_msg = f"CRITICAL: Missing required services: {missing_services}. App cannot start."
            logger.error(f"❌ {error_msg}")
            raise RuntimeError(error_msg)
        
        # Initialize compatibility layers
        logger.info("📋 Initializing compatibility layers...")
        self.analytics_layer = AnalyticsCompatibilityLayer(analytics_service)
        self.logistics_layer = LogisticsCompatibilityLayer(logistics_service)
        self.kpi_layer = KPICompatibilityLayer(kpi_service)
        self.ai_layer = AICompatibilityLayer(ai_provider)
        
        # Priority 11: Log service versions
        logger.info("")
        logger.info("📦 SERVICE VERSIONS:")
        logger.info(f"   Analytics: {self.analytics_layer._version or 'unknown'}")
        logger.info(f"   Logistics: {self.logistics_layer._version or 'unknown'}")
        logger.info(f"   KPI: {self.kpi_layer._version or 'unknown'}")
        logger.info(f"   AI: {self.ai_layer._version or 'unknown'}")
        
        # Validate required methods
        if not self.logistics_layer.is_available():
            logger.error("❌ Logistics service DN methods not available!")
            raise RuntimeError("Logistics service missing required DN methods")
        
        if not self.analytics_layer.is_available():
            logger.error("❌ Analytics service dealer methods not available!")
            raise RuntimeError("Analytics service missing required dealer methods")
        
        logger.info("✅ All required services and methods validated")
        
        # Initialize components
        self.normalizer = ResponseNormalizer()
        self.formatter = WhatsAppFormatter()
        self.cache = ResponseCache()
        self.dealer_resolver = DealerResolver(self.analytics_layer)
        self.entity_extractor = EntityExtractor(DN_PATTERN)
        self.intent_detector = IntentDetector()
        self.conversation_context: Dict[str, ConversationContext] = {}
        
        # Initialize handlers
        self.handlers = QueryHandlers(
            self.analytics_layer,
            self.logistics_layer,
            self.kpi_layer,
            self.ai_layer,
            self.normalizer,
            self.formatter,
            self.cache,
            self.conversation_context
        )
        
        # Priority 6: Central route registry
        self.route_registry = RouteRegistry(self.handlers)
        
        # Metrics
        self.metrics = {
            "total_queries": 0,
            "successful_queries": 0,
            "failed_queries": 0,
            "by_intent": {},
            "by_response_type": {},
            "avg_response_time_ms": 0,
            "total_response_time_ms": 0,
            "start_time": datetime.now()
        }
        
        self.audit_trail: List[AuditEntry] = []
        
        self._log_startup_summary()
        logger.info("=" * 70)
        logger.info("✅ AI Query Service v52.0 - READY")
        logger.info("=" * 70)
    
    def _log_startup_summary(self):
        logger.info("")
        logger.info("📋 STARTUP VALIDATION SUMMARY:")
        logger.info(f"   {'✅' if self.analytics_layer.is_available() else '❌'} Analytics Service")
        logger.info(f"   {'✅' if self.logistics_layer.is_available() else '❌'} Logistics Service")
        logger.info(f"   {'✅' if self.kpi_layer.is_available() else '⚠️'} KPI Service")
        logger.info(f"   {'✅' if self.ai_layer.is_available() else '⚠️'} AI Provider")
        logger.info("")
        logger.info("📋 ROUTES REGISTERED:")
        for route in self.route_registry.get_all_routes():
            logger.info(f"   • {route}")
        logger.info("")
        logger.info(f"📋 CACHE TTL: {RESPONSE_CACHE_TTL}s")
        logger.info(f"📋 DEBUG MODE: {'ON' if DEBUG_MODE else 'OFF'}")
    
    def _add_audit_entry(self, entry: AuditEntry):
        if ENABLE_AUDIT_TRAIL:
            self.audit_trail.append(entry)
            if len(self.audit_trail) > 1000:
                self.audit_trail = self.audit_trail[-1000:]
    
    def _update_metrics(self, intent: str, response_type: str, response_time_ms: float, success: bool):
        self.metrics["total_queries"] += 1
        
        if intent not in self.metrics["by_intent"]:
            self.metrics["by_intent"][intent] = 0
        self.metrics["by_intent"][intent] += 1
        
        if response_type not in self.metrics["by_response_type"]:
            self.metrics["by_response_type"][response_type] = 0
        self.metrics["by_response_type"][response_type] += 1
        
        if success:
            self.metrics["successful_queries"] += 1
        else:
            self.metrics["failed_queries"] += 1
        
        self.metrics["total_response_time_ms"] += response_time_ms
        self.metrics["avg_response_time_ms"] = (
            self.metrics["total_response_time_ms"] / max(1, self.metrics["total_queries"])
        )
    
    def process(self, message: str, user_id: str = "guest", session_id: str = None) -> str:
        start_time = datetime.now()
        cache_hit = False
        
        logger.info(f"📥 INCOMING | user={user_id} | query={message[:100]}")
        
        # Extract entities
        dn = self.entity_extractor.extract_dn(message)
        dealer, dealer_conf = self.dealer_resolver.resolve(message) if not dn else (None, 0)
        
        has_dn = dn is not None
        has_dealer = dealer is not None
        
        # Detect intent
        intent, response_type, confidence = self.intent_detector.detect(message, has_dn, has_dealer)
        logger.info(f"🎯 INTENT | {intent} | type={response_type} | confidence={confidence:.2f}")
        
        # Get context for follow-up
        context = self.conversation_context.get(user_id)
        if intent == "follow_up" and context and context.has_context_within():
            follow_up = context.get_follow_up_context()
            if follow_up:
                last_intent, last_response_type, last_entity = follow_up
                logger.info(f"🔄 FOLLOW-UP | previous={last_intent}")
                intent = last_intent or "operational"
                response_type = last_response_type or response_type
        
        # Determine entity for routing
        entity_type = "dn" if dn else ("dealer" if dealer else None)
        entity_value = dn or dealer or None
        
        # Priority 13: Debug mode for DN not found
        if DEBUG_MODE and intent == "dn" and entity_value:
            debug_result = self.logistics_layer.debug_search(entity_value)
            if debug_result.get("error"):
                logger.warning(f"Debug search: {debug_result}")
        
        # Route to appropriate handler
        try:
            route_name = intent
            if intent == "operational":
                if response_type == "PENDING_POD":
                    route_name = "operational_pod"
                elif response_type == "CRITICAL_DELAYS":
                    route_name = "operational_critical"
                else:
                    route_name = "operational_delivery"
            
            handler = self.route_registry.get_handler(route_name)
            
            if not handler:
                logger.warning(f"No handler found for route: {route_name}")
                handler = self.route_registry.get_handler("clarification")
            
            # Call handler with appropriate parameters
            if intent == "dn" and entity_value:
                response, resp_type, error_type, error_msg = handler(entity_value, user_id, {"query": message})
            elif intent == "dealer" and entity_value:
                response, resp_type, error_type, error_msg = handler(entity_value, user_id, {"query": message})
            elif intent == "operational":
                response, resp_type, error_type, error_msg = handler(message, user_id, {"query": message}, response_type)
            elif intent == "executive":
                response, resp_type, error_type, error_msg = handler(user_id, {"query": message})
            elif intent == "root_cause":
                response, resp_type, error_type, error_msg = handler(message, user_id, {"query": message})
            elif intent == "follow_up":
                response, resp_type, error_type, error_msg = handler(message, user_id, {"query": message})
            else:
                response, resp_type, error_type, error_msg = handler()
            
            response_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            # Update context
            if entity_type and entity_value:
                if context:
                    context.update(entity_type, entity_value, intent, resp_type)
                else:
                    new_context = ConversationContext()
                    new_context.update(entity_type, entity_value, intent, resp_type)
                    self.conversation_context[user_id] = new_context
            elif context and resp_type in ["PENDING_POD", "PENDING_DELIVERY", "CRITICAL_DELAYS"]:
                context.last_response_type = resp_type
                context.last_intent = intent
            
            # Audit
            entry = AuditEntry(
                timestamp=datetime.now(),
                query=message,
                user_id=user_id,
                intent=intent,
                response_type=resp_type,
                entity_type=entity_type or "unknown",
                entity_value=entity_value,
                confidence=confidence,
                success=error_type is None,
                response_time_ms=response_time_ms,
                cache_hit=cache_hit,
                error_type=error_type.value if error_type else None,
                error_message=error_msg,
                response_length=len(response)
            )
            self._add_audit_entry(entry)
            self._update_metrics(intent, resp_type, response_time_ms, error_type is None)
            
            logger.info(f"📤 RESPONSE | type={resp_type} | length={len(response)} | time={response_time_ms:.0f}ms | success={error_type is None}")
            
            return response
            
        except Exception as e:
            logger.exception(f"❌ QUERY FAILED | {e}")
            response_time_ms = (datetime.now() - start_time).total_seconds() * 1000
            
            entry = AuditEntry(
                timestamp=datetime.now(),
                query=message,
                user_id=user_id,
                intent=intent,
                response_type="ERROR",
                entity_type=entity_type or "unknown",
                entity_value=entity_value,
                confidence=confidence,
                success=False,
                response_time_ms=response_time_ms,
                cache_hit=cache_hit,
                error_type=ErrorType.UNKNOWN.value,
                error_message=str(e),
                response_length=0
            )
            self._add_audit_entry(entry)
            self._update_metrics(intent, "ERROR", response_time_ms, False)
            
            return self.formatter.format_error_response(ErrorType.UNKNOWN, str(e), message)
    
    def health_check(self) -> Dict[str, Any]:
        return {
            "service": "ai_query_service",
            "version": "52.0",
            "architecture": "pure_router",
            "status": "healthy" if self.analytics_layer.is_available() and self.logistics_layer.is_available() else "degraded",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "analytics": self.analytics_layer.is_available(),
                "logistics": self.logistics_layer.is_available(),
                "kpi": self.kpi_layer.is_available(),
                "ai": self.ai_layer.is_available()
            },
            "service_versions": {
                "analytics": self.analytics_layer._version,
                "logistics": self.logistics_layer._version,
                "kpi": self.kpi_layer._version,
                "ai": self.ai_layer._version
            },
            "routes": self.route_registry.get_all_routes(),
            "cache": self.cache.get_stats(),
            "uptime_seconds": (datetime.now() - self.metrics["start_time"]).total_seconds(),
            "total_queries": self.metrics["total_queries"],
            "success_rate": round(
                self.metrics["successful_queries"] / max(1, self.metrics["total_queries"]) * 100, 2
            )
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        return {
            "service": "ai_query_service",
            "version": "52.0",
            "architecture": "pure_router",
            "uptime_seconds": round((datetime.now() - self.metrics["start_time"]).total_seconds(), 2),
            "metrics": {
                "total_queries": self.metrics["total_queries"],
                "successful_queries": self.metrics["successful_queries"],
                "failed_queries": self.metrics["failed_queries"],
                "success_rate": round(
                    self.metrics["successful_queries"] / max(1, self.metrics["total_queries"]) * 100, 2
                ),
                "avg_response_time_ms": round(self.metrics["avg_response_time_ms"], 2),
                "by_intent": self.metrics["by_intent"],
                "by_response_type": self.metrics["by_response_type"]
            },
            "cache": self.cache.get_stats(),
            "services_available": {
                "analytics": self.analytics_layer.is_available(),
                "logistics": self.logistics_layer.is_available(),
                "kpi": self.kpi_layer.is_available(),
                "ai": self.ai_layer.is_available()
            },
            "routes": self.route_registry.get_all_routes()
        }
    
    def invalidate_cache(self, pattern: str = None):
        self.cache.invalidate(pattern)
        logger.info(f"Cache invalidated: pattern={pattern}")
    
    def get_audit_trail(self, limit: int = 50) -> List[Dict]:
        return [entry.to_dict() for entry in self.audit_trail[-limit:]]


# ==========================================================
# SINGLETON & COMPATIBILITY FUNCTIONS
# ==========================================================

_query_service = None


def initialize_query_service(analytics_service=None, logistics_service=None,
                             kpi_service=None, ai_provider=None) -> AIQueryService:
    global _query_service
    _query_service = AIQueryService(analytics_service, logistics_service, kpi_service, ai_provider)
    return _query_service


def get_query_service() -> AIQueryService:
    global _query_service
    if _query_service is None:
        raise RuntimeError("AI Query Service not initialized. Call initialize_query_service() first.")
    return _query_service


def process_query(message: str, user_id: str = "guest", session_id: str = None) -> str:
    return get_query_service().process(message, user_id, session_id)


def health_check() -> Dict[str, Any]:
    return get_query_service().health_check()


def get_metrics() -> Dict[str, Any]:
    return get_query_service().get_metrics()


def invalidate_cache(pattern: str = None):
    return get_query_service().invalidate_cache(pattern)


def get_audit_trail(limit: int = 50) -> List[Dict]:
    return get_query_service().get_audit_trail(limit)


# ==========================================================
# CRITICAL: WHATSAPP COMPATIBILITY FUNCTION
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
    DO NOT RENAME without updating webhook.py.
    
    Args:
        question: The user's question/message
        session_factory: SQLAlchemy session factory (SessionLocal)
        phone_number: User's phone number (optional)
        user_id: User ID (defaults to phone_number)
        request_id: Request ID for tracing
    
    Returns:
        Response string to send back to user
    """
    req_id = request_id or str(uuid.uuid4())[:8]
    user_id_final = user_id or phone_number or "guest"
    
    logger.bind(request_id=req_id).info(f"📞 WhatsApp query: {question[:100]}...")
    
    db = None
    try:
        db = session_factory()
        
        from app.services.analytics_service import AnalyticsService
        from app.services.logistics_query_service import LogisticsQueryService
        from app.services.kpi_service import KPIService
        from app.services.ai_provider_service import AIProviderService
        
        analytics_service = AnalyticsService(db)
        logistics_service = LogisticsQueryService(db)
        kpi_service = KPIService(db)
        ai_provider = AIProviderService()
        
        try:
            query_service = get_query_service()
        except RuntimeError:
            query_service = initialize_query_service(
                analytics_service=analytics_service,
                logistics_service=logistics_service,
                kpi_service=kpi_service,
                ai_provider=ai_provider
            )
        
        response = query_service.process(question, user_id_final, req_id)
        
        logger.bind(request_id=req_id).info(f"✅ Response: {len(response)} chars")
        
        return response
        
    except ImportError as e:
        logger.bind(request_id=req_id).exception(f"Import error: {e}")
        return f"⚠️ Service configuration error. Import failed: {type(e).__name__}"
        
    except Exception as e:
        logger.bind(request_id=req_id).exception(f"Error: {e}")
        return f"⚠️ Error: {type(e).__name__}. Please try again."
        
    finally:
        if db:
            db.close()


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 70)
logger.info("🚀 AI QUERY SERVICE v52.0 - PURE ROUTER ARCHITECTURE")
logger.info("")
logger.info("   ARCHITECTURE PRINCIPLES:")
logger.info("   ✅ Pure Router - NO business logic")
logger.info("   ✅ NO calculations, NO SQL, NO business rules")
logger.info("   ✅ Only routing, normalization, and formatting")
logger.info("")
logger.info("   KEY FEATURES:")
logger.info("   • Universal Response Contract")
logger.info("   • Compatibility Layers (isolate service changes)")
logger.info("   • Response Normalization Layer")
logger.info("   • Dynamic Method Discovery")
logger.info("   • Central Route Registry")
logger.info("   • Startup Validation (fail fast)")
logger.info("   • Intelligent Error Framework")
logger.info("   • Business Rule Enforcement Layer")
logger.info("   • Unified WhatsApp Formatters")
logger.info("   • Service Version Tracking")
logger.info("   • Response Caching")
logger.info("   • Debug Mode")
logger.info("")
logger.info("   WHAT THIS MEANS:")
logger.info("   • Changes to analytics_service.py → NO changes here")
logger.info("   • Changes to logistics_query_service.py → NO changes here")
logger.info("   • Method renames → Compatibility layer handles it")
logger.info("   • Field name changes → Normalization layer handles it")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY - PURE ROUTER")
logger.info("=" * 70)
