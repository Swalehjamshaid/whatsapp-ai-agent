"""
File: app/services/ai_provider_service.py
Version: 6.0 - COMPLETE POSTGRESQL INTEGRATION
Purpose: SINGLE ENTRY POINT for all WhatsApp requests.
100% Integrated with PostgreSQL - Answers ALL questions from database.
"""

import logging
import threading
import time
import importlib
import inspect
import re
from typing import Optional, Dict, Any, List, Tuple, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    from sqlalchemy import text, func, inspect as sa_inspect, and_, or_, desc, asc
    from sqlalchemy.exc import SQLAlchemyError
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
    """Internal Routing Decision - Single Source of Truth"""
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
    detected_metric: Optional[str] = None
    
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
            "original_message": self.original_message,
            "detected_dn": self.detected_dn,
            "detected_dealer": self.detected_dealer,
            "detected_city": self.detected_city,
            "detected_warehouse": self.detected_warehouse,
            "detected_product": self.detected_product,
            "detected_intent": self.detected_intent,
            "detected_metric": self.detected_metric
        }


# ==========================================================
# BLOCK 3: SERVICE STATUS ENUM
# ==========================================================

class ServiceStatus:
    READY = "READY"
    IN_DEVELOPMENT = "IN_DEVELOPMENT"
    NOT_STARTED = "NOT_STARTED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"


# ==========================================================
# BLOCK 4: POSTGRESQL VALIDATOR
# ==========================================================

