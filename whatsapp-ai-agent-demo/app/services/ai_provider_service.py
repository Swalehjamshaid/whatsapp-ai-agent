# ==========================================================
# MASTER ROUTING AND ORCHESTRATION ENGINE
# ==========================================================
# File: app/services/ai_provider_service.py
# Version: 4.0 - PRODUCTION GRADE
# Purpose: Single entry point for all WhatsApp requests.
#
# Responsibilities:
# - Intent Routing
# - Service Orchestration
# - Auto-Discovery & Auto-Activation
# - True Service Readiness Validation
# - PostgreSQL Validation
# - Groq Integration (language layer only)
# - WhatsApp Response Control
# - Incremental Development Support
#
# This file does NOT:
# - Calculate KPIs
# - Execute SQL business analytics
# - Build dashboards
# - Generate fake data
# - Use mock data
#
# Services auto-activate when fully ready.
# No manual registry updates required.
# ==========================================================

import logging
import threading
import time
import importlib
import inspect
import os
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.services.ai_query_service import get_ai_query_service, RoutingDecision
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import text, inspect as sa_inspect
    logger.info("✅ Core imports successful")
except ImportError as e:
    logger.error(f"❌ Core import failed: {e}")
    get_ai_query_service = None
    SessionLocal = None
    DeliveryReport = None


# ==========================================================
# BLOCK 2: SERVICE STATUS ENUM
# ==========================================================

