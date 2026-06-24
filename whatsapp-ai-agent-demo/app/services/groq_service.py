# ==========================================================
# MASTER ROUTING AND ORCHESTRATION ENGINE
# ==========================================================
# File: app/services/ai_provider_service.py
# Version: 5.0 - PRODUCTION GRADE
# Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
#
# This file is the ONLY orchestration engine in the application.
# No other file makes routing decisions.
#
# Responsibilities:
# - Intent Detection
# - Entity Extraction
# - Routing Decision
# - Service Registry (Auto-Discovery)
# - Service Readiness Validation
# - Dependency Validation
# - PostgreSQL Validation
# - Groq Controller (Language Layer Only)
# - WhatsApp Response Controller
#
# This file does NOT:
# - Calculate KPIs
# - Execute SQL business analytics
# - Build dashboards
# - Generate fake data
# - Use mock data
# ==========================================================

import logging
import threading
import time
import importlib
import inspect
import re
from typing import Optional, Dict, Any, List, Tuple, Set
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import text, inspect as sa_inspect
    logger.info("✅ Database imports successful")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None


# ==========================================================
# BLOCK 2: ROUTING DECISION CLASS
# ==========================================================

@dataclass
class RoutingDecision:
    """
    Internal Routing Decision.
    
    This is the SINGLE SOURCE OF TRUTH for all routing decisions.
    """
    intent: str
    service_key: str
    method: str
    entity: Optional[str] = None
    entity2: Optional[str] = None
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    
    # Detection fields
    detected_dn: Optional[str] = None
    detected_dealer: Optional[str] = None
    detected_city: Optional[str] = None
    detected_warehouse: Optional[str] = None
    detected_product: Optional[str] = None
    detected_intent: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "intent": self.intent,
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "entity2": self.entity2,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message,
            "detected_dn": self.detected_dn,
            "detected_dealer": self.detected_dealer,
            "detected_city": self.detected_city,
            "detected_warehouse": self.detected_warehouse,
            "detected_product": self.detected_product,
            "detected_intent": self.detected_intent
        }


# ==========================================================
# BLOCK 3: SERVICE STATUS ENUM
# ==========================================================

class ServiceStatus:
    """Service status constants."""
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# ==========================================================
# BLOCK 4: POSTGRESQL VALIDATOR
# ==========================================================