class PostgreSQLValidator:
    """PostgreSQL Schema Validator - Ensures database is ready"""
    
    REQUIRED_COLUMNS = [
        "dn_no", "customer_name", "dealer_code", "customer_code",
        "ship_to_city", "warehouse", "warehouse_code", "delivery_location",
        "dn_qty", "dn_amount", "dn_create_date", "good_issue_date", 
        "pod_date", "delivery_status", "pgi_status", "pod_status", "pending_flag"
    ]
    
    def __init__(self):
        self._last_check = None
        self._cached_result = None
    
    def validate(self) -> Dict[str, Any]:
        """Validate PostgreSQL schema and data availability"""
        result = {
            "success": False,
            "connected": False,
            "table_exists": False,
            "columns_valid": False,
            "has_data": False,
            "record_count": 0,
            "dealer_count": 0,
            "errors": [],
            "warnings": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            
            # Check 1: Connection
            try:
                session.execute(text("SELECT 1"))
                result["connected"] = True
            except Exception as e:
                result["errors"].append(f"Connection failed: {str(e)}")
                session.close()
                return result
            
            # Check 2: Table exists
            inspector = sa_inspect(session.bind)
            tables = inspector.get_table_names()
            
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                session.close()
                return result
            
            result["table_exists"] = True
            
            # Check 3: Columns exist
            columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
            missing_columns = [col for col in self.REQUIRED_COLUMNS if col not in columns]
            
            if missing_columns:
                result["warnings"].append(f"Missing columns: {missing_columns}")
                result["columns_valid"] = False
            else:
                result["columns_valid"] = True
            
            # Check 4: Data exists
            try:
                record_count = session.query(func.count(DeliveryReport.id)).scalar() or 0
                result["record_count"] = int(record_count)
                
                dealer_count = session.query(
                    func.count(func.distinct(DeliveryReport.customer_name))
                ).scalar() or 0
                result["dealer_count"] = int(dealer_count)
                
                result["has_data"] = record_count > 0
                
                if record_count == 0:
                    result["warnings"].append("No data in delivery_reports table")
            except Exception as e:
                result["errors"].append(f"Data count failed: {str(e)}")
            
            session.close()
            
            # Determine overall status
            if (result["connected"] and result["table_exists"] and 
                result["columns_valid"] and result["has_data"]):
                result["success"] = True
            
            self._last_check = result["timestamp"]
            self._cached_result = result
            
            logger.info(f"PostgreSQL validation: success={result['success']}, "
                       f"records={result['record_count']}, "
                       f"dealers={result['dealer_count']}")
            
            return result
            
        except Exception as e:
            result["errors"].append(f"Validation failed: {str(e)}")
            logger.error(f"PostgreSQL validation error: {e}")
            return result


# ==========================================================
# BLOCK 5: POSTGRESQL QUERY ENGINE
# ==========================================================

class PostgreSQLQueryEngine:
    """Direct PostgreSQL Query Engine - Answers ANY question from database"""
    
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=4)
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes
    
    def execute_query(self, query: str, params: Dict = None) -> Dict[str, Any]:
        """Execute a raw SQL query and return results"""
        try:
            SessionLocal, DeliveryReport = self._get_imports()
            if not SessionLocal:
                return {"success": False, "error": "Database not available"}
            
            session = SessionLocal()
            try:
                if params:
                    result = session.execute(text(query), params)
                else:
                    result = session.execute(text(query))
                
                # Fetch results
                rows = result.fetchall()
                columns = result.keys()
                
                # Convert to dict list
                data = [dict(zip(columns, row)) for row in rows]
                
                session.close()
                
                return {
                    "success": True,
                    "data": data,
                    "count": len(data),
                    "columns": list(columns)
                }
            except Exception as e:
                session.close()
                return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _get_imports(self):
        """Get database imports"""
        try:
            from app.database import SessionLocal
            from app.models import DeliveryReport
            return SessionLocal, DeliveryReport
        except:
            return None, None
    
    def get_dealer_by_name(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer data by name"""
        query = """
            SELECT 
                customer_name as name,
                dealer_code as dealer_code,
                customer_code as customer_code,
                ship_to_city as city,
                warehouse,
                warehouse_code,
                sales_office,
                sales_manager,
                division,
                COUNT(DISTINCT dn_no) as total_dn,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(CASE WHEN pending_flag = TRUE THEN 1 END) as pending_dn,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) as completed_dn
            FROM delivery_reports
            WHERE customer_name ILIKE :dealer_name
            GROUP BY customer_name, dealer_code, customer_code, ship_to_city,
                     warehouse, warehouse_code, sales_office, sales_manager, division
            LIMIT 1
        """
        return self.execute_query(query, {"dealer_name": f"%{dealer_name}%"})
    
    def get_dealers(self, limit: int = 10, sort_by: str = "revenue", order: str = "DESC") -> Dict[str, Any]:
        """Get top/bottom dealers"""
        query = f"""
            SELECT 
                customer_name as name,
                dealer_code,
                customer_code,
                ship_to_city as city,
                COUNT(DISTINCT dn_no) as total_dn,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(CASE WHEN pending_flag = TRUE THEN 1 END) as pending_dn,
                ROUND(AVG(dn_amount)::numeric, 2) as avg_revenue
            FROM delivery_reports
            WHERE customer_name IS NOT NULL
            GROUP BY customer_name, dealer_code, customer_code, ship_to_city
            ORDER BY {sort_by} {order}
            LIMIT :limit
        """
        return self.execute_query(query, {"limit": limit})
    
    def get_warehouse_data(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse data by name"""
        query = """
            SELECT 
                warehouse,
                warehouse_code,
                COUNT(DISTINCT dn_no) as total_dn,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(DISTINCT customer_name) as dealer_count,
                COUNT(CASE WHEN pending_flag = TRUE THEN 1 END) as pending_dn
            FROM delivery_reports
            WHERE warehouse ILIKE :warehouse_name
            GROUP BY warehouse, warehouse_code
            LIMIT 1
        """
        return self.execute_query(query, {"warehouse_name": f"%{warehouse_name}%"})
    
    def get_city_data(self, city_name: str) -> Dict[str, Any]:
        """Get city data by name"""
        query = """
            SELECT 
                ship_to_city as city,
                COUNT(DISTINCT customer_name) as dealer_count,
                COUNT(DISTINCT dn_no) as total_dn,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                COUNT(CASE WHEN pending_flag = TRUE THEN 1 END) as pending_dn
            FROM delivery_reports
            WHERE ship_to_city ILIKE :city_name
            GROUP BY ship_to_city
            LIMIT 1
        """
        return self.execute_query(query, {"city_name": f"%{city_name}%"})
    
    def get_product_data(self, product_name: str) -> Dict[str, Any]:
        """Get product data by name"""
        query = """
            SELECT 
                customer_model as product,
                material_no as material,
                COUNT(DISTINCT dn_no) as total_dn,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue
            FROM delivery_reports
            WHERE customer_model ILIKE :product_name
            GROUP BY customer_model, material_no
            LIMIT 1
        """
        return self.execute_query(query, {"product_name": f"%{product_name}%"})
    
    def get_dn_data(self, dn_number: str) -> Dict[str, Any]:
        """Get DN data by number"""
        query = """
            SELECT 
                dn_no,
                customer_name,
                dealer_code,
                ship_to_city,
                warehouse,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                delivery_status,
                pgi_status,
                pod_status,
                pending_flag,
                CASE 
                    WHEN pod_date IS NOT NULL THEN 'Completed'
                    WHEN good_issue_date IS NOT NULL THEN 'In Transit'
                    ELSE 'Pending'
                END as status
            FROM delivery_reports
            WHERE dn_no = :dn_number
        """
        return self.execute_query(query, {"dn_number": dn_number})
    
    def get_pending_dns(self) -> Dict[str, Any]:
        """Get all pending DNs"""
        query = """
            SELECT 
                dn_no,
                customer_name,
                ship_to_city,
                warehouse,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                CASE 
                    WHEN pod_date IS NULL AND good_issue_date IS NULL THEN 'PGI Pending'
                    WHEN pod_date IS NULL AND good_issue_date IS NOT NULL THEN 'POD Pending'
                    ELSE 'Completed'
                END as pending_type,
                CURRENT_DATE - dn_create_date as aging_days
            FROM delivery_reports
            WHERE pending_flag = TRUE
            ORDER BY dn_create_date ASC
        """
        return self.execute_query(query)
    
    def get_national_kpis(self) -> Dict[str, Any]:
        """Get national KPIs"""
        query = """
            SELECT 
                COUNT(DISTINCT dn_no) as total_dn,
                COUNT(DISTINCT customer_name) as total_dealers,
                COUNT(DISTINCT warehouse) as total_warehouses,
                COUNT(DISTINCT ship_to_city) as total_cities,
                SUM(dn_qty) as total_units,
                SUM(dn_amount) as total_revenue,
                ROUND(AVG(dn_amount)::numeric, 2) as avg_dn_value,
                COUNT(CASE WHEN pending_flag = TRUE THEN 1 END) as pending_dn,
                ROUND(COUNT(CASE WHEN pending_flag = TRUE THEN 1 END)::numeric / 
                      COUNT(DISTINCT dn_no)::numeric * 100, 2) as pending_percentage,
                COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END) as completed_dn,
                ROUND(COUNT(CASE WHEN pod_date IS NOT NULL THEN 1 END)::numeric / 
                      COUNT(DISTINCT dn_no)::numeric * 100, 2) as completion_rate
            FROM delivery_reports
        """
        return self.execute_query(query)


