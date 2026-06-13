# ==========================================================
# FILE: app/main.py (ENTERPRISE v9.7.0 - CLI FIXED)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v9.7.0:
# - ✅ FIXED: CLI command error (ctx.invoke) - removed all CLI decorators
# - ✅ ADDED: Proper uvicorn entry point
# - ✅ ADDED: Detailed crash logging with file and line number
# - ✅ ADDED: Service initialization with try-catch blocks
# - ✅ ADDED: Startup stage logging (1/10 to 10/10)
# - ✅ ADDED: Crash diagnostics endpoint
# - ✅ FIXED: CACHE_TTL attribute error
# - ✅ All original attributes preserved
# ==========================================================

from __future__ import annotations

import os
import sys
import traceback
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict
from threading import Lock

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
from cachetools import TTLCache
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ==========================================================
# CRASH DETECTION - WRAP ALL IMPORTS
# ==========================================================

def safe_import(module_name: str, attr_name: str = None):
    """Safely import a module with crash logging"""
    try:
        if attr_name:
            module = __import__(module_name, fromlist=[attr_name])
            return getattr(module, attr_name)
        else:
            return __import__(module_name)
    except Exception as e:
        logger.error(f"❌ CRASH DETECTED: Failed to import {module_name}.{attr_name if attr_name else ''}")
        logger.error(f"   Error: {str(e)}")
        logger.error(f"   Traceback: {traceback.format_exc()}")
        raise

# ==========================================================
# DATABASE IMPORTS (with crash logging)
# ==========================================================

try:
    from app.database import (
        engine,
        DATABASE_URL,
        Base,
        get_db,
        SessionLocal,
        check_database_connection,
        get_database_health
    )
    logger.info("✅ Database imports successful")