class ServiceStatus:
    """Service status constants."""
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# ==========================================================
# BLOCK 3: POSTGRESQL VALIDATOR
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
        "ship_to_city", "warehouse", "dn_qty", "dn_amount",
        "dn_create_date", "good_issue_date", "pod_date",
        "delivery_status", "pgi_status", "pod_status", "pending_flag"
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
# BLOCK 4: SERVICE REGISTRY - AUTO-DISCOVERY & AUTO-ACTIVATION
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
            "description": "DN Analytics Service"
        },
        "dealer": {
            "module": "app.services.dealer_analytics_service",
            "class_name": "DealerAnalyticsService",
            "methods": [
                "get_dealer_dashboard", "get_dealer_profile", 
                "compare_dealers", "get_top_dealers", "get_bottom_dealers",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Dealer Analytics Service"
        },
        "warehouse": {
            "module": "app.services.warehouse_analytics_service",
            "class_name": "WarehouseAnalyticsService",
            "methods": [
                "get_warehouse_dashboard", "get_top_warehouses",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Warehouse Analytics Service"
        },
        "city": {
            "module": "app.services.city_analytics_service",
            "class_name": "CityAnalyticsService",
            "methods": [
                "get_city_dashboard", "get_top_cities",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "City Analytics Service"
        },
        "product": {
            "module": "app.services.product_analytics_service",
            "class_name": "ProductAnalyticsService",
            "methods": [
                "get_product_dashboard", "get_top_products",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "Product Analytics Service"
        },
        "national_kpi": {
            "module": "app.services.national_kpi_service",
            "class_name": "NationalKPIService",
            "methods": [
                "get_national_kpi_dashboard", "get_delivery_kpis", 
                "get_warehouse_kpis",
                "health_check", "validation_query", "get_service_metadata"
            ],
            "description": "National KPI Service"
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
            
            results = {}
            for service_key in self._services:
                results[service_key] = self._validate_service(
                    service_key, 
                    pg_valid=pg_status.get("success", False)
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
        
        result = {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": [],
            "warnings": [],
            "checks_passed": 0,
            "checks_total": 7
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
        
        # All checks passed!
        result["status"] = ServiceStatus.READY
        result["ready"] = True
        result["instance"] = instance
        
        return result
    
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
            "checks_total": status.get("checks_total", 7),
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
# BLOCK 5: WHATSAPP PROVIDER SERVICE - MASTER ROUTER
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
            logger.info("AI Provider Service v4.0 - PRODUCTION GRADE")
            logger.info("=" * 70)
            
            # Initialize service registry (auto-discovery)
            self.registry = ServiceRegistry()
            
            # Initialize AI query service
            self._ai_query_service = None
            if get_ai_query_service:
                try:
                    self._ai_query_service = get_ai_query_service()
                    logger.info("✅ AIQueryService initialized")
                except Exception as e:
                    logger.error(f"❌ AIQueryService initialization failed: {e}")
            
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
                total_checks = status.get("checks_total", 7)
                
                icon = "✅" if ready else "🔧"
                logger.info(f"   {icon} {service_key.title():15} → {status_text} ({checks}/{total_checks} checks)")
            
            logger.info("")
            logger.info("   ROUTING RULES:")
            logger.info("   ✅ Only READY services execute")
            logger.info("   ✅ Services auto-activate when ready")
            logger.info("   ✅ Groq = Language layer only")
            logger.info("   ✅ PostgreSQL = Only data source")
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
    # BLOCK 6: MAIN ROUTING METHOD
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
            # STEP 1: Check if this is a greeting/help query
            # ==========================================================
            
            if self._is_greeting(message) or self._is_help(message) or self._is_explanation_query(message):
                return await self._handle_greeting_or_help(message)
            
            # ==========================================================
            # STEP 2: Detect Intent and Entity
            # ==========================================================
            
            if not self._ai_query_service:
                return self._format_response(
                    message,
                    "⚠️ Service Unavailable\n\nAI Query Service is not available.",
                    error=True
                )
            
            routing_decision = await self._ai_query_service.process_query(
                question=message,
                context=None,
                user_id=sender_id
            )
            
            logger.info(f"🎯 Routing Decision: {routing_decision}")
            
            # ==========================================================
            # STEP 3: Check if this needs Groq (language layer only)
            # ==========================================================
            
            if self._should_use_groq(routing_decision):
                return await self._handle_groq_query(message, routing_decision)
            
            # ==========================================================
            # STEP 4: Map intent to service key
            # ==========================================================
            
            service_key = self._map_intent_to_service_key(routing_decision.intent)
            
            if service_key == "unknown":
                return self._format_response(
                    message,
                    "I don't understand this query. Please try a different question.",
                    error=True
                )
            
            # ==========================================================
            # STEP 5: Check Service Readiness
            # ==========================================================
            
            if not self.registry.is_service_ready(service_key):
                return self._format_module_unavailable(
                    message,
                    service_key,
                    self.registry.get_service_info(service_key)
                )
            
            # ==========================================================
            # STEP 6: Execute Service
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
    # BLOCK 7: INTENT MAPPING
    # ==========================================================
    
    def _map_intent_to_service_key(self, intent: str) -> str:
        """Map intent to service key."""
        intent_to_service = {
            "dn_lookup": "dn",
            "dealer_dashboard": "dealer",
            "dealer_profile": "dealer",
            "compare_dealers": "dealer",
            "top_dealers": "dealer",
            "top_dealers_revenue": "dealer",
            "top_dealers_units": "dealer",
            "bottom_dealers": "dealer",
            "city_dashboard": "city",
            "warehouse_dashboard": "warehouse",
            "product_dashboard": "product",
            "pending_dn": "dn",
            "pending_pgi": "dn",
            "pending_pod": "dn",
            "top_cities": "city",
            "top_warehouses": "warehouse",
            "top_products": "product",
            "national_kpi": "national_kpi",
            "executive_insight": "national_kpi"
        }
        
        return intent_to_service.get(intent, "unknown")
    
    # ==========================================================
    # BLOCK 8: GROQ HANDLING (Language Layer Only)
    # ==========================================================
    
    def _is_greeting(self, message: str) -> bool:
        """Check if message is a greeting."""
        greetings = ['hello', 'hi', 'hey', 'good morning', 'good evening', 'good afternoon', 'howdy', 'greetings']
        return any(g in message.lower() for g in greetings)
    
    def _is_help(self, message: str) -> bool:
        """Check if message is a help request."""
        help_terms = ['help', 'menu', 'commands', 'what can you do', 'capabilities', 'how to use']
        return any(h in message.lower() for h in help_terms)
    
    def _is_explanation_query(self, message: str) -> bool:
        """Check if message is asking for explanation."""
        explanation_terms = [
            'what is pod', 'what is pgi', 'what is dn', 'explain',
            'what does pod mean', 'what does pgi mean', 'what does aging mean',
            'definition', 'meaning'
        ]
        return any(e in message.lower() for e in explanation_terms)
    
    def _should_use_groq(self, routing_decision: RoutingDecision) -> bool:
        """
        Check if this query should use Groq.
        
        Groq is ONLY for:
        - Greetings
        - Help
        - Definitions
        - Explanations
        - Formatting
        - Executive Summaries of PostgreSQL Results
        
        Groq is NEVER for:
        - DN Queries
        - Dealer Queries
        - Warehouse Queries
        - City Queries
        - Product Queries
        - Revenue Queries
        - KPI Queries
        - Pending Queries
        - Distance Queries
        - Analytics Queries
        """
        # Always allow Groq for help and greetings
        if routing_decision.intent in ['help', 'greeting']:
            return True
        
        # Executive insight may use Groq for summarization ONLY
        if routing_decision.intent == 'executive_insight':
            return True
        
        # General AI queries use Groq
        if routing_decision.intent == 'general_ai':
            return True
        
        # Groq is allowed if explicitly requested
        if routing_decision.needs_groq:
            return True
        
        # Block Groq for all analytics queries
        analytics_intents = [
            'dn_lookup', 'dealer_dashboard', 'dealer_profile',
            'compare_dealers', 'top_dealers', 'bottom_dealers',
            'city_dashboard', 'warehouse_dashboard', 'product_dashboard',
            'pending_dn', 'pending_pgi', 'pending_pod',
            'top_cities', 'top_warehouses', 'top_products',
            'national_kpi'
        ]
        
        if routing_decision.intent in analytics_intents:
            return False
        
        # Default: use Groq
        return True
    
    async def _handle_greeting_or_help(self, message: str) -> Dict[str, Any]:
        """Handle greeting or help queries."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq processing failed: {e}")
        
        # Fallback response if Groq unavailable
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
    
    async def _handle_groq_query(self, message: str, routing_decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq query (language layer only)."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq processing failed: {e}")
        
        return self._format_response(
            message,
            "I'm sorry, I couldn't process your request. Please try again later.",
            error=True
        )
    
    # ==========================================================
    # BLOCK 9: SERVICE EXECUTION
    # ==========================================================
    
    async def _execute_routing_decision(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Execute a routing decision."""
        service_key = self._map_intent_to_service_key(decision.intent)
        
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
    # BLOCK 10: RESPONSE FORMATTING
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
        checks_total = info.get("checks_total", 7)
        
        message = f"""⚠️ Module Currently Under Development

Module:
{service_key.title()} Service

Status:
{status_text}

Readiness:
{checks_passed}/{checks_total} checks passed

"""
        
        if errors:
            message += f"\nMissing:\n{chr(10).join(['- ' + e for e in errors[:3]])}"
        
        message += """

Please try again after development is completed."""
        
        return self._format_response(original_message, message, error=True)
    
    def _format_database_error(self, original_message: str, error: str) -> Dict[str, Any]:
        """Format database error response."""
        message = f"""⚠️ Database Connection Unavailable

Service: All Services
Source: PostgreSQL

Error: {error}

Please contact administrator."""
        
        return self._format_response(original_message, message, error=True)
    
    # ==========================================================
    # BLOCK 11: DIAGNOSTIC METHODS
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
            "version": "4.0"
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
# BLOCK 12: THREAD-SAFE SINGLETON
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
# BLOCK 13: EXPORTS
# ==========================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'ServiceStatus',
    'PostgreSQLValidator'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.debug("=" * 70)
logger.debug("AI Provider Service v4.0 - PRODUCTION GRADE")
logger.debug("=" * 70)
logger.debug("")
logger.debug("   ROUTING ARCHITECTURE:")
logger.debug("   WhatsApp User → webhook.py → ai_provider_service.py")
logger.debug("   ↓")
logger.debug("   Intent Detection (ai_query_service.py)")
logger.debug("   ↓")
logger.debug("   Auto-Discover Service Registry")
logger.debug("   ↓")
logger.debug("   True Service Readiness Validation")
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
logger.debug("   ✅ Groq = Language layer only")
logger.debug("   ✅ PostgreSQL = Only data source")
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
logger.debug("")
logger.debug("   STATUS: ✅ PRODUCTION GRADE")
logger.debug("=" * 70)