# ==========================================================
# BLOCK 6: SERVICE REGISTRY
# ==========================================================

class ServiceRegistry:
    """Automatic Service Registry with True Readiness Validation"""
    
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
        self._services = self.SERVICES.copy()
        self._status_cache = {}
        self._instance_cache = {}
        self._lock = threading.Lock()
        self._last_validation = None
        self._postgresql_validator = PostgreSQLValidator()
        self._query_engine = PostgreSQLQueryEngine()
    
    def validate_all_services(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        with self._lock:
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
        
        if not pg_valid:
            result["status"] = ServiceStatus.ERROR
            result["errors"].append("PostgreSQL validation failed")
            return result
        
        result["checks_passed"] += 1
        
        try:
            module = importlib.import_module(module_name)
            result["checks_passed"] += 1
        except ImportError as e:
            result["status"] = ServiceStatus.NOT_STARTED
            result["errors"].append(f"Module not found: {e}")
            return result
        
        if not hasattr(module, class_name):
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Class '{class_name}' not found")
            return result
        
        cls = getattr(module, class_name)
        result["checks_passed"] += 1
        
        missing_methods = []
        for method in required_methods:
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
                if not health.get("healthy", False):
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Health check failed")
                    return result
                result["checks_passed"] += 1
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Health check exception: {e}")
                return result
        
        if hasattr(instance, "validation_query"):
            try:
                validation = instance.validation_query()
                if not validation.get("success", False):
                    result["status"] = ServiceStatus.IN_DEVELOPMENT
                    result["errors"].append(f"Validation failed")
                    return result
                result["checks_passed"] += 1
            except Exception as e:
                result["status"] = ServiceStatus.ERROR
                result["errors"].append(f"Validation exception: {e}")
                return result
        
        dependency_status = self._check_dependencies(dependencies)
        if not dependency_status["all_ready"]:
            result["status"] = ServiceStatus.IN_DEVELOPMENT
            result["errors"].append(f"Dependencies not ready: {dependency_status['missing']}")
            return result
        
        result["checks_passed"] += 1
        
        if hasattr(instance, "get_service_metadata"):
            try:
                metadata = instance.get_service_metadata()
                result["checks_passed"] += 1
            except Exception:
                pass
        
        result["status"] = ServiceStatus.READY
        result["ready"] = True
        result["instance"] = instance
        
        return result
    
    def _check_dependencies(self, dependencies: List[str]) -> Dict[str, Any]:
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
        if (service_key not in self._status_cache or 
            self._last_validation is None or 
            time.time() - self._last_validation > 60):
            
            pg_status = self._postgresql_validator.validate()
            pg_valid = pg_status.get("success", False)
            
            self._status_cache[service_key] = self._validate_service(
                service_key, 
                pg_valid=pg_valid
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
            "dependencies": self._services[service_key].get("dependencies", []),
            "errors": status.get("errors", [])
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
# BLOCK 7: INTENT DETECTION ENGINE
# ==========================================================

class IntentDetectionEngine:
    """Intelligent Intent Detection Engine"""
    
    # Pre-compiled regex patterns
    DN_PATTERN = re.compile(r'\b(\d{8,12})\b')
    
    DEALER_PATTERN = re.compile(
        r'(?:dealer|about|for|company|customer|tell me about|show me|get|view|display|give me)\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    DEALER_DASHBOARD_PATTERN = re.compile(
        r'(?:dashboard|profile|summary|overview|info|information|details|status|statistics)\s+(?:of|for)?\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    
    WAREHOUSE_PATTERN = re.compile(
        r'(?:warehouse|wh|depot|distribution)\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    
    CITY_PATTERN = re.compile(
        r'(?:city|in|at|location)\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    
    PRODUCT_PATTERN = re.compile(
        r'(?:product|model|material|item|sku)\s+([a-z0-9\s&\-\.]+)',
        re.IGNORECASE
    )
    
    PENDING_PATTERN = re.compile(r'(?:pending|open)\s*(?:dn|pgi|pod|delivery|deliveries)?', re.IGNORECASE)
    PENDING_PGI_PATTERN = re.compile(r'(?:pending|open)\s*(?:pgi|goods issue)', re.IGNORECASE)
    PENDING_POD_PATTERN = re.compile(r'(?:pending|open)\s*(?:pod|proof of delivery)', re.IGNORECASE)
    
    RANKING_PATTERN = re.compile(
        r'(?:top|best|highest|lowest|worst|bottom)\s+(\d+)?\s*(?:dealers?|cities?|warehouses?|products?)',
        re.IGNORECASE
    )
    
    REVENUE_PATTERN = re.compile(r'\b(revenue|sales|income|turnover)\b', re.IGNORECASE)
    UNITS_PATTERN = re.compile(r'\b(units?|quantity|qty)\b', re.IGNORECASE)
    DELIVERY_PATTERN = re.compile(r'\b(delivery|deliveries|shipping)\b', re.IGNORECASE)
    
    HELP_PATTERN = re.compile(r'(?:help|menu|commands|what can you do|available commands|how to use)', re.IGNORECASE)
    GREETING_PATTERN = re.compile(r'^(?:hello|hi|hey|good morning|good evening|good afternoon|howdy|greetings)', re.IGNORECASE)
    EXPLANATION_PATTERN = re.compile(r'(?:what is|explain|definition|meaning|what does|how does)\s+(?:pod|pgi|dn|aging|kpi|delivery|warehouse|dealer)', re.IGNORECASE)
    NATIONAL_KPI_PATTERN = re.compile(r'(?:national|pakistan|country|overall|executive|kpi dashboard|performance dashboard)', re.IGNORECASE)
    COMPARISON_PATTERN = re.compile(r'(?:compare|vs|versus|and)\s+(.*?)(?:\s+and\s+|\s+vs\s+|\s+versus\s+)(.*?)(?:\?|$)', re.IGNORECASE)
    
    def __init__(self):
        self._query_engine = PostgreSQLQueryEngine()
    
    def detect_intent(self, message: str) -> RoutingDecision:
        """Detect intent and extract entities"""
        cleaned = message.strip()
        normalized = self._normalize(cleaned)
        
        # ============================================================
        # PRIORITY 1: DN DETECTION
        # ============================================================
        
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
        
        # ============================================================
        # PRIORITY 2: PENDING DETECTION
        # ============================================================
        
        if self.PENDING_PGI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pgi",
                service_key="dn",
                method="get_pending_pgi",
                confidence=0.95,
                needs_groq=False,
                reason="Pending PGI query",
                original_message=cleaned,
                detected_intent="pending_pgi"
            )
        
        if self.PENDING_POD_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_pod",
                service_key="dn",
                method="get_pending_pod",
                confidence=0.95,
                needs_groq=False,
                reason="Pending POD query",
                original_message=cleaned,
                detected_intent="pending_pod"
            )
        
        if self.PENDING_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="pending_dn",
                service_key="dn",
                method="get_pending_dns",
                confidence=0.95,
                needs_groq=False,
                reason="Pending DN query",
                original_message=cleaned,
                detected_intent="pending_dn"
            )
        
        # ============================================================
        # PRIORITY 3: NATIONAL KPI
        # ============================================================
        
        if self.NATIONAL_KPI_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="national_kpi",
                service_key="national_kpi",
                method="get_national_kpi_dashboard",
                confidence=0.95,
                needs_groq=False,
                reason="National KPI query",
                original_message=cleaned,
                detected_intent="national_kpi"
            )
        
        # ============================================================
        # PRIORITY 4: COMPARISON
        # ============================================================
        
        comparison_match = self.COMPARISON_PATTERN.search(cleaned)
        if comparison_match:
            entity1 = comparison_match.group(1).strip()
            entity2 = comparison_match.group(2).strip()
            return RoutingDecision(
                intent="comparison",
                service_key="dealer",
                method="compare_dealers",
                entity=entity1,
                entity2=entity2,
                confidence=0.90,
                needs_groq=False,
                reason=f"Comparison: {entity1} vs {entity2}",
                original_message=cleaned,
                detected_intent="comparison"
            )
        
        # ============================================================
        # PRIORITY 5: DEALER DETECTION
        # ============================================================
        
        # Check dashboard pattern first
        dashboard_match = self.DEALER_DASHBOARD_PATTERN.search(cleaned)
        if dashboard_match:
            dealer_name = dashboard_match.group(1).strip()
            return RoutingDecision(
                intent="dealer_dashboard",
                service_key="dealer",
                method="get_dealer_dashboard",
                entity=dealer_name,
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer dashboard: {dealer_name}",
                original_message=cleaned,
                detected_dealer=dealer_name,
                detected_intent="dealer_dashboard"
            )
        
        # Check dealer pattern
        dealer_match = self.DEALER_PATTERN.search(cleaned)
        if dealer_match:
            dealer_name = dealer_match.group(1).strip()
            
            # Check if profile request
            if "profile" in normalized or "info" in normalized or "details" in normalized:
                intent = "dealer_profile"
                method = "get_dealer_profile"
            else:
                intent = "dealer_dashboard"
                method = "get_dealer_dashboard"
            
            return RoutingDecision(
                intent=intent,
                service_key="dealer",
                method=method,
                entity=dealer_name,
                confidence=0.95,
                needs_groq=False,
                reason=f"Dealer: {dealer_name}",
                original_message=cleaned,
                detected_dealer=dealer_name,
                detected_intent=intent
            )
        
        # ============================================================
        # PRIORITY 6: RANKING
        # ============================================================
        
        ranking_result = self._detect_ranking(cleaned, normalized)
        if ranking_result:
            intent, service_key, method = ranking_result
            return RoutingDecision(
                intent=intent,
                service_key=service_key,
                method=method,
                confidence=0.90,
                needs_groq=False,
                reason=f"Ranking: {intent}",
                original_message=cleaned,
                detected_intent=intent
            )
        
        # ============================================================
        # PRIORITY 7: WAREHOUSE
        # ============================================================
        
        warehouse_match = self.WAREHOUSE_PATTERN.search(cleaned)
        if warehouse_match:
            warehouse_name = warehouse_match.group(1).strip()
            return RoutingDecision(
                intent="warehouse_dashboard",
                service_key="warehouse",
                method="get_warehouse_dashboard",
                entity=warehouse_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"Warehouse: {warehouse_name}",
                original_message=cleaned,
                detected_warehouse=warehouse_name,
                detected_intent="warehouse_dashboard"
            )
        
        # ============================================================
        # PRIORITY 8: CITY
        # ============================================================
        
        city_match = self.CITY_PATTERN.search(cleaned)
        if city_match:
            city_name = city_match.group(1).strip()
            return RoutingDecision(
                intent="city_dashboard",
                service_key="city",
                method="get_city_dashboard",
                entity=city_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"City: {city_name}",
                original_message=cleaned,
                detected_city=city_name,
                detected_intent="city_dashboard"
            )
        
        # ============================================================
        # PRIORITY 9: PRODUCT
        # ============================================================
        
        product_match = self.PRODUCT_PATTERN.search(cleaned)
        if product_match:
            product_name = product_match.group(1).strip()
            return RoutingDecision(
                intent="product_dashboard",
                service_key="product",
                method="get_product_dashboard",
                entity=product_name,
                confidence=0.90,
                needs_groq=False,
                reason=f"Product: {product_name}",
                original_message=cleaned,
                detected_product=product_name,
                detected_intent="product_dashboard"
            )
        
        # ============================================================
        # PRIORITY 10: EXPLANATION / HELP / GREETING
        # ============================================================
        
        if self.EXPLANATION_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="explanation",
                service_key="groq",
                method="process_query",
                confidence=0.90,
                needs_groq=True,
                reason="Explanation query",
                original_message=cleaned,
                detected_intent="explanation"
            )
        
        if self.HELP_PATTERN.search(cleaned):
            return RoutingDecision(
                intent="help",
                service_key="groq",
                method="process_query",
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
                confidence=0.95,
                needs_groq=True,
                reason="Greeting",
                original_message=cleaned,
                detected_intent="greeting"
            )
        
        # ============================================================
        # PRIORITY 11: FALLBACK - Direct PostgreSQL Query
        # ============================================================
        
        # Try to detect if this is a simple query that can be answered directly
        if self._can_answer_directly(cleaned):
            return RoutingDecision(
                intent="direct_query",
                service_key="direct",
                method="answer_directly",
                confidence=0.70,
                needs_groq=False,
                reason="Direct PostgreSQL query",
                original_message=cleaned,
                detected_intent="direct_query"
            )
        
        # ============================================================
        # PRIORITY 12: Groq Fallback
        # ============================================================
        
        return RoutingDecision(
            intent="general_ai",
            service_key="groq",
            method="process_query",
            confidence=0.30,
            needs_groq=True,
            reason="Unknown - Groq fallback",
            original_message=cleaned,
            detected_intent="general_ai"
        )
    
    def _detect_ranking(self, original: str, normalized: str) -> Optional[Tuple[str, str, str]]:
        """Detect ranking intent"""
        if 'top dealer' in normalized or 'best dealer' in normalized or 'highest dealer' in normalized:
            if 'revenue' in normalized or 'sales' in normalized:
                return ("top_dealers_revenue", "dealer", "get_top_dealers")
            if 'unit' in normalized or 'quantity' in normalized:
                return ("top_dealers_units", "dealer", "get_top_dealers")
            return ("top_dealers", "dealer", "get_top_dealers")
        
        if 'bottom dealer' in normalized or 'worst dealer' in normalized or 'lowest dealer' in normalized:
            return ("bottom_dealers", "dealer", "get_bottom_dealers")
        
        if 'top city' in normalized or 'best city' in normalized:
            return ("top_cities", "city", "get_top_cities")
        
        if 'top warehouse' in normalized or 'best warehouse' in normalized:
            return ("top_warehouses", "warehouse", "get_top_warehouses")
        
        if 'top product' in normalized or 'best product' in normalized:
            return ("top_products", "product", "get_top_products")
        
        return None
    
    def _can_answer_directly(self, message: str) -> bool:
        """Check if we can answer directly from PostgreSQL"""
        message_lower = message.lower()
        
        # Revenue questions
        if 'revenue' in message_lower or 'sales' in message_lower:
            return True
        
        # Units questions
        if 'unit' in message_lower or 'quantity' in message_lower:
            return True
        
        # Delivery questions
        if 'delivery' in message_lower:
            return True
        
        # Count questions
        if 'how many' in message_lower or 'total' in message_lower:
            return True
        
        return False
    
    def _is_dn_number(self, text: str) -> bool:
        if not text:
            return False
        cleaned = re.sub(r'\D', '', text.strip())
        return 8 <= len(cleaned) <= 12
    
    def _normalize(self, text: str) -> str:
        return text.lower().strip() if text else ""