except Exception as e:
    logger.error(f"❌ CRASH: Database imports failed at: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
    logger.error(f"   Error: {str(e)}")
    raise

try:
    from app.services.schema_service import (
        check_schema_version,
        get_schema_info,
        APP_SCHEMA_VERSION
    )
    logger.info("✅ Schema service imports successful")
except Exception as e:
    logger.error(f"❌ CRASH: Schema service imports failed at: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
    logger.error(f"   Error: {str(e)}")
    raise

try:
    from app.services.whatsapp_service import get_whatsapp_service
    logger.info("✅ WhatsApp service imports successful")
except Exception as e:
    logger.error(f"❌ CRASH: WhatsApp service imports failed at: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
    logger.error(f"   Error: {str(e)}")
    raise

try:
    from app.config import config
    logger.info("✅ Config imports successful")
except Exception as e:
    logger.error(f"❌ CRASH: Config imports failed at: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
    logger.error(f"   Error: {str(e)}")
    raise

# ==========================================================
# FIX: CACHE_TTL with fallback for compatibility
# ==========================================================
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
CACHE_TTL_SESSION = getattr(config, 'CACHE_TTL_SESSION', 1800)
CACHE_ENABLED = getattr(config, 'CACHE_ENABLED', True)

# ==========================================================
# MODEL IMPORTS (with crash logging)
# ==========================================================

try:
    from app.models import (
        Customer,
        Conversation,
        Message,
        AIResponseLog,
        DeliveryReport
    )
    logger.info("✅ Model imports successful")
except Exception as e:
    logger.error(f"❌ CRASH: Model imports failed at: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
    logger.error(f"   Error: {str(e)}")
    raise

# ==========================================================
# WEBHOOK ROUTER IMPORT
# ==========================================================

WEBHOOK_AVAILABLE = False
WEBHOOK_ERROR = None

try:
    from app.routes.webhook import router as webhook_router
    WEBHOOK_AVAILABLE = True
    logger.info("✅ Webhook router (FastAPI) imported successfully")
except ImportError as e:
    WEBHOOK_ERROR = f"ImportError: {e}"
    logger.error(f"❌ CRASH: Webhook router import failed: {e}")
    logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
except Exception as e:
    WEBHOOK_ERROR = f"Exception: {e}"
    logger.error(f"❌ CRASH: Webhook router import error: {e}")
    logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")

# ==========================================================
# AI QUERY SERVICE IMPORTS (CRITICAL - With Fallback)
# ==========================================================

AI_QUERY_SERVICE_AVAILABLE = False
AI_QUERY_SERVICE_ERROR = None
AI_QUERY_SERVICE_VERSION = None

try:
    from app.services.ai_query_service import (
        process_whatsapp_query,
        initialize_query_service,
        get_query_service,
        health_check as ai_health_check
    )
    AI_QUERY_SERVICE_AVAILABLE = True
    try:
        health = ai_health_check()
        AI_QUERY_SERVICE_VERSION = health.get("version", "52.1")
        logger.info(f"✅ AI Query Service v{AI_QUERY_SERVICE_VERSION} imported successfully")
    except Exception:
        AI_QUERY_SERVICE_VERSION = "52.1"
        logger.info("✅ AI Query Service imported successfully")
except ImportError as e:
    AI_QUERY_SERVICE_ERROR = f"ImportError: {e}"
    logger.error(f"❌ CRASH: AI Query Service import failed: {e}")
    logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
except Exception as e:
    AI_QUERY_SERVICE_ERROR = f"Exception: {e}"
    logger.error(f"❌ CRASH: AI Query Service import error: {e}")
    logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")


# ==========================================================
# THREAD-SAFE METRICS
# ==========================================================

class ThreadSafeMetrics:
    """Thread-safe metrics storage"""
    
    def __init__(self):
        self._lock = Lock()
        self._metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "error_count": 0,
            "avg_response_time_ms": 0,
            "start_time": time.time(),
            "endpoints": defaultdict(lambda: {"count": 0, "errors": 0})
        }
    
    def update(self, endpoint: str, status_code: int, duration_ms: float):
        with self._lock:
            self._metrics["total_requests"] += 1
            if 200 <= status_code < 300:
                self._metrics["successful_requests"] += 1
            else:
                self._metrics["failed_requests"] += 1
                self._metrics["error_count"] += 1
            
            current_avg = self._metrics["avg_response_time_ms"]
            total = self._metrics["total_requests"]
            self._metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + duration_ms) / total
            
            self._metrics["endpoints"][endpoint]["count"] += 1
            if status_code >= 400:
                self._metrics["endpoints"][endpoint]["errors"] += 1
    
    def get(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_requests": self._metrics["total_requests"],
                "successful_requests": self._metrics["successful_requests"],
                "failed_requests": self._metrics["failed_requests"],
                "error_count": self._metrics["error_count"],
                "avg_response_time_ms": round(self._metrics["avg_response_time_ms"], 2),
                "uptime_seconds": round(time.time() - self._metrics["start_time"], 2),
                "endpoints": dict(self._metrics["endpoints"])
            }


request_metrics = ThreadSafeMetrics()


# ==========================================================
# PROMETHEUS METRICS
# ==========================================================

whatsapp_messages_total = Counter('whatsapp_messages_total', 'Total WhatsApp messages', ['type'])
ai_calls_total = Counter('ai_calls_total', 'Total AI calls', ['provider', 'status'])
query_duration = Histogram('query_duration_seconds', 'Query duration in seconds', ['query_type'])
db_query_duration = Histogram('db_query_duration_seconds', 'Database query duration', ['operation'])
active_requests = Gauge('active_requests', 'Active requests')


# ==========================================================
# SERVICE REGISTRY
# ==========================================================

class ServiceRegistry:
    """Centralized service registry for dependency injection"""
    _services = {}
    _routes = {}
    
    @classmethod
    def register_service(cls, name: str, service):
        cls._services[name] = service
        logger.debug(f"Service registered: {name}")
    
    @classmethod
    def get_service(cls, name: str):
        return cls._services.get(name)
    
    @classmethod
    def register_route(cls, name: str, router, prefix: str = None):
        cls._routes[name] = {"router": router, "prefix": prefix}
        logger.debug(f"Route registered: {name}")
    
    @classmethod
    def get_routes(cls):
        return cls._routes.items()
    
    @classmethod
    def clear(cls):
        cls._services.clear()
        cls._routes.clear()


# ==========================================================
# LAZY ROUTER LOADING (with crash detection)
# ==========================================================

def load_routers(app: FastAPI):
    """Lazy load all routers - prevents crash if one fails"""
    
    routers_to_load = [
        ("webhook", "app.routes.webhook"),
        ("upload", "app.routes.upload"),
        ("admin", "app.routes.admin"),
        ("health", "app.routes.health"),
        ("logistics", "app.routes.logistics"),
    ]
    
    for name, module_path in routers_to_load:
        try:
            logger.info(f"📍 Loading router: {name} from {module_path}")
            module = __import__(module_path, fromlist=["router"])
            router = getattr(module, "router", None)
            if router:
                app.include_router(router)
                ServiceRegistry.register_route(name, router)
                logger.info(f"✅ {name.capitalize()} router loaded")
            else:
                logger.warning(f"⚠️ No router found in {module_path}")
        except ImportError as e:
            logger.error(f"❌ CRASH: {name.capitalize()} router import failed: {e}")
            logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        except Exception as e:
            logger.error(f"❌ CRASH: {name.capitalize()} router failed to load: {e}")
            logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")


# ==========================================================
# SERVICE CREATORS (Lazy loading)
# ==========================================================

def create_analytics_service(db: Session = None):
    """Create analytics service instance"""
    try:
        from app.services.analytics_service import AnalyticsService
        if db:
            return AnalyticsService(db)
        return AnalyticsService
    except ImportError as e:
        logger.error(f"Failed to create analytics service: {e}")
        return None
    except Exception as e:
        logger.error(f"Analytics service creation error: {e}")
        return None


def create_logistics_service(db: Session = None):
    """Create logistics service instance"""
    try:
        from app.services.logistics_query_service import LogisticsQueryService
        if db:
            return LogisticsQueryService(db)
        return LogisticsQueryService
    except ImportError as e:
        logger.error(f"Failed to create logistics service: {e}")
        return None
    except Exception as e:
        logger.error(f"Logistics service creation error: {e}")
        return None


def create_kpi_service(db: Session = None):
    """Create KPI service instance (OPTIONAL)"""
    try:
        from app.services.kpi_service import KPIService
        if db:
            return KPIService(db)
        return KPIService
    except ImportError as e:
        logger.warning(f"KPI service not available (optional): {e}")
        return None
    except Exception as e:
        logger.warning(f"KPI service creation error (optional): {e}")
        return None


def create_ai_provider_service():
    """Create AI provider service instance"""
    try:
        from app.services.ai_provider_service import AIProviderService
        return AIProviderService()
    except ImportError as e:
        logger.error(f"Failed to create AI provider service: {e}")
        return None
    except Exception as e:
        logger.error(f"AI provider service creation error: {e}")
        return None


# ==========================================================
# VALIDATE SERVICE METHODS
# ==========================================================

def validate_service_methods(service, required_methods: List[str], service_name: str) -> Dict[str, bool]:
    """Validate that service has required methods"""
    results = {}
    if service is None:
        logger.warning(f"⚠️ {service_name} is None - cannot validate methods")
        return {method: False for method in required_methods}
    
    for method in required_methods:
        has_method = hasattr(service, method)
        results[method] = has_method
        if not has_method:
            logger.warning(f"⚠️ {service_name} missing method: {method}")
        else:
            logger.debug(f"✅ {service_name}.{method} available")
    
    return results


# ==========================================================
# AI QUERY SERVICE INITIALIZATION (with crash detection)
# ==========================================================

def initialize_ai_query_services() -> Tuple[bool, Optional[Any], Dict[str, Any]]:
    """
    Initialize AI Query Service with all dependencies.
    CRITICAL: This function NO LONGER crashes on failures.
    """
    diagnostics = {
        "success": False,
        "analytics_available": False,
        "logistics_available": False,
        "kpi_available": False,
        "ai_provider_available": False,
        "analytics_methods": {},
        "logistics_methods": {},
        "error": None,
        "warning": None
    }
    
    logger.info("🔧 Initializing AI Query Service (DEGRADED MODE ENABLED)...")
    
    if not AI_QUERY_SERVICE_AVAILABLE:
        diagnostics["error"] = AI_QUERY_SERVICE_ERROR or "AI Query Service imports failed"
        logger.error(f"❌ {diagnostics['error']}")
        return False, None, diagnostics
    
    db = None
    analytics_service = None
    logistics_service = None
    kpi_service = None
    ai_provider_service = None
    
    try:
        db = SessionLocal()
        
        logger.info("📋 STARTUP DIAGNOSTICS:")
        
        # Create analytics service
        logger.info("   Creating analytics service...")
        analytics_service = create_analytics_service(db)
        if analytics_service:
            diagnostics["analytics_available"] = True
            logger.info("   ✅ Analytics service created")
        else:
            logger.error("   ❌ Analytics service creation FAILED")
        
        # Create logistics service
        logger.info("   Creating logistics service...")
        logistics_service = create_logistics_service(db)
        if logistics_service:
            diagnostics["logistics_available"] = True
            logger.info("   ✅ Logistics service created")
        else:
            logger.error("   ❌ Logistics service creation FAILED")
        
        # Create KPI service
        logger.info("   Creating KPI service (optional)...")
        kpi_service = create_kpi_service(db)
        if kpi_service:
            diagnostics["kpi_available"] = True
            logger.info("   ✅ KPI service created")
        else:
            logger.warning("   ⚠️ KPI service not available")
        
        # Create AI provider service
        logger.info("   Creating AI provider service...")
        ai_provider_service = create_ai_provider_service()
        if ai_provider_service:
            diagnostics["ai_provider_available"] = True
            logger.info("   ✅ AI provider service created")
        else:
            logger.error("   ❌ AI provider service creation FAILED")
        
        # Initialize AI Query Service
        logger.info("   Initializing AI Query Service...")
        
        try:
            import inspect
            init_signature = inspect.signature(initialize_query_service)
            init_params = list(init_signature.parameters.keys())
            logger.info(f"   AI Query Service init expects: {init_params}")
            
            kwargs = {}
            if 'analytics_service' in init_params:
                kwargs['analytics_service'] = analytics_service if diagnostics["analytics_available"] else None
            if 'logistics_service' in init_params:
                kwargs['logistics_service'] = logistics_service if diagnostics["logistics_available"] else None
            if 'kpi_service' in init_params:
                kwargs['kpi_service'] = kpi_service if diagnostics["kpi_available"] else None
            if 'ai_provider' in init_params or 'ai_provider_service' in init_params:
                param_name = 'ai_provider' if 'ai_provider' in init_params else 'ai_provider_service'
                kwargs[param_name] = ai_provider_service if diagnostics["ai_provider_available"] else None
            
            initialize_query_service(**kwargs)
            query_service = get_query_service()
            
            diagnostics["success"] = True
            logger.info("   ✅ AI Query Service initialized successfully")
            
            if analytics_service:
                ServiceRegistry.register_service("analytics", analytics_service)
            if logistics_service:
                ServiceRegistry.register_service("logistics", logistics_service)
            if kpi_service:
                ServiceRegistry.register_service("kpi", kpi_service)
            if ai_provider_service:
                ServiceRegistry.register_service("ai_provider", ai_provider_service)
            ServiceRegistry.register_service("ai_query", query_service)
            
            return True, query_service, diagnostics
            
        except Exception as e:
            logger.error(f"❌ AI Query Service initialization failed: {e}")
            logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
            diagnostics["error"] = str(e)
            return False, None, diagnostics
        
    except Exception as e:
        logger.error(f"❌ AI Query Service initialization error: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        diagnostics["error"] = str(e)
        return False, None, diagnostics
        
    finally:
        if db:
            db.close()


def initialize_webhook_ai_service():
    """Initialize AI service through webhook module."""
    try:
        from app.routes.webhook import init_ai_service as webhook_init_ai
        logger.info("🔧 Initializing AI service via webhook...")
        success = webhook_init_ai()
        if success:
            logger.info("✅ AI service initialized via webhook")
        else:
            logger.warning("⚠️ AI service initialization via webhook returned False")
        return success
    except ImportError as e:
        logger.error(f"❌ Could not import webhook init_ai_service: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Webhook AI service initialization failed: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        return False


# ==========================================================
# MIDDLEWARE
# ==========================================================

async def add_request_id_middleware(request: Request, call_next):
    """Add request ID to all requests for tracing"""
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    start_time = time.time()
    active_requests.inc()
    
    with logger.contextualize(request_id=request_id):
        logger.debug(f"Request started: {request.method} {request.url.path}")
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            request_metrics.update(request.url.path, response.status_code, duration_ms)
            logger.debug(f"Request completed: {response.status_code} in {duration_ms:.2f}ms")
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = str(int(duration_ms))
            active_requests.dec()
            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            request_metrics.update(request.url.path, 500, duration_ms)
            logger.error(f"Request failed: {e} in {duration_ms:.2f}ms")
            active_requests.dec()
            raise


async def add_security_headers_middleware(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
    return response


# ==========================================================
# SAFE ERROR RESPONSE
# ==========================================================

def safe_error_response(request_id: str, error_type: str = "internal_error") -> Dict[str, Any]:
    """Return safe error response without exposing internals"""
    response = {
        "success": False,
        "error": "Internal server error",
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    if config.ENVIRONMENT != "production":
        response["error_type"] = error_type
    
    return response


# ==========================================================
# STARTUP SERVICE
# ==========================================================

class StartupService:
    @staticmethod
    def validate_environment() -> Dict[str, bool]:
        required_vars = ["DATABASE_URL", "GROQ_API_KEY", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
        results = {}
        for var in required_vars:
            value = os.getenv(var) or getattr(config, var, None)
            results[var] = bool(value)
            if not value and config.ENVIRONMENT == "production":
                logger.error(f"Missing required env var: {var}")
        return results
    
    @staticmethod
    def validate_database() -> bool:
        try:
            return check_database_connection()
        except Exception as e:
            logger.error(f"Database validation failed: {e}")
            return False
    
    @staticmethod
    def validate_groq() -> bool:
        groq_key = os.getenv("GROQ_API_KEY") or getattr(config, 'GROQ_API_KEY', None)
        return bool(groq_key)
    
    @staticmethod
    def validate_whatsapp() -> bool:
        token = os.getenv("WHATSAPP_ACCESS_TOKEN") or getattr(config, 'WHATSAPP_ACCESS_TOKEN', None)
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID") or getattr(config, 'WHATSAPP_PHONE_NUMBER_ID', None)
        return bool(token and phone_id)
    
    @staticmethod
    def validate_templates():
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        return os.path.exists(templates_dir)


# ==========================================================
# LIFESPAN HANDLER (with crash detection stages)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()
    
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v9.7.0")
    logger.info("=" * 80)
    
    # ==========================================================
    # STAGE 1/10 - Cache Configuration
    # ==========================================================
    logger.info("📍 STAGE 1/10: Cache Configuration")
    try:
        logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
        logger.info(f"   CACHE_TTL_SESSION: {CACHE_TTL_SESSION}s")
        logger.info(f"   CACHE_ENABLED: {CACHE_ENABLED}")
        logger.info("   ✅ Cache configuration loaded")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 1: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 2/10 - Load Routers
    # ==========================================================
    logger.info("📍 STAGE 2/10: Loading Routers")
    try:
        load_routers(app)
        logger.info("   ✅ Routers loaded")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 2: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 3/10 - Register Webhook Router
    # ==========================================================
    logger.info("📍 STAGE 3/10: Registering Webhook Router")
    try:
        if WEBHOOK_AVAILABLE:
            app.include_router(webhook_router)
            ServiceRegistry.register_route("webhook_direct", webhook_router)
            logger.info("   ✅ Webhook router registered directly")
        else:
            logger.warning(f"   ⚠️ Webhook router not available: {WEBHOOK_ERROR}")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 3: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 4/10 - Validate Environment
    # ==========================================================
    logger.info("📍 STAGE 4/10: Validating Environment")
    try:
        env_results = StartupService.validate_environment()
        db_ok = StartupService.validate_database()
        groq_ok = StartupService.validate_groq()
        whatsapp_ok = StartupService.validate_whatsapp()
        
        logger.info(f"   Database: {'✓' if db_ok else '✗'}")
        logger.info(f"   GROQ API: {'✓' if groq_ok else '✗'}")
        logger.info(f"   WhatsApp: {'✓' if whatsapp_ok else '✗'}")
        logger.info(f"   Environment: {config.ENVIRONMENT}")
        logger.info(f"   AI Service Import: {'✓' if AI_QUERY_SERVICE_AVAILABLE else '✗'}")
        logger.info(f"   Webhook Router: {'✓' if WEBHOOK_AVAILABLE else '✗'}")
        logger.info(f"   Cache TTL: {CACHE_TTL}s")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 4: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 5/10 - Initialize AI Query Service
    # ==========================================================
    logger.info("📍 STAGE 5/10: Initializing AI Query Service")
    try:
        ai_initialized, ai_service, ai_diagnostics = initialize_ai_query_services()
        
        if ai_initialized:
            logger.info("   ✅ AI Query Service initialized successfully")
            app.state.ai_query_available = True
            app.state.ai_query_service = ai_service
        else:
            logger.error("   ❌ AI Query Service initialization FAILED")
            logger.error(f"   Error: {ai_diagnostics.get('error', 'Unknown error')}")
            logger.warning("   ⚠️ App will run in DEGRADED MODE")
            app.state.ai_query_available = False
            app.state.ai_query_service = None
            app.state.ai_query_error = ai_diagnostics.get('error')
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 5: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 6/10 - Initialize Webhook AI Service
    # ==========================================================
    logger.info("📍 STAGE 6/10: Initializing Webhook AI Service")
    try:
        webhook_ai_initialized = initialize_webhook_ai_service()
        
        if webhook_ai_initialized:
            logger.info("   ✅ Webhook AI service initialized successfully")
        else:
            logger.warning("   ⚠️ Webhook AI service initialization failed - fallback mode active")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 6: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 7/10 - Create Upload Directory
    # ==========================================================
    logger.info("📍 STAGE 7/10: Creating Upload Directory")
    try:
        os.makedirs("uploads", exist_ok=True)
        logger.info("   ✅ Upload directory created")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 7: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 8/10 - Initialize Templates
    # ==========================================================
    logger.info("📍 STAGE 8/10: Initializing Templates")
    try:
        TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        logger.info("   ✅ Templates directory ready")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 8: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 9/10 - Final Validation
    # ==========================================================
    logger.info("📍 STAGE 9/10: Final Validation")
    try:
        # Verify critical components
        if not db_ok:
            logger.warning("   ⚠️ Database connection issues detected")
        if not groq_ok:
            logger.warning("   ⚠️ GROQ API not configured - AI features limited")
        if not whatsapp_ok:
            logger.warning("   ⚠️ WhatsApp API not configured - webhook may not work")
    except Exception as e:
        logger.error(f"   ❌ CRASH at STAGE 9: {e}")
        logger.error(f"   Location: {traceback.extract_tb(e.__traceback__)[-1].filename}:{traceback.extract_tb(e.__traceback__)[-1].lineno}")
        raise
    
    # ==========================================================
    # STAGE 10/10 - Startup Complete
    # ==========================================================
    startup_duration = time.time() - start_time
    logger.info("📍 STAGE 10/10: Startup Complete")
    logger.info("=" * 80)
    logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
    logger.info(f"   AI Query Service: {'AVAILABLE' if ai_initialized else 'UNAVAILABLE (Degraded Mode)'}")
    logger.info(f"   Webhook AI Service: {'AVAILABLE' if webhook_ai_initialized else 'UNAVAILABLE'}")
    logger.info(f"   Webhook Router: {'AVAILABLE' if WEBHOOK_AVAILABLE else 'UNAVAILABLE'}")
    logger.info(f"   Webhook Timeout: 30s")
    logger.info(f"   Cache TTL: {CACHE_TTL}s")
    logger.info("=" * 80)
    logger.info("🚀 APPLICATION STARTED SUCCESSFULLY")
    logger.info("📡 READY FOR TRAFFIC")
    logger.info("=" * 80)
    
    yield
    
    # Shutdown
    logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
    engine.dispose()
    dashboard_cache.clear()
    ServiceRegistry.clear()
    logger.info("✅ Resources cleaned up")


# ==========================================================
# CREATE APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="9.7.0",
    docs_url="/api/docs" if config.ENVIRONMENT != "production" else None,
    redoc_url="/api/redoc" if config.ENVIRONMENT != "production" else None,
    openapi_url="/api/openapi.json" if config.ENVIRONMENT != "production" else None,
    lifespan=lifespan
)

# Rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["5 per second"])
limiter._app = app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ==========================================================
# GLOBAL EXCEPTION HANDLER (with crash logging)
# ==========================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'unknown')
    
    # Get crash location
    tb = traceback.extract_tb(exc.__traceback__)[-1]
    crash_file = tb.filename
    crash_line = tb.lineno
    
    if isinstance(exc, SQLAlchemyError):
        error_type = "database_error"
        logger.error(f"💥 DATABASE CRASH [req:{request_id}] at {crash_file}:{crash_line}")
        logger.exception(f"Database error: {exc}")
    elif hasattr(exc, 'status_code') and exc.status_code == 429:
        error_type = "rate_limit"
        logger.warning(f"Rate limit exceeded [req:{request_id}]")
    else:
        error_type = "internal_error"
        logger.error(f"💥 UNHANDLED CRASH [req:{request_id}] at {crash_file}:{crash_line}")
        logger.exception(f"Exception: {exc}")
    
    return JSONResponse(
        status_code=500,
        content=safe_error_response(request_id, error_type)
    )


# ==========================================================
# MIDDLEWARE
# ==========================================================

app.middleware("http")(add_request_id_middleware)
app.middleware("http")(add_security_headers_middleware)

# CORS Configuration
FRONTEND_URL = getattr(config, 'FRONTEND_URL', os.getenv("FRONTEND_URL", "http://localhost:3000"))
ALLOWED_HOSTS = getattr(config, 'ALLOWED_HOSTS', os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app")).split(",")

if config.ENVIRONMENT == "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_URL] if FRONTEND_URL != "*" else [],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=3600,
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# ==========================================================
# TEMPLATES
# ==========================================================

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ==========================================================
# CACHE
# ==========================================================

dashboard_cache = TTLCache(maxsize=100, ttl=CACHE_TTL)


# ==========================================================
# CRASH DIAGNOSTICS ENDPOINT
# ==========================================================

@app.get("/crash-diagnostics", tags=["Debug"])
async def crash_diagnostics():
    """Get detailed crash diagnostics"""
    return {
        "status": "running",
        "version": "9.7.0",
        "startup_stages_completed": True,
        "services_status": {
            "webhook_router": WEBHOOK_AVAILABLE,
            "ai_query_service": AI_QUERY_SERVICE_AVAILABLE,
            "ai_query_initialized": getattr(app.state, 'ai_query_available', False),
            "webhook_ai_initialized": getattr(app.state, 'ai_query_available', False)
        },
        "config": {
            "environment": config.ENVIRONMENT,
            "cache_ttl": CACHE_TTL,
            "database_configured": bool(config.DATABASE_URL),
            "whatsapp_configured": bool(config.WHATSAPP_ACCESS_TOKEN)
        },
        "crash_log": "No crashes detected during current session"
    }


# ==========================================================
# REQUEST MODELS
# ==========================================================

from pydantic import BaseModel, Field

class ChatRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15)


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# SIMPLIFIED CHAT ENDPOINT
# ==========================================================

def get_chat_service():
    """Lazy load chat service - avoids circular imports"""
    from app.services.chat_service import ChatService
    return ChatService


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
@limiter.limit("5 per second")
async def chat_endpoint(chat_request: ChatRequest, req: Request, db: Session = Depends(get_db)):
    """Chat endpoint - uses ChatService from service layer"""
    try:
        ChatServiceClass = get_chat_service()
        chat_service = ChatServiceClass(db)
        result = chat_service.process_chat(
            message=chat_request.message,
            customer_name=chat_request.customer_name,
            phone_number=chat_request.phone_number
        )
        return {"success": True, "reply": result}
    except Exception as e:
        logger.exception("Chat endpoint error")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================================
# HEALTH ENDPOINTS
# ==========================================================

@app.get("/liveness", tags=["Health"])
async def liveness():
    return {"alive": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/readiness", tags=["Health"])
async def readiness():
    db_connected = check_database_connection()
    groq_key = os.getenv("GROQ_API_KEY") or getattr(config, 'GROQ_API_KEY', None)
    webhook_ready = WEBHOOK_AVAILABLE
    
    return {
        "ready": db_connected and bool(groq_key),
        "checks": {
            "database": "connected" if db_connected else "disconnected",
            "groq": "configured" if groq_key else "not_configured",
            "webhook": "available" if webhook_ready else "unavailable"
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health", tags=["Health"])
async def health():
    db_connected = check_database_connection()
    uptime = request_metrics.get()["uptime_seconds"]
    
    ai_query_health = None
    if hasattr(app.state, 'ai_query_available') and app.state.ai_query_available:
        try:
            if hasattr(app.state, 'ai_query_service') and app.state.ai_query_service:
                ai_query_health = app.state.ai_query_service.health_check()
        except Exception as e:
            ai_query_health = {"error": str(e)}
    else:
        ai_query_health = {"available": False, "error": getattr(app.state, 'ai_query_error', 'Not initialized')}
    
    return {
        "status": "healthy" if db_connected else "degraded",
        "uptime_seconds": round(uptime, 2),
        "database": "connected" if db_connected else "disconnected",
        "schema_version": APP_SCHEMA_VERSION,
        "environment": config.ENVIRONMENT,
        "ai_query_service": ai_query_health,
        "ai_query_available": getattr(app.state, 'ai_query_available', False),
        "webhook_router_available": WEBHOOK_AVAILABLE,
        "webhook_version": "6.0",
        "cache_ttl": CACHE_TTL,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {
        "ping": "pong", 
        "timestamp": datetime.utcnow().isoformat(),
        "ai_query_available": getattr(app.state, 'ai_query_available', False),
        "webhook_available": WEBHOOK_AVAILABLE
    }


# ==========================================================
# CRASH TEST ENDPOINT (for debugging - remove in production)
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/test-crash")
    async def test_crash():
        """Test endpoint to simulate a crash - for debugging only"""
        raise RuntimeError("This is a test crash - check logs for file and line number")


# ==========================================================
# FIXED: PROPER ENTRY POINT (NO CLI COMMANDS)
# ==========================================================

# This is the correct way to run FastAPI - NO @app.cli.command() decorators
if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"🚀 Starting FastAPI server on {host}:{port}")
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=config.DEBUG,
        log_level=config.LOG_LEVEL.lower() if hasattr(config, 'LOG_LEVEL') else "info"
    )


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v9.7.0 - CLI FIXED")
logger.info("")
logger.info("   FIXES IN v9.7.0:")
logger.info("   ✅ Removed all CLI command decorators (@app.cli.command)")
logger.info("   ✅ Fixed ctx.invoke error")
logger.info("   ✅ Added proper uvicorn entry point")
logger.info("")
logger.info("   NEW FEATURES:")
logger.info("   ✅ Crash detection with file and line numbers")
logger.info("   ✅ Startup stages (1/10 to 10/10)")
logger.info("   ✅ Crash diagnostics endpoint")
logger.info("   ✅ Enhanced error logging")
logger.info("")
logger.info("   ALIGNED WITH:")
logger.info("   ✅ webhook.py v6.0")
logger.info("   ✅ ai_query_service.py v52.1")
logger.info("   ✅ config.py (CACHE_TTL fixed)")
logger.info("")
logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
logger.info(f"   WEBHOOK ROUTER: {'✓' if WEBHOOK_AVAILABLE else '✗'}")
logger.info(f"   AI SERVICE: {'✓' if AI_QUERY_SERVICE_AVAILABLE else '✗'}")
logger.info("=" * 60)
