# ==========================================================
# MASTER ROUTING AND ORCHESTRATION ENGINE
# ==========================================================
# File: app/services/ai_provider_service.py
# Version: 5.1 - WITH WHATSAPP FORMATTING FIX
# Purpose: Single entry point for all WhatsApp requests.
# NO DEPENDENCY ON ai_query_service.py
# ALL intent detection is built-in.
# ==========================================================

import logging
import os
import threading
import time
import importlib
import inspect
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import text, func, inspect as sa_inspect
    logger.info("âœ… Core imports successful")
except ImportError as e:
    logger.error(f"âŒ Core import failed: {e}")
    SessionLocal = None
    DeliveryReport = None


# ==========================================================
# BLOCK 2: ROUTING DECISION CLASS
# ==========================================================

@dataclass
class RoutingDecision:
    """Internal routing decision - NO external dependency."""
    intent: str
    service_key: str
    method: str
    entity: Optional[str] = None
    entity2: Optional[str] = None
    confidence: float = 0.0
    needs_groq: bool = False
    reason: str = ""
    original_message: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "service_key": self.service_key,
            "method": self.method,
            "entity": self.entity,
            "entity2": self.entity2,
            "confidence": self.confidence,
            "needs_groq": self.needs_groq,
            "reason": self.reason,
            "original_message": self.original_message
        }


# ==========================================================
# BLOCK 3: INTENT DETECTION ENGINE (BUILT-IN)
# ==========================================================

class IntentDetectionEngine:
    """Built-in Intent Detection - NO ai_query_service.py dependency."""
    
    DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
    DEALER_PATTERN = re.compile(r'(?:dealer(?:\s+(?:dashboard|profile|performance|revenue|pending|distance))?|show|display|get|view|tell me about|about)\s+([a-z0-9\s&\-\.]+)', re.IGNORECASE)
    PENDING_DN_PATTERN = re.compile(r'^(?:(?:show|list)\s+)?(?:pending|open)\s+(?:dn|dns|delivery|deliveries)$|^(?:pending|open)$', re.IGNORECASE)
    PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue)', re.IGNORECASE)
    PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
    CITY_PATTERN = re.compile(r'^(?:city|in)\s+([a-z\s]+)$|^([a-z\s]+)\s+city$', re.IGNORECASE)
    WAREHOUSE_PATTERN = re.compile(r'^(?:warehouse|wh)\s+([a-z0-9\s]+)$|^([a-z0-9\s]+)\s+warehouse$', re.IGNORECASE)
    PRODUCT_PATTERN = re.compile(r'(?:product|model|material)\s+([a-z0-9\s\-]+)', re.IGNORECASE)
    HELP_PATTERN = re.compile(r'(?:help|menu|commands|what can you do|available commands|how to use)', re.IGNORECASE)
    GREETING_PATTERN = re.compile(r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings)', re.IGNORECASE)
    NATIONAL_KPI_PATTERN = re.compile(r'(?:national\s+kpi|country\s+kpi|overall\s+kpi|national\s+dashboard)', re.IGNORECASE)
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """Detect intent from message - NO external dependencies."""
        cleaned = message.strip()
        normalized = cleaned.lower()
        
        # 1. DN Detection (Full match)
        if self._is_dn_number(cleaned):
            dn = re.sub(r'\D', '', cleaned)
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn,
                confidence=1.0,
                needs_groq=False,
                reason="DN detected",
                original_message=cleaned
            )
        
        # 2. DN Detection (Pattern match)
        dn_match = self.DN_PATTERN.search(cleaned)
        if dn_match:
            dn = dn_match.group(1)
            return RoutingDecision(
                intent="dn_lookup",
                service_key="dn",
                method="get_dn_dashboard",
                entity=dn,
                confidence=1.0,
                needs_groq=False,
                reason="DN extracted",
                original_message=cleaned
            )
        
        # 3. Pending PGI/POD must be checked before the general pending route.
        if self.PENDING_PGI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pgi", service_key="dn", method="get_pending_pgi",
                confidence=0.98, needs_groq=False, reason="Pending PGI", original_message=cleaned
            )

        if self.PENDING_POD_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pod", service_key="dn", method="get_pending_pod",
                confidence=0.98, needs_groq=False, reason="Pending POD", original_message=cleaned
            )

        # 4. Pending DN
        if self.PENDING_DN_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.95,
                needs_groq=False,
                reason="Pending DN",
                original_message=cleaned
            )
        
        # 6. Dealer Detection
        dealer_match = self.DEALER_PATTERN.search(cleaned)
        if dealer_match:
            dealer = dealer_match.group(1).strip()
            method = "get_dealer_profile" if "profile" in normalized else "get_dealer_dashboard"
            return RoutingDecision(
                intent="dealer_dashboard",
                service_key="dealer",
                method=method,
                entity=dealer,
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer: {dealer}",
                original_message=cleaned
            )
        
        # 7. City Detection
        city_match = self.CITY_PATTERN.search(cleaned)
        if city_match:
            city = next((group for group in city_match.groups() if group), "").strip()
            return RoutingDecision(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city,
                confidence=0.90,
                needs_groq=False,
                reason="City detected",
                original_message=cleaned
            )
        
        # 8. Warehouse Detection
        warehouse_match = self.WAREHOUSE_PATTERN.search(cleaned)
        if warehouse_match:
            warehouse = next((group for group in warehouse_match.groups() if group), "").strip()
            return RoutingDecision(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse,
                confidence=0.90,
                needs_groq=False,
                reason="Warehouse detected",
                original_message=cleaned
            )
        
        # 9. Product Detection
        product_match = self.PRODUCT_PATTERN.search(cleaned)
        if product_match:
            return RoutingDecision(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_match.group(1).strip(),
                confidence=0.90,
                needs_groq=False,
                reason="Product detected",
                original_message=cleaned
            )
        
        # 10. National KPI
        if self.NATIONAL_KPI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="national_kpi", service_key="national_kpi",
                method="get_national_kpi_dashboard", confidence=0.95,
                needs_groq=False, reason="National KPI detected", original_message=cleaned
            )

        # 11. Help
        if self.HELP_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="help",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Help request",
                original_message=cleaned
            )
        
        # 11. Greeting
        if self.GREETING_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="greeting",
                service_key="groq",
                method="process_query",
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original_message=cleaned
            )
        
        # 12. Default - Groq
        return RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="Unknown - Groq",
            original_message=cleaned
        )
    
    def _is_dn_number(self, text: str) -> bool:
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12