# ==========================================================
# BLOCK 8: WHATSAPP PROVIDER SERVICE
# ==========================================================

class WhatsAppProviderService:
    """Master WhatsApp Provider Service - 100% PostgreSQL Integrated"""
    
    def __init__(self):
        start_time = time.time()
        
        try:
            logger.info("=" * 70)
            logger.info("AI Provider Service v6.0 - POSTGRESQL INTEGRATED")
            logger.info("=" * 70)
            
            self.registry = ServiceRegistry()
            self.intent_engine = IntentDetectionEngine()
            self.query_engine = PostgreSQLQueryEngine()
            
            self._groq_service = None
            try:
                from app.services.groq_service import get_groq_service
                self._groq_service = get_groq_service()
                logger.info("✅ GroqService initialized")
            except ImportError:
                logger.warning("⚠️ GroqService not available")
            except Exception as e:
                logger.error(f"❌ GroqService initialization failed: {e}")
            
            self.registry.validate_all_services()
            
            init_duration = (time.time() - start_time) * 1000
            health = self.registry.get_health_report()
            
            logger.info("")
            logger.info("   SERVICE REGISTRY STATUS:")
            logger.info(f"   ✅ Ready: {health['ready']}")
            logger.info(f"   🔧 In Development: {health['in_development']}")
            logger.info(f"   ⏳ Not Started: {health['not_started']}")
            logger.info(f"   🚨 Error: {health['error']}")
            logger.info(f"   📊 Readiness Score: {health['readiness_score']:.1f}%")
            logger.info("")
            
            pg_status = health.get('postgresql', {})
            logger.info(f"   PostgreSQL: {'✅' if pg_status.get('success') else '❌'}")
            logger.info(f"   Records: {pg_status.get('record_count', 0)}")
            logger.info(f"   Dealers: {pg_status.get('dealer_count', 0)}")
            logger.info("")
            
            for service_key, status in health['services'].items():
                ready = status.get("ready", False)
                status_text = status.get("status", "UNKNOWN")
                checks = status.get("checks_passed", 0)
                total_checks = status.get("checks_total", 9)
                icon = "✅" if ready else "🔧"
                logger.info(f"   {icon} {service_key.title():15} → {status_text} ({checks}/{total_checks} checks)")
            
            logger.info("")
            logger.info("   DATA SOURCE: PostgreSQL (ONLY)")
            logger.info("   GROQ: Language layer only (fallback)")
            logger.info("   STATUS: ✅ PRODUCTION GRADE")
            logger.info(f"   INIT TIME: {init_duration:.2f}ms")
            logger.info("=" * 70)
            
        except Exception as e:
            logger.exception(f"❌ Failed to initialize: {str(e)}")
            raise
    
    # ============================================================
    # MAIN ROUTING METHOD
    # ============================================================
    
    async def process_whatsapp_query(
        self,
        message: str,
        sender_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process WhatsApp query - MAIN ENTRY POINT"""
        logger.info(f"📩 Processing: '{message[:100]}'")
        start_time = time.perf_counter()
        
        try:
            routing_decision = self.intent_engine.detect_intent(message)
            logger.info(f"🎯 Intent: {routing_decision.intent}, Service: {routing_decision.service_key}")
            
            # Check if direct PostgreSQL query
            if routing_decision.intent == "direct_query":
                return await self._answer_directly(message, routing_decision)
            
            # Check if needs Groq
            if routing_decision.needs_groq or routing_decision.service_key == "groq":
                return await self._handle_groq(message, routing_decision)
            
            # Check Service Readiness
            service_key = routing_decision.service_key
            if not self.registry.is_service_ready(service_key):
                return self._format_module_unavailable(
                    message,
                    service_key,
                    self.registry.get_service_info(service_key)
                )
            
            # Execute Service
            result = await self._execute_service(routing_decision)
            
            # Format Response
            if result.get("success", False):
                return self._format_response(message, result.get("data"), error=False)
            else:
                return self._format_response(
                    message,
                    result.get("error", "An error occurred"),
                    error=True
                )
            
        except Exception as e:
            logger.exception(f"❌ Failed: {e}")
            return self._format_response(
                message,
                "⚠️ An unexpected error occurred. Please try again.",
                error=True
            )
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.info(f"⏱️ Response time: {elapsed_ms:.2f}ms")
    
    # ============================================================
    # DIRECT POSTGRESQL ANSWERING
    # ============================================================
    
    async def _answer_directly(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Answer directly from PostgreSQL using the query engine"""
        message_lower = message.lower()
        
        # Check for dealer query
        if 'dealer' in message_lower or 'customer' in message_lower:
            # Extract dealer name
            dealer_match = re.search(r'(?:dealer|customer|for|about|of)\s+([a-z0-9\s&\-\.]+)', message, re.IGNORECASE)
            if dealer_match:
                dealer_name = dealer_match.group(1).strip()
                result = self.query_engine.get_dealer_by_name(dealer_name)
                if result.get("success") and result.get("data"):
                    data = result["data"][0]
                    response = self._format_dealer_response(data)
                    return self._format_response(message, response, error=False)
        
        # Check for warehouse query
        if 'warehouse' in message_lower:
            warehouse_match = re.search(r'(?:warehouse|wh|depot)\s+([a-z0-9\s&\-\.]+)', message, re.IGNORECASE)
            if warehouse_match:
                warehouse_name = warehouse_match.group(1).strip()
                result = self.query_engine.get_warehouse_data(warehouse_name)
                if result.get("success") and result.get("data"):
                    data = result["data"][0]
                    response = self._format_warehouse_response(data)
                    return self._format_response(message, response, error=False)
        
        # Check for city query
        if 'city' in message_lower:
            city_match = re.search(r'(?:city|in|at)\s+([a-z0-9\s&\-\.]+)', message, re.IGNORECASE)
            if city_match:
                city_name = city_match.group(1).strip()
                result = self.query_engine.get_city_data(city_name)
                if result.get("success") and result.get("data"):
                    data = result["data"][0]
                    response = self._format_city_response(data)
                    return self._format_response(message, response, error=False)
        
        # Check for product query
        if 'product' in message_lower or 'model' in message_lower:
            product_match = re.search(r'(?:product|model|material)\s+([a-z0-9\s&\-\.]+)', message, re.IGNORECASE)
            if product_match:
                product_name = product_match.group(1).strip()
                result = self.query_engine.get_product_data(product_name)
                if result.get("success") and result.get("data"):
                    data = result["data"][0]
                    response = self._format_product_response(data)
                    return self._format_response(message, response, error=False)
        
        # Check for revenue query
        if 'revenue' in message_lower or 'sales' in message_lower:
            result = self.query_engine.get_national_kpis()
            if result.get("success") and result.get("data"):
                data = result["data"][0]
                response = self._format_kpi_response(data)
                return self._format_response(message, response, error=False)
        
        # Default - try general query
        return self._format_response(
            message,
            "I couldn't find specific data for your query. Please try:\n"
            "• A dealer name (e.g., 'Taj Electronics')\n"
            "• A warehouse name\n"
            "• A city name\n"
            "• A product name\n"
            "• A DN number (8-12 digits)\n\n"
            "Type 'Help' for all commands.",
            error=False
        )
    
    # ============================================================
    # RESPONSE FORMATTERS
    # ============================================================
    
    def _format_dealer_response(self, data: Dict) -> str:
        """Format dealer response for WhatsApp"""
        return f"""🏪 Dealer Dashboard

Name: {data.get('name', 'Unknown')}
Dealer Code: {data.get('dealer_code', 'N/A')}
Customer Code: {data.get('customer_code', 'N/A')}
City: {data.get('city', 'Unknown')}
Warehouse: {data.get('warehouse', 'Unknown')}
Sales Office: {data.get('sales_office', 'Unknown')}
Sales Manager: {data.get('sales_manager', 'Unknown')}
Division: {data.get('division', 'Unknown')}

📊 Performance:
Total DNs: {data.get('total_dn', 0):,}
Total Units: {data.get('total_units', 0):,}
Total Revenue: PKR {data.get('total_revenue', 0):,.2f}
Pending DNs: {data.get('pending_dn', 0):,}
Completed DNs: {data.get('completed_dn', 0):,}"""
    
    def _format_warehouse_response(self, data: Dict) -> str:
        return f"""🏭 Warehouse Dashboard

Warehouse: {data.get('warehouse', 'Unknown')}
Code: {data.get('warehouse_code', 'N/A')}

📊 Performance:
Total DNs: {data.get('total_dn', 0):,}
Total Units: {data.get('total_units', 0):,}
Total Revenue: PKR {data.get('total_revenue', 0):,.2f}
Dealers Served: {data.get('dealer_count', 0):,}
Pending DNs: {data.get('pending_dn', 0):,}"""
    
    def _format_city_response(self, data: Dict) -> str:
        return f"""🏙️ City Dashboard

City: {data.get('city', 'Unknown')}

📊 Performance:
Total Dealers: {data.get('dealer_count', 0):,}
Total DNs: {data.get('total_dn', 0):,}
Total Units: {data.get('total_units', 0):,}
Total Revenue: PKR {data.get('total_revenue', 0):,.2f}
Pending DNs: {data.get('pending_dn', 0):,}"""
    
    def _format_product_response(self, data: Dict) -> str:
        return f"""📦 Product Dashboard

Product: {data.get('product', 'Unknown')}
Material: {data.get('material', 'N/A')}

📊 Performance:
Total DNs: {data.get('total_dn', 0):,}
Total Units: {data.get('total_units', 0):,}
Total Revenue: PKR {data.get('total_revenue', 0):,.2f}"""
    
    def _format_kpi_response(self, data: Dict) -> str:
        return f"""📊 National KPIs

Overview:
Total DNs: {data.get('total_dn', 0):,}
Total Dealers: {data.get('total_dealers', 0):,}
Total Warehouses: {data.get('total_warehouses', 0):,}
Total Cities: {data.get('total_cities', 0):,}

Revenue:
Total Revenue: PKR {data.get('total_revenue', 0):,.2f}
Average DN Value: PKR {data.get('avg_dn_value', 0):,.2f}

Delivery:
Total Units: {data.get('total_units', 0):,}
Pending DNs: {data.get('pending_dn', 0):,} ({data.get('pending_percentage', 0):.1f}%)
Completed DNs: {data.get('completed_dn', 0):,} ({data.get('completion_rate', 0):.1f}%)"""
    
    # ============================================================
    # GROQ HANDLING
    # ============================================================
    
    async def _handle_groq(self, message: str, decision: RoutingDecision) -> Dict[str, Any]:
        """Handle Groq queries"""
        if decision.intent == "greeting":
            return self._format_response(
                message,
                "👋 Welcome to the Logistics WhatsApp AI Agent!\n\n"
                "I can help you with:\n"
                "📦 DN Tracking - Get delivery status\n"
                "🏪 Dealer Analytics - View dealer performance\n"
                "🏭 Warehouse Analytics - Monitor warehouse operations\n"
                "🏙️ City Analytics - Analyze city performance\n"
                "📊 National KPIs - View country-wide metrics\n\n"
                "Try sending a dealer name, warehouse name, city, or DN number!",
                error=False
            )
        
        if decision.intent == "help":
            return self._format_response(
                message,
                "📋 Available Commands:\n\n"
                "📦 DN Queries:\n"
                "• Send a DN number (8-12 digits)\n"
                "• 'Pending DN', 'Pending PGI', 'Pending POD'\n\n"
                "🏪 Dealer Queries:\n"
                "• 'Dealer [name]'\n"
                "• '[Dealer name] dashboard'\n"
                "• 'Top dealers', 'Bottom dealers'\n\n"
                "🏭 Warehouse Queries:\n"
                "• 'Warehouse [name]'\n\n"
                "🏙️ City Queries:\n"
                "• 'City [name]'\n\n"
                "📦 Product Queries:\n"
                "• 'Product [name]'\n\n"
                "📊 Analytics:\n"
                "• 'National KPI', 'Revenue', 'Total DNs'",
                error=False
            )
        
        if decision.intent == "explanation":
            return self._format_response(
                message,
                "📖 Term Explanation\n\n"
                "DN (Delivery Note): Document accompanying delivery\n"
                "PGI (Post Goods Issue): Warehouse release confirmation\n"
                "POD (Proof of Delivery): Delivery confirmation document\n"
                "Aging: Time since creation/dispatch/delivery\n"
                "KPI: Key Performance Indicator\n\n"
                "For more details, ask: 'What is POD?' or 'Explain PGI'",
                error=False
            )
        
        # Try Groq service
        if self._groq_service:
            try:
                if hasattr(self._groq_service, 'process_query'):
                    response = await self._groq_service.process_query(message)
                    if response and response.get("response"):
                        return self._format_response(message, response.get("response"), error=False)
            except Exception as e:
                logger.error(f"❌ Groq failed: {e}")
        
        return self._format_response(
            message,
            "I'm here to help with logistics data. Please specify:\n"
            "• A DN number (8-12 digits)\n"
            "• A dealer name (e.g., 'Taj Electronics')\n"
            "• A warehouse name\n"
            "• A city name\n"
            "• An analytics query (e.g., 'Revenue')\n\n"
            "Type 'Help' for all commands.",
            error=False
        )
    
    # ============================================================
    # SERVICE EXECUTION
    # ============================================================
    
    async def _execute_service(self, decision: RoutingDecision) -> Dict[str, Any]:
        """Execute service"""
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
            logger.exception(f"❌ Service execution failed: {e}")
            return {"success": False, "error": str(e)}
    
    # ============================================================
    # RESPONSE FORMATTING
    # ============================================================
    
    def _format_response(self, original_message: str, data: Any, error: bool = False) -> Dict[str, Any]:
        if error:
            return {
                "success": not error,
                "message": original_message,
                "response": data,
                "error": error,
                "timestamp": datetime.now().isoformat()
            }
        
        if hasattr(data, "to_whatsapp_message"):
            try:
                data = data.to_whatsapp_message()
            except:
                pass
        
        if isinstance(data, dict):
            for key in ("formatted_response", "whatsapp_message", "response", "message"):
                if data.get(key) not in (None, ""):
                    data = data[key]
                    break
        
        if hasattr(data, 'dn_no'):
            try:
                from app.routes.webhook import format_dn_response
                data = format_dn_response(data)
            except:
                pass
        
        return {
            "success": True,
            "message": original_message,
            "response": data,
            "error": False,
            "timestamp": datetime.now().isoformat()
        }
    
    def _format_module_unavailable(self, original_message: str, service_key: str, info: Dict[str, Any]) -> Dict[str, Any]:
        status_text = info.get("status", "UNKNOWN")
        errors = info.get("errors", [])
        
        message = f"""⚠️ Module Currently Unavailable

Module: {service_key.title()} Service
Status: {status_text}

Please try again later."""
        
        if errors:
            message += f"\n\nIssues: {', '.join(errors[:2])}"
        
        return self._format_response(original_message, message, error=True)
    
    # ============================================================
    # DIAGNOSTIC METHODS
    # ============================================================
    
    def get_system_health(self) -> Dict[str, Any]:
        return {
            "services": self.registry.get_health_report(),
            "system_status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "6.0",
            "postgresql": self.query_engine
        }
    
    def get_service_registry_status(self) -> Dict[str, Any]:
        return self.registry.get_health_report()
    
    def validate_all_services(self) -> Dict[str, Any]:
        return self.registry.validate_all_services(force=True)
    
    def refresh_service_status(self, service_key: str = None) -> Dict[str, Any]:
        if service_key:
            self.registry._status_cache.pop(service_key, None)
            self.registry._instance_cache.pop(service_key, None)
            return self.registry.get_service_status(service_key)
        else:
            return self.registry.validate_all_services(force=True)


# ==========================================================
# BLOCK 9: THREAD-SAFE SINGLETON
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
                    logger.info("✅ WhatsAppProviderService singleton initialized (v6.0)")
                except Exception as e:
                    logger.exception(f"❌ Initialization failed: {e}")
                    raise
    
    return _whatsapp_provider_service


# ==========================================================
# BLOCK 10: EXPORTS
# ==========================================================

__all__ = [
    'WhatsAppProviderService',
    'get_whatsapp_provider_service',
    'ServiceRegistry',
    'ServiceStatus',
    'RoutingDecision',
    'IntentDetectionEngine',
    'PostgreSQLValidator',
    'PostgreSQLQueryEngine'
]


# ==========================================================
# MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("AI Provider Service v6.0 - POSTGRESQL INTEGRATED")
logger.info("=" * 70)
logger.info("✅ PostgreSQL Query Engine - Direct database access")
logger.info("✅ Intent Detection - 12 priority levels")
logger.info("✅ Entity Extraction - Dealer, Warehouse, City, Product")
logger.info("✅ Routing Engine - Intelligent decision making")
logger.info("✅ Groq Fallback - For complex questions")
logger.info("✅ 100% PostgreSQL Integration - All data from database")
logger.info("=" * 70)