class PostgreSQLValidator:
    """
    PostgreSQL Schema Validator.
    
    Validates:
    - Connection
    - delivery_reports table exists
    - Required columns exist
    - Query execution works
    """
    
    REQUIRED_COLUMNS = [
        "dn_no", "customer_name", "dealer_code", "customer_code",
        "ship_to_city", "warehouse", "warehouse_code", "delivery_location",
        "dn_qty", "dn_amount", "dn_create_date", "good_issue_date", 
        "pod_date", "delivery_status", "pgi_status", "pod_status", "pending_flag"
    ]
    
    def __init__(self):
        """Initialize PostgreSQL validator."""
        self._last_check = None
        self._cached_result = None
    
    def validate(self) -> Dict[str, Any]:
        """
        Validate PostgreSQL schema.
        
        Returns:
            Dict with validation results
        """
        result = {
            "success": False,
            "connected": False,
            "table_exists": False,
            "columns_valid": False,
            "query_executes": False,
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Check 1: Connection
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            
            # Test connection
            try:
                session.execute(text("SELECT 1"))
                result["connected"] = True
            except Exception as e:
                result["errors"].append(f"Connection test failed: {str(e)}")
                session.close()
                return result
            
            # Check 2: Table exists
            inspector = sa_inspect(session.bind)
            tables = inspector.get_table_names()
            
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                result["table_exists"] = False
                session.close()
                return result
            
            result["table_exists"] = True
            
            # Check 3: Required columns exist
            columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
            missing_columns = [col for col in self.REQUIRED_COLUMNS if col not in columns]
            
            if missing_columns:
                result["warnings"].append(f"Missing columns: {missing_columns}")
                result["columns_valid"] = False
            else:
                result["columns_valid"] = True
            
            # Check 4: Test query execution
            try:
                test_query = """
                    SELECT COUNT(*) as count 
                    FROM delivery_reports 
                    LIMIT 1
                """
                session.execute(text(test_query))
                result["query_executes"] = True
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                result["query_executes"] = False
            
            session.close()
            
            # Determine overall status
            if (result["connected"] and result["table_exists"] and 
                result["columns_valid"] and result["query_executes"]):
                result["success"] = True
            
            self._last_check = result["timestamp"]
            self._cached_result = result
            
            logger.info(f"PostgreSQL validation: success={result['success']}, "
                       f"connected={result['connected']}, "
                       f"table={result['table_exists']}, "
                       f"columns={result['columns_valid']}")
            
            return result
            
        except Exception as e:
            result["errors"].append(f"Validation failed: {str(e)}")
            logger.error(f"PostgreSQL validation error: {e}")
            return result


# ==========================================================
# BLOCK 5: SERVICE REGISTRY - AUTO-DISCOVERY & AUTO-ACTIVATION
# ==========================================================

class ServiceRegistry:
    """
    Automatic Service Registry with True Readiness Validation.
    
    Services auto-activate when fully ready.
    No manual status updates required.
    """
    
    # Service definitions
    SERVICES = {
        "dn": {
            "module": "app.services.dn_analysis",
            "class_name": "DNAnalysisService",
            "methods": [
                "get_dn_dashboard", "search_dn", "verify_dn",
                "get_pending_dns", "get_pending_pgi", "get_pending_pod",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "DN Analytics Service",
            "dependencies": []
        },
        "dealer": {
            "module": "app.services.dealer_analytics_service",
            "class_name": "DealerAnalyticsService",
            "methods": [
                "get_dealer_dashboard", "get_dealer_profile", 
                "compare_dealers", "get_top_dealers", "get_bottom_dealers",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Dealer Analytics Service",
            "dependencies": ["dn"]
        },
        "warehouse": {
            "module": "app.services.warehouse_analytics_service",
            "class_name": "WarehouseAnalyticsService",
            "methods": [
                "get_warehouse_dashboard", "get_top_warehouses",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Warehouse Analytics Service",
            "dependencies": ["dn", "dealer"]
        },
        "city": {
            "module": "app.services.city_analytics_service",
            "class_name": "CityAnalyticsService",
            "methods": [
                "get_city_dashboard", "get_top_cities",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "City Analytics Service",
            "dependencies": ["dn"]
        },
        "product": {
            "module": "app.services.product_analytics_service",
            "class_name": "ProductAnalyticsService",
            "methods": [
                "get_product_dashboard", "get_top_products",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Product Analytics Service",
            "dependencies": ["dn"]
        },
        "national_kpi": {
            "module": "app.services.national_kpi_service",
            "class_name": "NationalKPIService",
            "methods": [
                "get_national_kpi_dashboard", "get_delivery_kpis", 
                "get_warehouse_kpis",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "National KPI Service",
            "dependencies": ["dn", "dealer", "warehouse", "city", "product"]
        }
    }
    
    def __init__(self):
        """Initialize service registry."""
        self._services = self.SERVICES.copy()
        self._status_cache = {}
        self._instance_cache = {}
        self._lock = threading.Lock()
        self._last_validation = None
        self._postgresql_validator = PostgreSQLValidator()
    
    def validate_all_services(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Validate all services and update status."""
        with self._lock:
            # First, validate PostgreSQL
            pg_status = self._postgresql_validator.validate()
            pg_valid = pg_status.get("success", False)
            
            results = {}
            for service_key in self._services:
                results[service_key] = self._validate_service(
                    service_key, 
                    pg_valid=pg_valid
                )
            
            self._last_validation = time.time()
            return results
    
    def _validate_service(self, service_key: str, pg_valid: bool = False) -> Dict[str, Any]:
        """
        True readiness validation.
        
        A service is READY only when ALL checks pass.
        """
        if service_key not in self._services:
            return {
                "status": ServiceStatus.NOT_STARTED,
                "ready": False,
                "errors": [f"Service '{service_key}' not registered"]
            }
        
        service_def = self._services[service_key]
        module_name = service_def.get("module")
        class_name = service_def.get("class_name")
        required_methods = service_def.get("methods", [])
        dependencies = service_def.get("dependencies", [])
        
        result = {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": [],
            "warnings": [],
            "checks_passed": 0,
            "checks_total": 9
        }
        
        # Check 1: PostgreSQL validation
        if not pg_valid:
            pg_status = self._postgresql_validator.validate()
            if not pg_status.get("success", False):
                result["status"] = ServiceStatus.ERROR
                result["errors"].append("PostgreSQL validation failed")
                result["errors"].extend(pg_status.get("errors", []))
                return result
        
        result["checks_passed"] += 1
        
        # Check 2: Module exists
        try:
            module = importlib.import_module(module_name)
            result["checks_passed"] += 1
        except ImportError as e:
            result["status"] = ServiceStatus.NOT_STARTED
            result["errors"].append(f"Module '{module_name}' not found: {e}")
            return result
        
        # Check 3: Class exists
        if not hasattr(module, class_name):
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Class '{class_name}' not found")
            return result
        
        cls = getattr(module, class_name)
        result["checks_passed"] += 1
        
        # Check 4: Required methods exist
        missing_methods = []
        for method in required_methods:
            if not hasattr(cls, method):
                missing_methods.append(method)
        
        if missing_methods:
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Missing methods: {missing_methods}")
            return result
        
        result["checks_passed"] += 1
        
        # Check 5: Instantiate service
        try:
            instance = cls()
            result["checks_passed"] += 1
        except Exception as e:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append(f"Instantiation failed: {e}")
            return result
        
        # Check 6: health_check passes
        if hasattr(instance, "health_check"):
            try:
                health = instance.health_check()
                if not health.get("healthy", False):
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Health check failed: {health.get('error', 'Unknown error')}")
                    return result
                result["checks_passed"] += 1
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Health check exception: {e}")
                return result
        else:
            result["warnings"].append("No health_check method")
        
        # Check 7: validation_query executes
        if hasattr(instance, "validation_query"):
            try:
                validation = instance.validation_query()
                if not validation.get("success", False):
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Validation query failed: {validation.get('error', 'Unknown error')}")
                    return result
                result["checks_passed"] += 1
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Validation query exception: {e}")
                return result
        else:
            result["warnings"].append("No validation_query method")
        
        # Check 8: Dependencies are ready
        dependency_status = self._check_dependencies(dependencies)
        if not dependency_status["all_ready"]:
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Dependencies not ready: {dependency_status['missing']}")
            return result
        
        result["checks_passed"] += 1
        
        # Check 9: get_service_metadata exists
        if hasattr(instance, "get_service_metadata"):
            try:
                metadata = instance.get_service_metadata()
                if not metadata.get("name"):
                    result["warnings"].append("Service metadata missing name")
                result["checks_passed"] += 1
            except Exception as e:
                result["warnings"].append(f"get_service_metadata failed: {e}")
        else:
            result["warnings"].append("No get_service_metadata method")
        
        # All checks passed!
        result["status"] = ServiceStatus.READY
        result["ready"] = True
        result["instance"] = instance
        
        return result
    
    def _check_dependencies(self, dependencies: List[str]) -> Dict[str, Any]:
        """Check if all dependencies are ready."""
        missing = []
        for dep in dependencies:
            dep_status = self.get_service_status(dep)
            if not dep_status.get("ready", False):
                missing.append(dep)
        
        return {
            "all_ready": len(missing) == 0,
            "missing": missing
        }
    
    def get_service_status(self, service_key: str) -> Dict[str, Any]:
        """Get status of a specific service."""
        # Validate if not in cache or cache is old
        if (service_key not in self._status_cache or 
            self._last_validation is None or 
            time.time() - self._last_validation > 60):
            
            # Check if PostgreSQL validation status has changed
            pg_status = self._postgresql_validator.validate()
            pg_valid = pg_status.get("success", False)
            
            # Validate service
            self._status_cache[service_key] = self._validate_service(
                service_key, 
                pg_valid=pg_valid
            )
            
            # Cache instance if ready
            if self._status_cache[service_key].get("ready", False):
                self._instance_cache[service_key] = self._status_cache[service_key].get("instance")
        
        return self._status_cache.get(service_key, {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": ["Service not validated"]
        })
    
    def is_service_ready(self, service_key: str) -> bool:
        """Check if a service is ready to execute."""
        status = self.get_service_status(service_key)
        return status.get("ready", False)
    
    def get_service_instance(self, service_key: str):
        """Get service instance."""
        if not self.is_service_ready(service_key):
            return None
        
        return self._instance_cache.get(service_key)
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
        """Get service metadata if available."""
        if service_key not in self._services:
            return {"error": "Service not registered"}
        
        status = self.get_service_status(service_key)
        
        return {
            "key": service_key,
            "description": self._services[service_key].get("description", ""),
            "status": status.get("status", ServiceStatus.NOT_STARTED),
            "ready": status.get("ready", False),
            "checks_passed": status.get("checks_passed", 0),
            "checks_total": status.get("checks_total", 9),
            "dependencies": self._services[service_key].get("dependencies", []),
            "errors": status.get("errors", []),
            "warnings": status.get("warnings", [])
        }
    
    def get_all_service_statuses(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all services."""
        results = {}
        for service_key in self._services:
            results[service_key] = self.get_service_status(service_key)
        return results
    
    def get_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report."""
        statuses = self.get_all_service_statuses()
        
        total = len(statuses)
        ready = sum(1 for s in statuses.values() if s.get("ready", False))
        in_dev = sum(1 for s in statuses.values() 
                    if s.get("status") == ServiceStatus.IN_DEVELOPMENT)
        not_started = sum(1 for s in statuses.values() 
                        if s.get("status") == ServiceStatus.NOT_STARTED)
        error = sum(1 for s in statuses.values() 
                   if s.get("status") == ServiceStatus.ERROR)
        
        pg_status = self._postgresql_validator.validate()
        
        return {
            "total_services": total,
            "ready": ready,
            "in_development": in_dev,
            "not_started": not_started,
            "error": error,
            "readiness_score": (ready / total * 100) if total > 0 else 0,
            "services": statuses,
            "postgresql": pg_status,
            "last_validation": self._last_validation
        }


# ==========================================================
# BLOCK 6: INTENT DETECTION ENGINE
# ==========================================================

class IntentDetectionEngine:
    """
    Intent Detection Engine.
    
    Detects intent from user messages.
    Extracts entities from user messages.
    """
    
    # DN Pattern - 8 to 12 digits
    DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
    
    # Dealer Patterns
    DEALER_PATTERN = re.compile(r'(?:dealer|show|display|get|view|tell me about|about)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    DEALER_CODE_PATTERN = re.compile(r'(?:dealer code|code)\s+([a-z0-9]+)', re.IGNORECASE)
    
    # Pending Patterns
    PENDING_DN_PATTERN = re.compile(r'(?:pending|open|show\s+pending|list\s+pending)\s*(?:dn|delivery|deliveries)?', re.IGNORECASE)
    PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue)', re.IGNORECASE)
    PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
    
    # City Pattern
    CITY_PATTERN = re.compile(r'(?:city|in)\s+([a-z\s]+)', re.IGNORECASE)
    
    # Warehouse Pattern
    WAREHOUSE_PATTERN = re.compile(r'(?:warehouse|wh)\s+([a-z0-9\s]+)', re.IGNORECASE)
    
    # Product Pattern
    PRODUCT_PATTERN = re.compile(r'(?:product|model|material)\s+([a-z0-9\s\-]+)', re.IGNORECASE)
    
    # Ranking Patterns
    RANKING_PATTERN = re.compile(r'(?:top|best|worst|bottom)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)', re.IGNORECASE)
    
    # Help Patterns
    HELP_PATTERN = re.compile(r'(?:help|menu|commands|what can you do|available commands|how to use)', re.IGNORECASE)
    
    # Greeting Patterns
    GREETING_PATTERN = re.compile(r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings|hola|hey there|hi there)', re.IGNORECASE)
    
    # Explanation Patterns
    EXPLANATION_PATTERN = re.compile(r'(?:what is|explain|definition|meaning|what does|how does)\s+(?:pod|pgi|dn|aging|kpi|delivery|warehouse|dealer)', re.IGNORECASE)
    
    # National KPI Patterns
    NATIONAL_KPI_PATTERN = re.compile(r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard)', re.IGNORECASE)
    
    def __init__(self):
        """Initialize intent detection engine."""
        self._context_memory = {}
        self._context_ttl = 300  # 5 minutes
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """
        Detect intent and extract entities from message.
        
        Returns:
            RoutingDecision with intent, service_key, method, entity
        """
        cleaned = message.strip()
        normalized = self._normalize(cleaned)
        
        # ==========================================================
        # PRIORITY 1: DN DETECTION
        # ==========================================================
        
        # Check if entire message is DN
        if self._is_dn_number(cleaned):
            dn_number = re.sub(r'\D', '', cleaned)
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                needs_groq=False,
                reason="DN number detected",
                original_message=cleaned,
                detected_dn=dn_number,
                detected_intent="dn_lookup"
            )
        
        # Check for DN in message
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            dn_number = dn_match.group(1)
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn_number,
                confidence=1.0,
                needs_groq=False,
                reason="DN number extracted",
                original_message=cleaned,
                detected_dn=dn_number,
                detected_intent="dn_lookup"
            )
        
        # ==========================================================
        # PRIORITY 2: PENDING DETECTION
        # ==========================================================
        
        if self.PENDING_DN_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                entity=None,
                confidence=0.95,
                needs_groq=False,
                reason="Pending DN query detected",
                original_message=cleaned,
                detected_intent="pending_dn"
            )
        
        if self.PENDING_PGI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                entity=None,
                confidence=0.95,
                needs_groq=False,
                reason="Pending PGI query detected",
                original_message=cleaned,
                detected_intent="pending_pgi"
            )
        
        if self.PENDING_POD_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                entity=None,
                confidence=0.95,
                needs_groq=False,
                reason="Pending POD query detected",
                original_message=cleaned,
                detected_intent="pending_pod"
            )
        
        # ==========================================================
        # PRIORITY 3: NATIONAL KPI DETECTION
        # ==========================================================
        
        if self.NATIONAL_KPI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                entity=None,
                confidence=0.90,
                needs_groq=False,
                reason="National KPI query detected",
                original_message=cleaned,
                detected_intent="national_kpi"
            )
        
        # ==========================================================
        # PRIORITY 4: DEALER DETECTION
        # ==========================================================
        
        dealer_result = self._detect_dealer(cleaned, normalized)
        if dealer_result:
            dealer_name = dealer_result
            
            # Check if profile request
            if "profile" in normalized or "info" in normalized or "details" in normalized:
                method = "get_dealer_profile"
                intent = "dealer_profile"
            else:
                method = "get_dealer_dashboard"
                intent = "dealer_dashboard"
            
            return RoutingDecision(
                intent=intent,
                service_key="dealer",
                method=method,
                entity=dealer_name,
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer detected: {dealer_name}",
                original_message=cleaned,
                detected_dealer=dealer_name,
                detected_intent=intent
            )
        
        # ==========================================================
        # PRIORITY 5: CITY DETECTION
        # ==========================================================
        
        city_result = self._detect_city(cleaned, normalized)
        if city_result:
            return RoutingDecision(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city_result,
                confidence=0.90,
                needs_groq=False,
                reason=f"City detected: {city_result}",
                original_message=cleaned,
                detected_city=city_result,
                detected_intent="city_dashboard"
            )
        
        # ==========================================================
        # PRIORITY 6: WAREHOUSE DETECTION
        # ==========================================================
        
        warehouse_result = self._detect_warehouse(cleaned, normalized)
        if warehouse_result:
            return RoutingDecision(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse_result,
                confidence=0.90,
                needs_groq=False,
                reason=f"Warehouse detected: {warehouse_result}",
                original_message=cleaned,
                detected_warehouse=warehouse_result,
                detected_intent="warehouse_dashboard"
            )
        
        # ==========================================================
        # PRIORITY 7: PRODUCT DETECTION
        # ==========================================================
        
        product_result = self._detect_product(cleaned, normalized)
        if product_result:
            return RoutingDecision(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_result,
                confidence=0.90,
                needs_groq=False,
                reason=f"Product detected: {product_result}",
                original_message=cleaned,
                detected_product=product_result,
                detected_intent="product_dashboard"
            )
        
        # ==========================================================
        # PRIORITY 8: RANKING DETECTION
        # ==========================================================
        
        ranking_result = self._detect_ranking(cleaned, normalized)
        if ranking_result:
            intent, service_key, method = ranking_result
            return RoutingDecision(
                intent=intent,
                service_key=service_key,
                method=method,
                entity=None,
                confidence=0.85,
                needs_groq=False,
                reason=f"Ranking query: {intent}",
                original_message=cleaned,
                detected_intent=intent
            )
        
        # ==========================================================
        # PRIORITY 9: HELP / GREETING / EXPLANATION
        # ==========================================================
        
        if self.HELP_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="help",
                service_key="groq",
                method="process_query",
                entity=None,
                confidence=0.95,
                needs_groq=True,
                reason="Help query",
                original_message=cleaned,
                detected_intent="help"
            )
        
        if self.GREETING_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                entity=None,
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original_message=cleaned,
                detected_intent="greeting"
            )
        
        if self.EXPLANATION_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="explanation",
                service_key="groq",
                method="process_query",
                entity=None,
                confidence=0.90,
                needs_groq=True,
                reason="Explanation query",
                original_message=cleaned,
                detected_intent="explanation"
            )
        
        # ==========================================================
        # UNKNOWN - Default to Groq
        # ==========================================================
        
        return RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            entity=None,
            confidence=0.30,
            needs_groq=True,
            reason="Unknown query - using Groq",
            original_message=cleaned,
            detected_intent="general_ai"
        )
    
    def _detect_dealer(self, original: str, normalized: str) -> Optional[str]:
        """Detect dealer from query."""
        # Pattern extraction
        dealer_match = self.DEALER_PATTERN.search(original)
        if dealer_match:
            return dealer_match.group(1).strip()
        
        # Code pattern
        code_match = self.DEALER_CODE_PATTERN.search(original)
        if code_match:
            return code_match.group(1).strip()
        
        return None
    
    def _detect_city(self, original: str, normalized: str) -> Optional[str]:
        """Detect city from query."""
        city_match = self.CITY_PATTERN.search(original)
        if city_match:
            return city_match.group(1).strip()
        return None
    
    def _detect_warehouse(self, original: str, normalized: str) -> Optional[str]:
        """Detect warehouse from query."""
        warehouse_match = self.WAREHOUSE_PATTERN.search(original)
        if warehouse_match:
            return warehouse_match.group(1).strip()
        return None
    
    def _detect_product(self, original: str, normalized: str) -> Optional[str]:
        """Detect product from query."""
        product_match = self.PRODUCT_PATTERN.search(original)
        if product_match:
            return product_match.group(1).strip()
        return None
    
    def _detect_ranking(self, original: str, normalized: str) -> Optional[Tuple[str, str, str]]:
        """Detect ranking intent."""
        if 'top dealer' in normalized or 'best dealer' in normalized:
            if 'revenue' in normalized or 'sales' in normalized:
                return ("top_dealers_revenue", "dealer", "get_top_dealers")
            if 'unit' in normalized or 'quantity' in normalized:
                return ("top_dealers_units", "dealer", "get_top_dealers")
            return ("top_dealers", "dealer", "get_top_dealers")
        if 'bottom dealer' in normalized or 'worst dealer' in normalized:
            return ("bottom_dealers", "dealer", "get_bottom_dealers")
        if 'top city' in normalized or 'best city' in normalized:
            return ("top_cities", "city", "get_top_cities")
        if 'top warehouse' in normalized or 'best warehouse' in normalized:
            return ("top_warehouses", "warehouse", "get_top_warehouses")
        if 'top product' in normalized or 'best product' in normalized:
            return ("top_products", "product", "get_top_products")
        return None
    
    def _is_dn_number(self, text: str) -> bool:
        """Check if text is a valid DN number."""
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _normalize(self, text: str) -> str:
        """Normalize text for processing."""
        return text.lower().strip() if text else ""


# ==========================================================
# BLOCK 7: WHATSAPP PROVIDER SERVICE - MASTER ROUTER
# ==========================================================

class WhatsAppProviderService:
    """
    Master WhatsApp Provider Service.
    
    This is the SINGLE ENTRY POINT for all WhatsApp requests.
    This file orchestrates all services and controls responses.
    """
    
    def __init__(self):
        """Initialize WhatsAppProviderService."""
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v5.0 - PRODUCTION GRADE")
            logger.info("=" * 70)
            
            # Initialize service registry (auto-discovery)
            self.registry = ServiceRegistry()
            
            # Initialize intent detection engine
            self.intent_engine = IntentDetectionEngine()
            
            # Initialize Groq service (language layer only)
            self._groq_service = None
            try:
                from app.services.groq_service import get_groq_service
                self._groq_service = get_groq_service()
                logger.info("✅ GroqService initialized")
            except ImportError:
                logger.warning("⚠️ GroqService not available")
            except Exception as e:
                logger.error(f"❌ GroqService initialization failed: {e}")
            
            # Validate all services
            self.registry.validate_all_services()
            
            init_duration = (time.time() - start_time) * 1000
            
            # Log service registry status
            health = self.registry.get_health_report()
            
            logger.info("")
            logger.info("   SERVICE REGISTRY STATUS (Auto-Discovered):")
            logger.info(f"   ✅ Ready: {health['ready']}")
            logger.info(f"   🔧 In Development: {health['in_development']}")
            logger.info(f"   ⏳ Not Started: {health['not_started']}")
            logger.info(f"   🚨 Error: {health['error']}")
            logger.info(f"   📊 Readiness Score: {health['readiness_score']:.1f}%")
            logger.info("")
            
            # Log PostgreSQL status
            pg_status = health.get('postgresql', {})
            pg_icon = "✅" if pg_status.get("success") else "❌"
            logger.info(f"   PostgreSQL: {pg_icon} {pg_status.get('connected', False)}")
            logger.info("")
            
            # Log each service
            for service_key, status in health['services'].items():
                ready = status.get("ready", False)
                status_text = status.get("status", "UNKNOWN")
                checks = status.get("checks_passed", 0)
                total_checks = status.get("checks_total", 9)
                deps = self.registry.SERVICES.get(service_key, {}).get("dependencies", [])
                dep_text = f" (deps: {', '.join(deps)})" if deps else ""
                
                icon = "✅" if ready else "🔧"
                logger.info(f"   {icon} {service_key.title():15} → {status_text} ({checks}/{total_checks} checks){dep_text}")
            
            logger.info("")
            logger.info("   ROUTING RULES:")
            logger.info("   ✅ Only READY services execute")
            logger.info("   ✅ Services auto-activate when ready")
            logger.info("   ✅ Dependencies validated before activation")
            logger.info("   ✅ Groq = Language layer only")
            logger.info("   ✅ PostgreSQL = Only data source")
            logger.info("   ❌ No ai_query_service.py")
            logger.info("   ❌ No mock data")
            logger.info("   ❌ No fake analytics")
            logger.info("   ❌ No execution of incomplete modules")
            logger.info("   ❌ Groq never becomes data source")
            logger.info("")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize WhatsAppProviderService: {str(e)}")
            raise RuntimeError(f"WhatsAppProviderService initialization failed: {str(e)}") from e
    
    # ==========================================================
    # BLOCK 8: MAIN ROUTING METHOD
    # ==========================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a WhatsApp query - MAIN ENTRY POINT.
        
        This is the single entry point for all WhatsApp messages.
        
        Args:
            message: WhatsApp message text
            sender_id: WhatsApp sender ID
            
        Returns:
            Dict with response for WhatsApp
        """
        logger.info(f"📩 Processing WhatsApp query: '{message[:100]}' from {sender_id}")
        
        try:
            # ==========================================================
            # STEP 1: Detect Intent and Extract Entities
            # ==========================================================
            
            routing_decision = self.intent_engine.detect_intent(message)
            
            logger.info(f"🎯 Routing Decision: {routing_decision.to_dict()}")
            
            # ==========================================================
            # STEP 2: Check if this needs Groq (language layer only)
            # ==========================================================
            
            if routing_decision.needs_groq:
                return await self._handle_groq_query(message, routing_decision)
            
            # ==========================================================
            # STEP 3: Check Service Readiness
            # ==========================================================
            
            service_key = routing_decision.service_key
            
            if not self.registry.is_service_ready(service_key):
                return self._format_module_unavailable(
                    message,
                    service_key,
                    self.registry.get_service_info(service_key)
                )
            
            # ==========================================================
            # STEP 4: Execute Service
            # ==========================================================
            
            result = await self._execute_routing_decision(routing_decision)
            
            if result.get("success", False):
                return self._format_response(message, result.get("data"), error=False)
            else:
                return self._format_response(
                    message,
                    result.get("error", "An error occurred while processing your request."),
                    error=True
                )
            
        except Exception as e:
            logger.exception(f"❌ Failed to process WhatsApp query: {e}")
            return self._format_response(
                message,
                f"⚠️ An unexpected error occurred.\n\nPlease try again later.",
                error=True
            )
    
    # ==========================================================
    # BLOCK 9: GROQ HANDLING (Language Layer Only)
    # ==========================================================
    
    async def _handle_groq_query(self, message: str, routing_decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq query (language layer only)."""
        
        # Greeting - use Groq or fallback
        if routing_decision.intent == "greeting":
            return await self._handle_greeting(message)
        
        # Help - use Groq or fallback
        if routing_decision.intent == "help":
            return await self._handle_help(message)
        
        # Explanation - use Groq or fallback
        if routing_decision.intent == "explanation":
            return await self._handle_explanation(message)
        
        # General AI - use Groq
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq processing failed: {e}")
        
        # Fallback
        return self._format_response(
            message,
            "I'm sorry, I couldn't process your request. Please try again later.",
            error=True
        )
    
    async def _handle_greeting(self, message: str) -> Dict[str, Any]:
        """Handle greeting."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq greeting failed: {e}")
        
        fallback = """Welcome to the Logistics WhatsApp AI Agent!

I can help you with:

📦 DN Tracking - Get delivery status and details
🏪 Dealer Analytics - View dealer performance and KPIs
🏭 Warehouse Analytics - Monitor warehouse operations
🏙️ City Analytics - Analyze city-level performance
📊 National KPIs - View Pakistan-wide metrics

To get started, try:
- Send a DN number (8-12 digits)
- Ask about a dealer name
- Ask about a warehouse
- Ask about a city
- Ask for help

All data comes directly from PostgreSQL."""
        
        return self._format_response(message, fallback, error=False)
    
    async def _handle_help(self, message: str) -> Dict[str, Any]:
        """Handle help request."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq help failed: {e}")
        
        fallback = """📋 Available Commands:

DN Queries:
- Send a DN number (e.g., 6243643667)
- "Pending DN"
- "Pending PGI"
- "Pending POD"

Dealer Queries:
- "Dealer [name]"
- "[Dealer name] profile"
- "Top dealers"
- "Bottom dealers"

Warehouse Queries:
- "Warehouse [name]"

City Queries:
- "City [name]"

Product Queries:
- "Product [name]"

National KPI:
- "National KPI dashboard"
- "Pakistan KPI"

Help:
- "Help"
- "What is POD?"
- "What is PGI?"
- "Explain delivery aging"

All data comes directly from PostgreSQL."""
        
        return self._format_response(message, fallback, error=False)
    
    async def _handle_explanation(self, message: str) -> Dict[str, Any]:
        """Handle explanation request."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq explanation failed: {e}")
        
        # Simple explanation fallback
        msg_lower = message.lower()
        if "pod" in msg_lower:
            explanation = """📄 POD (Proof of Delivery)

POD is a document that confirms the delivery of goods.

Key Points:
- POD confirms delivery completion
- POD includes delivery date/time
- POD includes receiver signature
- POD is required for invoicing

In this system:
- POD Date = When delivery was confirmed
- POD Status = Completed or Pending
- POD Aging = Time since POD should have been received"""
        elif "pgi" in msg_lower:
            explanation = """📦 PGI (Post Goods Issue)

PGI is the process of releasing goods from warehouse.

Key Points:
- PGI confirms goods have left warehouse
- PGI reduces inventory levels
- PGI triggers delivery process
- PGI date is when goods were dispatched

In this system:
- PGI Date = When goods were dispatched
- PGI Status = Completed or Pending
- PGI Aging = Time since PGI should have been completed"""
        elif "dn" in msg_lower:
            explanation = """📋 DN (Delivery Note)

DN is a document that accompanies goods during delivery.

Key Points:
- DN identifies the delivery
- DN includes customer information
- DN lists products and quantities
- DN tracks delivery status

In this system:
- DN Number = Unique identifier
- DN Status = Delivered, In Transit, or Pending
- DN Amount = Total value of delivery"""
        elif "aging" in msg_lower:
            explanation = """⏳ Delivery Aging

Delivery Aging measures how long a delivery has been in progress.

Types of Aging:
1. Delivery Aging: Time from DN creation to current date
2. PGI Aging: Time from PGI to current date
3. POD Aging: Time from POD to current date

Aging Categories:
- 0-7 days: Normal
- 8-14 days: Moderate
- 15-30 days: Delayed
- 30+ days: Critical"""
        else:
            explanation = """📊 Logistics Terms

Common logistics terms:

DN (Delivery Note): Document that accompanies goods delivery

PGI (Post Goods Issue): Process of releasing goods from warehouse

POD (Proof of Delivery): Document confirming delivery completion

Aging: Time elapsed since an event (creation, dispatch, delivery)

KPI (Key Performance Indicator): Measurable value showing performance

For more details, ask about specific terms like:
- "What is POD?"
- "What is PGI?"
- "What is DN?"
- "Explain delivery aging"
- "Explain KPI" """
        
        return self._format_response(message, explanation, error=False)
    
    # ==========================================================
    # BLOCK 10: SERVICE EXECUTION
    # ==========================================================
    
    async def _execute_routing_decision(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Execute a routing decision."""
        service_key = decision.service_key
        
        # Get service instance
        service_instance = self.registry.get_service_instance(service_key)
        
        if not service_instance:
            return {
                "success": False,
                "error": f"Service '{service_key}' is not available"
            }
        
        # Execute method
        try:
            method = getattr(service_instance, decision.method, None)
            if not method:
                return {
                    "success": False,
                    "error": f"Method '{decision.method}' not found in service '{service_key}'"
                }
            
            # Prepare arguments
            if decision.entity:
                if decision.entity2:
                    result = method(decision.entity, decision.entity2)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            # Handle async methods
            if inspect.iscoroutine(result):
                result = await result
            
            return result if isinstance(result, dict) else {"success": True, "data": result}
            
        except Exception as e:
            logger.exception(f"❌ Service execution failed: {e}")
            return {
                "success": False,
                "error": f"Service execution failed: {str(e)}"
            }
    
    # ==========================================================
    # BLOCK 11: RESPONSE FORMATTING
    # ==========================================================
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        """Format response for WhatsApp."""
        return {
            "success": not error,
            "message": original_message,
            "response": data,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
    
    def _format_module_unavailable(self, original_message: str, service_key: str, info: Dict[str, Any]) -> Dict[str, Any]:
        """Format module unavailable response."""
        status_text = info.get("status", "UNKNOWN")
        errors = info.get("errors", [])
        checks_passed = info.get("checks_passed", 0)
        checks_total = info.get("checks_total", 9)
        dependencies = info.get("dependencies", [])
        
        message = f"""⚠️ Module Currently Under Development

Module:
{service_key.title()} Service

Status:
{status_text}

Readiness:
{checks_passed}/{checks_total} checks passed

"""
        
        if dependencies:
            message += f"\nDependencies:\n{chr(10).join(['- ' + d.title() for d in dependencies])}"
        
        if errors:
            message += f"\n\nMissing:\n{chr(10).join(['- ' + e for e in errors[:3]])}"
        
        message += """

Please try again after development is completed."""
        
        return self._format_response(original_message, message, error=True)
    
    # ==========================================================
    # BLOCK 12: DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_service_registry_status(self) -> Dict[str, Any]:
        """Get service registry status."""
        return self.registry.get_health_report()
    
    def validate_all_services(self) -> Dict[str, Any]:
        """Validate all services."""
        return self.registry.validate_all_services(force=True)
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health report."""
        service_health = self.registry.get_health_report()
        
        return {
            "services": service_health,
            "system_status": "healthy" if service_health.get("readiness_score", 0) > 50 else "degraded",
            "timestamp": datetime.now().isoformat(),
            "version": "5.0"
        }
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
        """Get detailed service information."""
        return self.registry.get_service_info(service_key)
    
    def refresh_service_status(self, service_key: str = None) -> Dict[str, Any]:
        """Refresh service status."""
        if service_key:
            self.registry._status_cache.pop(service_key, None)
            self.registry._instance_cache.pop(service_key, None)
            return self.registry.get_service_status(service_key)
        else:
            return self.registry.validate_all_services(force=True)


# ==========================================================
# BLOCK 13: THREAD-SAFE SINGLETON
# ==========================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()


def get_whatsapp_provider_service() -> WhatsAppProviderService:
    """Thread-safe singleton getter."""
    global _whatsapp_provider_service
    
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("✅ WhatsAppProviderService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ WhatsAppProviderService initialization failed: {e}")
                    raise
    
    return _whatsapp_provider_service


# ==========================================================
# BLOCK 14: EXPORTS
# ==========================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'ServiceStatus',
    'RoutingDecision',
    'IntentDetectionEngine',
    'PostgreSQLValidator'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.debug("=" * 70)
logger.debug("AI Provider Service v5.0 - PRODUCTION GRADE")
logger.debug("=" * 70)
logger.debug("")
logger.debug("   ROUTING ARCHITECTURE:")
logger.debug("   WhatsApp User → webhook.py → ai_provider_service.py")
logger.debug("   ↓")
logger.debug("   Intent Detection Engine (Built-in)")
logger.debug("   ↓")
logger.debug("   Entity Extraction Engine (Built-in)")
logger.debug("   ↓")
logger.debug("   Auto-Discover Service Registry")
logger.debug("   ↓")
logger.debug("   True Service Readiness Validation")
logger.debug("   ↓")
logger.debug("   Dependency Validation")
logger.debug("   ↓")
logger.debug("   PostgreSQL Validation")
logger.debug("   ↓")
logger.debug("   Route To Correct Service")
logger.debug("   ↓")
logger.debug("   Return Result")
logger.debug("")
logger.debug("   ROUTER RULES:")
logger.debug("   ✅ Only READY services execute")
logger.debug("   ✅ Services auto-activate when ready")
logger.debug("   ✅ Dependencies validated before activation")
logger.debug("   ✅ Groq = Language layer only")
logger.debug("   ✅ PostgreSQL = Only data source")
logger.debug("   ❌ No ai_query_service.py")
logger.debug("   ❌ No mock data")
logger.debug("   ❌ No fake analytics")
logger.debug("   ❌ No execution of incomplete modules")
logger.debug("   ❌ Groq never becomes data source")
logger.debug("")
logger.debug("   SERVICE READINESS CHECKS:")
logger.debug("   1️⃣ PostgreSQL validated")
logger.debug("   2️⃣ Module exists")
logger.debug("   3️⃣ Class exists")
logger.debug("   4️⃣ Required methods exist")
logger.debug("   5️⃣ Service instantiates")
logger.debug("   6️⃣ health_check passes")
logger.debug("   7️⃣ validation_query executes")
logger.debug("   8️⃣ Dependencies ready")
logger.debug("   9️⃣ Service metadata available")
logger.debug("")
logger.debug("   STATUS: ✅ PRODUCTION GRADE")
logger.debug("=" * 70)