# ==========================================================
# BLOCK 4: SERVICE STATUS ENUM
# ==========================================================

class ServiceStatus:
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# ==========================================================
# BLOCK 5: POSTGRESQL VALIDATOR
# ==========================================================

class PostgreSQLValidator:
    REQUIRED_COLUMNS = [
        "dn_no", "customer_name", "dealer_code", "customer_code",
        "ship_to_city", "warehouse", "dn_qty", "dn_amount",
        "dn_create_date", "good_issue_date", "pod_date",
        "delivery_status", "pgi_status", "pod_status", "pending_flag"
    ]
    
    def validate(self) -> Dict[str, Any]:
        result = {
            "success": False,
            "connected": False,
            "table_exists": False,
            "columns_valid": False,
            "errors": [],
            "warnings": [],
            "record_count": 0,
            "latest_upload_batch": None,
            "latest_imported_date": None,
            "duplicate_dn_count": 0,
            "missing_indexes": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            
            try:
                session.execute(text("SELECT 1"))
                result["connected"] = True
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                session.close()
                return result
            
            inspector = sa_inspect(session.bind)
            tables = inspector.get_table_names()
            
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                session.close()
                return result
            
            result["table_exists"] = True
            
            columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
            missing = [col for col in self.REQUIRED_COLUMNS if col not in columns]
            
            if missing:
                result["errors"].append(f"Missing columns: {missing}")
                result["columns_valid"] = False
            else:
                result["columns_valid"] = True

            try:
                result["record_count"] = int(session.query(func.count(DeliveryReport.id)).scalar() or 0)
                latest = session.query(
                    DeliveryReport.upload_batch_id,
                    DeliveryReport.imported_at
                ).order_by(DeliveryReport.imported_at.desc().nullslast()).first()
                if latest:
                    result["latest_upload_batch"] = latest.upload_batch_id
                    result["latest_imported_date"] = latest.imported_at.isoformat() if latest.imported_at else None
                duplicate_groups = session.query(DeliveryReport.dn_no).filter(
                    DeliveryReport.dn_no.isnot(None)
                ).group_by(DeliveryReport.dn_no).having(func.count(DeliveryReport.id) > 1).subquery()
                result["duplicate_dn_count"] = int(session.query(func.count()).select_from(duplicate_groups).scalar() or 0)
                existing_indexes = {index["name"] for index in inspector.get_indexes("delivery_reports")}
                expected_indexes = {"idx_dealer_status", "idx_city_status", "idx_pending_queries", "idx_warehouse_code_status"}
                result["missing_indexes"] = sorted(expected_indexes - existing_indexes)
                if result["missing_indexes"]:
                    result["warnings"].append(f"Missing recommended indexes: {result['missing_indexes']}")
            except Exception as diagnostic_error:
                result["warnings"].append(f"Extended diagnostics failed: {diagnostic_error}")
            
            session.close()
            
            if result["connected"] and result["table_exists"] and result["columns_valid"]:
                result["success"] = True
            
            return result
            
        except Exception as e:
            result["errors"].append(str(e))
            return result


# ==========================================================
# BLOCK 6: SERVICE REGISTRY
# ==========================================================

class ServiceRegistry:
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
        self._services = self.SERVICES.copy()
        self._status_cache = {}
        self._instance_cache = {}
        self._lock = threading.Lock()
        self._last_validation = None
        self._postgresql_validator = PostgreSQLValidator()
    
    def validate_all_services(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
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
        if service_key not in self._services:
            return {"status": ServiceStatus.NOT_STARTED, "ready": False, "errors": ["Not registered"]}
        
        service_def = self._services[service_key]
        
        result = {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": [],
            "checks_passed": 0,
            "checks_total": 7
        }
        
        if not pg_valid:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append("PostgreSQL validation failed")
            return result
        
        result["checks_passed"] += 1
        
        try:
            module = importlib.import_module(service_def.get("module"))
            result["checks_passed"] += 1
        except ImportError as e:
            result["status"] = ServiceStatus.NOT_STARTED
            result["errors"].append(f"Module not found: {e}")
            return result
        
        if not hasattr(module, service_def.get("class_name")):
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Class '{service_def.get('class_name')}' not found")
            return result
        
        cls = getattr(module, service_def.get("class_name"))
        result["checks_passed"] += 1
        
        missing_methods = []
        for method in service_def.get("methods", []):
            if not hasattr(cls, method):
                missing_methods.append(method)
        
        if missing_methods:
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Missing methods: {missing_methods}")
            return result
        
        result["checks_passed"] += 1
        
        try:
            instance = cls()
            result["checks_passed"] += 1
        except Exception as e:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append(f"Instantiation failed: {e}")
            return result
        
        if hasattr(instance, "health_check"):
            try:
                health = instance.health_check()
                if health.get("healthy", False):
                    result["checks_passed"] += 1
                else:
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Health check failed")
                    return result
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Health check exception: {e}")
                return result
        
        if hasattr(instance, "validation_query"):
            try:
                validation = instance.validation_query()
                if validation.get("success", False):
                    result["checks_passed"] += 1
                else:
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Validation failed")
                    return result
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Validation exception: {e}")
                return result
        
        result["status"] = ServiceStatus.READY
        result["ready"] = True
        result["instance"] = instance
        
        return result
    
    def get_service_status(self, service_key: str) -> Dict[str, Any]:
        if (service_key not in self._status_cache or 
            self._last_validation is None or 
            time.time() - self._last_validation > 60):
            
            pg_status = self._postgresql_validator.validate()
            self._status_cache[service_key] = self._validate_service(
                service_key, 
                pg_valid=pg_status.get("success", False)
            )
            
            if self._status_cache[service_key].get("ready", False):
                self._instance_cache[service_key] = self._status_cache[service_key].get("instance")
        
        return self._status_cache.get(service_key, {
            "status": ServiceStatus.NOT_STARTED,
            "ready": False,
            "errors": ["Service not validated"]
        })
    
    def is_service_ready(self, service_key: str) -> bool:
        status = self.get_service_status(service_key)
        return status.get("ready", False)
    
    def get_service_instance(self, service_key: str):
        if not self.is_service_ready(service_key):
            return None
        return self._instance_cache.get(service_key)
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
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
        results = {}
        for service_key in self._services:
            results[service_key] = self.get_service_status(service_key)
        return results
    
    def get_health_report(self) -> Dict[str, Any]:
        statuses = self.get_all_service_statuses()
        total = len(statuses)
        ready = sum(1 for s in statuses.values() if s.get("ready", False))
        in_dev = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.IN_DEVELOPMENT)
        not_started = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.NOT_STARTED)
        error = sum(1 for s in statuses.values() if s.get("status") == ServiceStatus.ERROR)
        
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
# BLOCK 7: WHATSAPP PROVIDER SERVICE - MASTER ROUTER
# ==========================================================

class WhatsAppProviderService:
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v5.1 - WITH WHATSAPP FORMATTING")
            logger.info("=" * 70)
            
            self.intent_engine = IntentDetectionEngine()
            logger.info("âœ… IntentDetectionEngine initialized")
            
            self.registry = ServiceRegistry()
            logger.info("âœ… ServiceRegistry initialized")
            self._intent_cache: Dict[str, Tuple[float, RoutingDecision]] = {}
            self._intent_cache_ttl = max(30, int(os.getenv("INTENT_CACHE_TTL", "300")))
            try:
                self._dealer_confidence_threshold = float(os.getenv("DEALER_INTENT_THRESHOLD", "85"))
            except (TypeError, ValueError):
                self._dealer_confidence_threshold = 85.0
                logger.warning("Invalid DEALER_INTENT_THRESHOLD; using 85")
            
            self._groq_service = None
            try:
                from app.services.groq_service import get_groq_service
                self._groq_service = get_groq_service()
                logger.info("âœ… GroqService initialized")
            except ImportError:
                logger.warning("âš ï¸ GroqService not available")
            except Exception as e:
                logger.error(f"âŒ GroqService initialization failed: {e}")
            
            self.registry.validate_all_services()
            
            init_duration = (time.time() - start_time) * 1000
            health = self.registry.get_health_report()
            
            logger.info("")
            logger.info("   SERVICE REGISTRY STATUS:")
            logger.info(f"   âœ… Ready: {health['ready']}")
            logger.info(f"   ðŸ”§ In Development: {health['in_development']}")
            logger.info(f"   â³ Not Started: {health['not_started']}")
            logger.info(f"   ðŸš¨ Error: {health['error']}")
            logger.info(f"   ðŸ“Š Readiness Score: {health['readiness_score']:.1f}%")
            logger.info("")
            
            pg_status = health.get('postgresql', {})
            logger.info(f"   PostgreSQL: {'âœ…' if pg_status.get('success') else 'âŒ'} {pg_status.get('connected', False)}")
            logger.info("")
            
            for service_key, status in health['services'].items():
                ready = status.get("ready", False)
                status_text = status.get("status", "UNKNOWN")
                checks = status.get("checks_passed", 0)
                total_checks = status.get("checks_total", 7)
                icon = "âœ…" if ready else "ðŸ”§"
                logger.info(f"   {icon} {service_key.title():15} â†’ {status_text} ({checks}/{total_checks} checks)")
            
            logger.info("")
            logger.info("   STATUS: âœ… PRODUCTION GRADE")
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"âŒ Failed to initialize: {str(e)}")
            raise
    
    # ==========================================================
    # MAIN ROUTING METHOD
    # ==========================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a WhatsApp query - MAIN ENTRY POINT.
        
        NO DEPENDENCY ON ai_query_service.py.
        Uses built-in intent detection.
        """
        logger.info(f"ðŸ“© Processing WhatsApp query: '{message[:100]}'")
        
        try:
            # STEP 1: Detect intent using built-in engine
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"ðŸŽ¯ Intent: {routing_decision.intent}, Service: {routing_decision.service_key}")
            
            # STEP 2: Preserve regex routing, then enrich only the unknown fallback.
            if routing_decision.service_key == "groq" and routing_decision.intent == "general_ai":
                routing_decision = self._detect_secondary_intent(message, routing_decision)
                logger.info(
                    "ðŸ§­ Secondary intent: %s confidence=%.2f service=%s method=%s",
                    routing_decision.intent, routing_decision.confidence,
                    routing_decision.service_key, routing_decision.method
                )

            # STEP 3: Check if this needs Groq
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                return await self._handle_groq(message, routing_decision)
            
            # STEP 4: Check Service Readiness
            service_key = routing_decision.service_key
            if not self.registry.is_service_ready(service_key):
                return self._format_module_unavailable(
                    message,
                    service_key,
                    self.registry.get_service_info(service_key)
                )
            
            # STEP 5: Execute Service
            result = await self._execute_service(routing_decision)
            payload = self._extract_service_payload(result)
            # Suggestions and structured service messages are valid WhatsApp
            # responses even when the service uses success=False.
            has_presentable_response = any(
                result.get(key) not in (None, "")
                for key in ("formatted_response", "whatsapp_message", "response", "message", "suggestions")
            )
            return self._format_response(
                message,
                payload,
                error=not result.get("success", False) and not has_presentable_response
            )
            
        except Exception as e:
            logger.exception(f"âŒ Failed: {e}")
            return self._format_response(
                message,
                f"âš ï¸ An unexpected error occurred.\n\nPlease try again later.",
                error=True
            )

    def _detect_secondary_intent(
        self,
        message: str,
        default: RoutingDecision
    ) -> RoutingDecision:
        """Dealer fuzzy lookup, then DB-backed warehouse/city/product lookup."""
        normalized = re.sub(r"\s+", " ", message.strip().lower())
        now = time.time()
        cached = self._intent_cache.get(normalized)
        if cached and now - cached[0] <= self._intent_cache_ttl:
            return cached[1]

        decision = default
        try:
            dealer = self.registry.get_service_instance("dealer")
            diagnose = getattr(dealer, "diagnose_dealer_search", None) if dealer else None
            if diagnose:
                diagnostic_result = diagnose(message)
                diagnostic = diagnostic_result.get("diagnostic", {}) if isinstance(diagnostic_result, dict) else {}
                found = diagnostic.get("dealer_found")
                score = diagnostic.get("rapidfuzz_score")
                suggestions = diagnostic.get("suggestions") or []
                best_suggestion = float(suggestions[0].get("similarity", 0)) if suggestions else 0.0
                confidence_score = float(score or (100 if found else best_suggestion))
                if found or confidence_score >= self._dealer_confidence_threshold:
                    decision = RoutingDecision(
                        intent="dealer_dashboard", service_key="dealer",
                        method="get_dealer_dashboard", entity=message.strip(),
                        confidence=min(1.0, confidence_score / 100.0), needs_groq=False,
                        reason="Dealer fuzzy/diagnostic match", original_message=message
                    )
                    logger.info("ðŸª Dealer auto-detected found=%r score=%.2f", found, confidence_score)
        except Exception:
            logger.exception("Dealer secondary detection failed; continuing routing pipeline")

        if decision is default:
            decision = self._detect_database_entity(message, default)

        self._intent_cache[normalized] = (now, decision)
        if len(self._intent_cache) > 2048:
            oldest = sorted(self._intent_cache.items(), key=lambda item: item[1][0])[:256]
            for key, _ in oldest:
                self._intent_cache.pop(key, None)
        return decision

    def _detect_database_entity(
        self,
        message: str,
        default: RoutingDecision
    ) -> RoutingDecision:
        """Resolve bare warehouse, city, or product values without raw SQL."""
        if not SessionLocal or DeliveryReport is None:
            return default
        token = message.strip()
        if not token or len(token) < 2:
            return default
        session = None
        try:
            session = SessionLocal()
            checks = (
                ("warehouse", DeliveryReport.warehouse, "warehouse_dashboard", "get_warehouse_dashboard"),
                ("city", DeliveryReport.ship_to_city, "city_dashboard", "get_city_dashboard"),
                ("product", DeliveryReport.customer_model, "product_dashboard", "get_product_dashboard"),
                ("product", DeliveryReport.material_no, "product_dashboard", "get_product_dashboard"),
            )
            for service_key, column, intent, method in checks:
                match = session.query(column).filter(
                    column.isnot(None),
                    func.lower(func.trim(column)) == token.lower()
                ).limit(1).scalar()
                if match:
                    return RoutingDecision(
                        intent=intent, service_key=service_key, method=method,
                        entity=str(match), confidence=0.92, needs_groq=False,
                        reason=f"PostgreSQL {service_key} exact match", original_message=message
                    )
            return default
        except Exception:
            logger.exception("Database entity fallback failed")
            return default
        finally:
            if session is not None:
                session.close()

    @staticmethod
    def _extract_service_payload(result: Dict[str, Any]) -> Any:
        """Preserve service-owned formatting and structured responses."""
        if not isinstance(result, dict):
            return result
        for key in (
            "formatted_response", "whatsapp_message", "response", "message",
            "dashboard", "profile", "data", "suggestions", "error"
        ):
            value = result.get(key)
            if value not in (None, ""):
                return value
        return result
    
    # ==========================================================
    # GROQ HANDLING
    # ==========================================================
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq queries."""
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if isinstance(response, dict) and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
                    if isinstance(response, str) and response.strip():
                        return self._format_response(message, response, error=False)
            except Exception as e:
                logger.error(f"âŒ Groq failed: {e}")
        
        # Fallback
        if decision.intent == "greeting":
            return self._format_response(
                message,
                "Hello! How can I help you with your logistics today?",
                error=False
            )
        elif decision.intent == "help":
            return self._format_response(
                message,
                "ðŸ“‹ Available Commands:\n\n"
                "ðŸ“¦ DN Queries:\n"
                "- Send a DN number (8-12 digits)\n"
                "- 'Pending DN'\n"
                "- 'Pending PGI'\n"
                "- 'Pending POD'\n\n"
                "ðŸª Dealer Queries:\n"
                "- 'Dealer [name]'\n"
                "- '[Dealer name] profile'\n\n"
                "ðŸ­ Warehouse Queries:\n"
                "- 'Warehouse [name]'\n\n"
                "ðŸ™ï¸ City Queries:\n"
                "- 'City [name]'\n\n"
                "ðŸ“¦ Product Queries:\n"
                "- 'Product [name]'\n\n"
                "All data comes from PostgreSQL.",
                error=False
            )
        else:
            return self._format_response(
                message,
                "I couldn't confidently identify that request. Please include a DN number, dealer, warehouse, city, or product name. Type 'Help' for examples.",
                error=False
            )
    
    # ==========================================================
    # SERVICE EXECUTION
    # ==========================================================
    
    async def _execute_service(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Execute service."""
        started = time.perf_counter()
        service_instance = self.registry.get_service_instance(decision.service_key)
        if not service_instance:
            return {"success": False, "error": f"Service '{decision.service_key}' not available"}
        
        try:
            method = getattr(service_instance, decision.method, None)
            if not method:
                return {"success": False, "error": f"Method '{decision.method}' not found"}
            
            if decision.entity:
                if decision.entity2:
                    result = method(decision.entity, decision.entity2)
                else:
                    result = method(decision.entity)
            else:
                result = method()
            
            if inspect.iscoroutine(result):
                result = await result
            
            return result if isinstance(result, dict) else {"success": True, "data": result}
            
        except Exception as e:
            logger.exception(f"âŒ Service execution failed: {e}")
            return {"success": False, "error": str(e)}
        finally:
            logger.info(
                "â±ï¸ Service execution service=%s method=%s duration_ms=%.2f",
                decision.service_key, decision.method,
                (time.perf_counter() - started) * 1000
            )
    
    # ==========================================================
    # âœ… FIXED: RESPONSE FORMATTING WITH WHATSAPP FORMATTER
    # ==========================================================
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        """
        Format response for WhatsApp.
        
        âœ… FIXED: If data is a DNDashboard object, format it professionally
        âœ… PRESERVES: All attributes and data
        âœ… HANDLES: DNDashboard objects, dicts, strings, and other types
        """
        # If it's an error, return as-is
        if error:
            return {
                "success": not error,
                "message": original_message,
                "response": data,
                "error": error,
                "timestamp": datetime.now().isoformat()
            }
        
        # Preserve formatting owned by any analytics dashboard.
        if hasattr(data, "to_whatsapp_message"):
            try:
                data = data.to_whatsapp_message()
            except Exception:
                logger.exception("Generic dashboard WhatsApp formatting failed")

        if isinstance(data, dict):
            for key in ("formatted_response", "whatsapp_message", "response", "message"):
                if data.get(key) not in (None, ""):
                    data = data[key]
                    break

        # ============================================================
        # âœ… FIX: Format DNDashboard objects for WhatsApp
        # ============================================================
        if hasattr(data, 'dn_no'):
            try:
                # Import the formatter from webhook
                from app.routes.webhook import format_dn_response
                formatted_data = format_dn_response(data)
                logger.info("ðŸ“± Formatted DNDashboard for WhatsApp")
                return {
                    "success": not error,
                    "message": original_message,
                    "response": formatted_data,  # â† Now a beautiful string!
                    "error": error,
                    "timestamp": datetime.now().isoformat()
                }
            except ImportError as e:
                logger.warning(f"âš ï¸ Could not import formatter: {e}")
            except Exception as e:
                logger.warning(f"âš ï¸ Formatting failed: {e}")
        
        # ============================================================
        # âœ… Handle dictionaries with 'data' field
        # ============================================================
        if isinstance(data, dict) and 'data' in data:
            inner_data = data['data']
            if hasattr(inner_data, 'dn_no'):
                try:
                    from app.routes.webhook import format_dn_response
                    formatted_data = format_dn_response(inner_data)
                    return {
                        "success": not error,
                        "message": original_message,
                        "response": formatted_data,
                        "error": error,
                        "timestamp": datetime.now().isoformat()
                    }
                except:
                    pass
        
        # ============================================================
        # âœ… Handle dictionaries with 'response' field
        # ============================================================
        if isinstance(data, dict) and 'response' in data:
            inner_data = data['response']
            if hasattr(inner_data, 'dn_no'):
                try:
                    from app.routes.webhook import format_dn_response
                    formatted_data = format_dn_response(inner_data)
                    return {
                        "success": not error,
                        "message": original_message,
                        "response": formatted_data,
                        "error": error,
                        "timestamp": datetime.now().isoformat()
                    }
                except:
                    pass
        
        # ============================================================
        # âœ… Default: Return data as-is
        # ============================================================
        return {
            "success": not error,
            "message": original_message,
            "response": data,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
    
    def _format_module_unavailable(self, original_message: str, service_key: str, info: Dict[str, Any]) -> Dict[str, Any]:
        status_text = info.get("status", "UNKNOWN")
        errors = info.get("errors", [])
        checks_passed = info.get("checks_passed", 0)
        checks_total = info.get("checks_total", 7)
        
        message = f"""âš ï¸ Module Currently Under Development

Module:
{service_key.title()} Service

Status:
{status_text}

Readiness:
{checks_passed}/{checks_total} checks passed

"""
        if errors:
            message += f"\nMissing:\n{chr(10).join(['- ' + e for e in errors[:3]])}"
        message += "\n\nPlease try again after development is completed."
        
        return self._format_response(original_message, message, error=True)
    
    # ==========================================================
    # DIAGNOSTIC METHODS
    # ==========================================================
    
    def get_service_registry_status(self) -> Dict[str, Any]:
        return self.registry.get_health_report()
    
    def validate_all_services(self) -> Dict[str, Any]:
        return self.registry.validate_all_services()
    
    def get_system_health(self) -> Dict[str, Any]:
        service_health = self.registry.get_health_report()
        return {
            "services": service_health,
            "system_status": "healthy" if service_health.get("readiness_score", 0) > 50 else "degraded",
            "timestamp": datetime.now().isoformat(),
            "version": "5.2"
        }
    
    def get_service_info(self, service_key: str) -> Dict[str, Any]:
        return self.registry.get_service_info(service_key)
    
    def refresh_service_status(self, service_key: str = None) -> Dict[str, Any]:
        if service_key:
            self.registry._status_cache.pop(service_key, None)
            self.registry._instance_cache.pop(service_key, None)
            return self.registry.get_service_status(service_key)
        else:
            return self.registry.validate_all_services()


# ==========================================================
# BLOCK 8: THREAD-SAFE SINGLETON
# ==========================================================

_whatsapp_provider_service = None
_provider_service_lock = threading.Lock()

def get_whatsapp_provider_service() -> WhatsAppProviderService:
    global _whatsapp_provider_service
    if _whatsapp_provider_service is None:
        with _provider_service_lock:
            if _whatsapp_provider_service is None:
                try:
                    _whatsapp_provider_service = WhatsAppProviderService()
                    logger.info("âœ… WhatsAppProviderService singleton initialized")
                except Exception as e:
                    logger.exception(f"âŒ Initialization failed: {e}")
                    raise
    return _whatsapp_provider_service


# ==========================================================
# BLOCK 9: EXPORTS
# ==========================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'ServiceStatus',
    'RoutingDecision',
    'IntentDetectionEngine'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v5.2 - INTELLIGENT ROUTING")
logger.info("=" * 70)
logger.info("âœ… Intent detection built-in")
logger.info("âœ… No external routing dependencies")
logger.info("âœ… WhatsApp formatting enabled")
logger.info("âœ… Ready for production")
logger.info("=" * 70)
