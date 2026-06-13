# ==========================================================
# FILE: app/main.py (ENTERPRISE v9.5.0 - FASTAPI WEBHOOK INTEGRATED)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v9.5.0:
# - ✅ FIXED: FastAPI webhook router integration (no Flask conflict)
# - ✅ FIXED: CACHE_TTL attribute error (now reads from config with fallback)
# - ✅ FIXED: All syntax errors resolved
# - ✅ ADDED: Webhook AI service initialization call
# - ✅ ADDED: init_ai_service() integration from webhook
# - ✅ FIXED: AI service now properly initializes at startup
# - ✅ FULLY ALIGNED with webhook.py v5.3 (FastAPI version)
# - ✅ FULLY ALIGNED with ai_query_service.py v52.1
# - ✅ Degraded mode startup - NO RAILWAY CRASHES
# - ✅ All original attributes preserved
# ==========================================================

from __future__ import annotations

import os
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
# DATABASE IMPORTS
# ==========================================================

from app.database import (
    engine,
    DATABASE_URL,
    Base,
    get_db,
    SessionLocal,
    check_database_connection,
    get_database_health
)

from app.services.schema_service import (
    check_schema_version,
    get_schema_info,
    APP_SCHEMA_VERSION
)

from app.services.whatsapp_service import get_whatsapp_service

from app.config import config

# ==========================================================
# FIX: CACHE_TTL with fallback for compatibility
# ==========================================================
# This ensures CACHE_TTL is always defined even if missing from config
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
CACHE_TTL_SESSION = getattr(config, 'CACHE_TTL_SESSION', 1800)
CACHE_ENABLED = getattr(config, 'CACHE_ENABLED', True)

# ==========================================================
# MODEL IMPORTS
# ==========================================================

from app.models import (
    Customer,
    Conversation,
    Message,
    AIResponseLog,
    DeliveryReport
)

# ==========================================================
# WEBHOOK ROUTER IMPORT (FastAPI version)
# ==========================================================

WEBHOOK_AVAILABLE = False
WEBHOOK_ERROR = None

try:
    from app.routes.webhook import router as webhook_router
    WEBHOOK_AVAILABLE = True
    logger.info("✅ Webhook router (FastAPI) imported successfully")
except ImportError as e:
    WEBHOOK_ERROR = f"ImportError: {e}"
    logger.error(f"❌ Webhook router import failed: {e}")
except Exception as e:
    WEBHOOK_ERROR = f"Exception: {e}"
    logger.error(f"❌ Webhook router import error: {e}")

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
    logger.error(f"❌ AI Query Service import failed: {e}")
except Exception as e:
    AI_QUERY_SERVICE_ERROR = f"Exception: {e}"
    logger.error(f"❌ AI Query Service import error: {e}")


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
# LAZY ROUTER LOADING
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
            module = __import__(module_path, fromlist=["router"])
            router = getattr(module, "router", None)
            if router:
                app.include_router(router)
                ServiceRegistry.register_route(name, router)
                logger.info(f"✅ {name.capitalize()} router loaded")
            else:
                logger.warning(f"⚠️ No router found in {module_path}")
        except ImportError as e:
            logger.warning(f"⚠️ {name.capitalize()} router not available: {e}")
        except Exception as e:
            logger.exception(f"❌ {name.capitalize()} router failed to load: {e}")


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
# AI QUERY SERVICE INITIALIZATION (DEGRADED MODE)
# ==========================================================

def initialize_ai_query_services() -> Tuple[bool, Optional[Any], Dict[str, Any]]:
    """
    Initialize AI Query Service with all dependencies.
    CRITICAL: This function NO LONGER crashes on failures.
    Returns: (success, service_instance, diagnostics)
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
    
    # Check if AI Query Service imports are available
    if not AI_QUERY_SERVICE_AVAILABLE:
        diagnostics["error"] = AI_QUERY_SERVICE_ERROR or "AI Query Service imports failed"
        logger.error(f"❌ {diagnostics['error']}")
        logger.warning("⚠️ Continuing without AI Query Service - WhatsApp queries will use fallback")
        return False, None, diagnostics
    
    db = None
    analytics_service = None
    logistics_service = None
    kpi_service = None
    ai_provider_service = None
    
    try:
        # Create database session with proper cleanup
        db = SessionLocal()
        
        # Startup diagnostics
        logger.info("📋 STARTUP DIAGNOSTICS:")
        
        # Create analytics service
        logger.info("   Creating analytics service...")
        analytics_service = create_analytics_service(db)
        if analytics_service:
            diagnostics["analytics_available"] = True
            diagnostics["analytics_methods"] = validate_service_methods(
                analytics_service, 
                ["get_dealer_dashboard", "get_dealer_health", "get_pending_pod_aging", "get_pending_delivery_aging"],
                "AnalyticsService"
            )
            logger.info("   ✅ Analytics service created")
        else:
            logger.error("   ❌ Analytics service creation FAILED")
        
        # Create logistics service
        logger.info("   Creating logistics service...")
        logistics_service = create_logistics_service(db)
        if logistics_service:
            diagnostics["logistics_available"] = True
            diagnostics["logistics_methods"] = validate_service_methods(
                logistics_service,
                ["get_complete_dn_detail", "get_complete_dn_intelligence", "debug_dn_search"],
                "LogisticsService"
            )
            logger.info("   ✅ Logistics service created")
        else:
            logger.error("   ❌ Logistics service creation FAILED")
        
        # Create KPI service (OPTIONAL)
        logger.info("   Creating KPI service (optional)...")
        kpi_service = create_kpi_service(db)
        if kpi_service:
            diagnostics["kpi_available"] = True
            logger.info("   ✅ KPI service created")
        else:
            logger.warning("   ⚠️ KPI service not available - executive queries will use fallback")
        
        # Create AI provider service
        logger.info("   Creating AI provider service...")
        ai_provider_service = create_ai_provider_service()
        if ai_provider_service:
            diagnostics["ai_provider_available"] = True
            logger.info("   ✅ AI provider service created")
        else:
            logger.error("   ❌ AI provider service creation FAILED")
        
        # Check minimum requirements - LOG ONLY, NO CRASH
        if not diagnostics["analytics_available"]:
            logger.error("⚠️ CRITICAL: Analytics service not available - dealer queries will FAIL")
            diagnostics["warning"] = "Analytics service missing"
        
        if not diagnostics["logistics_available"]:
            logger.error("⚠️ CRITICAL: Logistics service not available - DN queries will FAIL")
            diagnostics["warning"] = diagnostics["warning"] or "Logistics service missing"
        
        # Initialize AI Query Service
        logger.info("   Initializing AI Query Service...")
        
        try:
            import inspect
            init_signature = inspect.signature(initialize_query_service)
            init_params = list(init_signature.parameters.keys())
            logger.info(f"   AI Query Service init expects: {init_params}")
            
            # Build kwargs based on available parameters
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
            
            # Store services in registry
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
            
        except TypeError as e:
            logger.error(f"❌ Parameter mismatch: {e}")
            # Try fallback initialization
            try:
                logger.info("   Attempting fallback initialization...")
                initialize_query_service(
                    analytics_service=analytics_service,
                    logistics_service=logistics_service
                )
                query_service = get_query_service()
                diagnostics["success"] = True
                logger.info("   ✅ Fallback initialization successful")
                return True, query_service, diagnostics
            except Exception as fallback_e:
                logger.error(f"❌ Fallback failed: {fallback_e}")
                diagnostics["error"] = str(fallback_e)
                return False, None, diagnostics
                
        except Exception as e:
            logger.error(f"❌ AI Query Service initialization failed: {e}")
            diagnostics["error"] = str(e)
            return False, None, diagnostics
        
    except Exception as e:
        logger.error(f"❌ AI Query Service initialization error: {e}")
        diagnostics["error"] = str(e)
        return False, None, diagnostics
        
    finally:
        if db:
            db.close()
            logger.debug("Database session closed")


def initialize_webhook_ai_service():
    """
    Initialize AI service through webhook module.
    This ensures the webhook's global service instance is properly set.
    """
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
# LIFESPAN HANDLER
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()
    
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v9.5.0")
    logger.info("=" * 80)
    
    # Log cache configuration (FIXED: CACHE_TTL now properly defined)
    logger.info("📦 CACHE CONFIGURATION:")
    logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
    logger.info(f"   CACHE_TTL_SESSION: {CACHE_TTL_SESSION}s")
    logger.info(f"   CACHE_ENABLED: {CACHE_ENABLED}")
    
    # Load routers FIRST
    logger.info("📡 Loading routers...")
    load_routers(app)
    
    # Also register webhook router directly if available (FastAPI version)
    if WEBHOOK_AVAILABLE:
        try:
            app.include_router(webhook_router)
            ServiceRegistry.register_route("webhook_direct", webhook_router)
            logger.info("✅ Webhook router (FastAPI) registered directly")
        except Exception as e:
            logger.error(f"❌ Failed to register webhook router directly: {e}")
    else:
        logger.warning(f"⚠️ Webhook router not available: {WEBHOOK_ERROR}")
    
    logger.info("✅ Routers loaded")
    
    # Validate environment
    env_results = StartupService.validate_environment()
    db_ok = StartupService.validate_database()
    groq_ok = StartupService.validate_groq()
    whatsapp_ok = StartupService.validate_whatsapp()
    
    logger.info("✅ STARTUP DIAGNOSTICS:")
    logger.info(f"   Database: {'✓' if db_ok else '✗'}")
    logger.info(f"   GROQ API: {'✓' if groq_ok else '✗'}")
    logger.info(f"   WhatsApp: {'✓' if whatsapp_ok else '✗'}")
    logger.info(f"   Environment: {config.ENVIRONMENT}")
    logger.info(f"   AI Service Import: {'✓' if AI_QUERY_SERVICE_AVAILABLE else '✗'}")
    logger.info(f"   Webhook Router: {'✓' if WEBHOOK_AVAILABLE else '✗'}")
    logger.info(f"   Cache TTL: {CACHE_TTL}s")
    
    # Initialize AI Query Service in degraded mode
    logger.info("=" * 40)
    logger.info("🔧 AI QUERY SERVICE INITIALIZATION")
    logger.info("=" * 40)
    
    ai_initialized, ai_service, ai_diagnostics = initialize_ai_query_services()
    
    if ai_initialized:
        logger.info("✅ AI Query Service initialized successfully")
        app.state.ai_query_available = True
        app.state.ai_query_service = ai_service
    else:
        logger.error("❌ AI Query Service initialization FAILED")
        logger.error(f"   Error: {ai_diagnostics.get('error', 'Unknown error')}")
        logger.warning("⚠️ App will run in DEGRADED MODE - WhatsApp queries will use fallback")
        app.state.ai_query_available = False
        app.state.ai_query_service = None
        app.state.ai_query_error = ai_diagnostics.get('error')
    
    # CRITICAL: Initialize AI service in webhook module
    logger.info("=" * 40)
    logger.info("🔧 WEBHOOK AI SERVICE INITIALIZATION")
    logger.info("=" * 40)
    
    webhook_ai_initialized = initialize_webhook_ai_service()
    
    if webhook_ai_initialized:
        logger.info("✅ Webhook AI service initialized successfully")
    else:
        logger.warning("⚠️ Webhook AI service initialization failed - fallback mode active")
    
    # Create upload directory
    os.makedirs("uploads", exist_ok=True)
    
    startup_duration = time.time() - start_time
    logger.info("=" * 80)
    logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
    logger.info(f"   AI Query Service: {'AVAILABLE' if ai_initialized else 'UNAVAILABLE (Degraded Mode)'}")
    logger.info(f"   Webhook AI Service: {'AVAILABLE' if webhook_ai_initialized else 'UNAVAILABLE'}")
    logger.info(f"   Webhook Router: {'AVAILABLE' if WEBHOOK_AVAILABLE else 'UNAVAILABLE'}")
    logger.info(f"   Webhook Timeout: 30s (aligned with webhook v5.3)")
    logger.info(f"   Cache TTL: {CACHE_TTL}s")
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
    version="9.5.0",
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
# GLOBAL EXCEPTION HANDLER
# ==========================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'unknown')
    
    if isinstance(exc, SQLAlchemyError):
        error_type = "database_error"
        logger.exception(f"Database error [req:{request_id}]: {exc}")
    elif hasattr(exc, 'status_code') and exc.status_code == 429:
        error_type = "rate_limit"
        logger.warning(f"Rate limit exceeded [req:{request_id}]")
    else:
        error_type = "internal_error"
        logger.exception(f"Unhandled exception [req:{request_id}]: {exc}")
    
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
# CACHE (FIXED: Now uses CACHE_TTL variable)
# ==========================================================

dashboard_cache = TTLCache(maxsize=100, ttl=CACHE_TTL)


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
# SIMPLIFIED CHAT ENDPOINT (Uses service layer)
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
# API VERSION 1 ENDPOINTS
# ==========================================================

@app.get("/api/v1/chat", response_model=ChatResponse, tags=["API v1"])
@limiter.limit("5 per second")
async def chat_v1(chat_request: ChatRequest, req: Request, db: Session = Depends(get_db)):
    """Versioned chat endpoint - API v1"""
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


@app.get("/api/v1/health", tags=["API v1"])
async def health_v1():
    return {"status": "healthy", "version": "v1", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/v1/status", tags=["API v1"])
async def status_v1(db: Session = Depends(get_db)):
    cache_key = "system_status_v1"
    cached = dashboard_cache.get(cache_key)
    if cached:
        return cached
    
    total_records = db.query(func.count(DeliveryReport.id)).scalar() or 0
    total_customers = db.query(func.count(Customer.id)).scalar() or 0
    
    result = {
        "version": "v1",
        "total_delivery_records": total_records,
        "total_customers": total_customers,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    dashboard_cache[cache_key] = result
    return result


# ==========================================================
# HEALTH ENDPOINTS (Aligned with webhook v5.3)
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
        "webhook_version": "5.3",
        "cache_ttl": CACHE_TTL,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/groq-health", tags=["Health"])
async def groq_health():
    groq_key = os.getenv("GROQ_API_KEY") or getattr(config, 'GROQ_API_KEY', None)
    return {
        "provider": "groq",
        "configured": bool(groq_key),
        "status": "healthy" if groq_key else "not_configured"
    }


@app.get("/database-health", tags=["Health"])
async def database_health():
    return get_database_health()


@app.get("/whatsapp-health", tags=["Health"])
async def whatsapp_health():
    try:
        whatsapp = get_whatsapp_service()
        return whatsapp.health_check()
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/ping", tags=["Health"])
async def ping():
    return {
        "ping": "pong", 
        "timestamp": datetime.utcnow().isoformat(),
        "ai_query_available": getattr(app.state, 'ai_query_available', False),
        "webhook_available": WEBHOOK_AVAILABLE
    }


# ==========================================================
# AI QUERY SERVICE HEALTH ENDPOINT
# ==========================================================

@app.get("/ai-query-health", tags=["Health"])
async def ai_query_health():
    """Get AI Query Service health status"""
    if not hasattr(app.state, 'ai_query_available') or not app.state.ai_query_available:
        return {
            "status": "unavailable",
            "available": False,
            "error": getattr(app.state, 'ai_query_error', 'Not initialized'),
            "message": "AI Query Service is not available. App running in degraded mode.",
            "fallback_active": True
        }
    
    try:
        if hasattr(app.state, 'ai_query_service') and app.state.ai_query_service:
            return app.state.ai_query_service.health_check()
        else:
            return {
                "status": "error",
                "available": False,
                "error": "Service instance not found"
            }
    except Exception as e:
        return {
            "status": "error",
            "available": False,
            "error": str(e)
        }


# ==========================================================
# PROMETHEUS METRICS ENDPOINT
# ==========================================================

@app.get("/metrics", tags=["Metrics"])
async def metrics():
    """Fixed metrics endpoint - returns bytes correctly"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/cache-status", tags=["Admin"])
async def cache_status():
    return {
        "cache_size": len(dashboard_cache),
        "cache_maxsize": dashboard_cache.maxsize,
        "cache_ttl_seconds": CACHE_TTL,
        "type": "in_memory_ttlcache"
    }


# ==========================================================
# SIMPLIFIED DASHBOARD ENDPOINT
# ==========================================================

def get_analytics_service():
    """Lazy load analytics service"""
    from app.services.analytics_service import AnalyticsService
    return AnalyticsService


@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Dashboard endpoint - uses AnalyticsService for data"""
    try:
        AnalyticsServiceClass = get_analytics_service()
        analytics_service = AnalyticsServiceClass(db)
        
        dashboard_data = analytics_service.get_dashboard_data()
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        schema_info = get_schema_info(db)
        last_refresh = datetime.utcnow()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                **dashboard_data,
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "groq_status": "Online" if groq_key else "Offline",
                "webhook_status": "Online" if WEBHOOK_AVAILABLE else "Offline",
                "schema_version": schema_info.get("app_version", "9.5"),
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat(),
                "ai_query_available": getattr(app.state, 'ai_query_available', False),
                "cache_ttl": CACHE_TTL
            }
        )
    except Exception as e:
        logger.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================================
# LEGACY STATUS ENDPOINT
# ==========================================================

@app.get("/status", tags=["Status"])
async def status_legacy(db: Session = Depends(get_db)):
    """Legacy status endpoint"""
    cache_key = "system_status"
    cached = dashboard_cache.get(cache_key)
    if cached:
        return cached
    
    result = {
        "application": "AI WhatsApp Agent",
        "version": "9.5.0",
        "database": "postgresql",
        "ai_provider": "groq",
        "whatsapp": "active",
        "webhook": "available" if WEBHOOK_AVAILABLE else "unavailable",
        "ai_query_available": getattr(app.state, 'ai_query_available', False),
        "cache_ttl": CACHE_TTL,
        "statistics": {
            "total_customers": db.query(func.count(Customer.id)).scalar() or 0,
            "total_conversations": db.query(func.count(Conversation.id)).scalar() or 0,
            "total_delivery_records": db.query(func.count(DeliveryReport.id)).scalar() or 0
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    dashboard_cache[cache_key] = result
    return result


# ==========================================================
# ROOT AND INFO ENDPOINTS
# ==========================================================

@app.get("/", tags=["Root"])
async def home():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Logistics Assistant",
        "version": "9.5.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION,
        "ai_provider": "groq",
        "ai_query_service": "initialized" if getattr(app.state, 'ai_query_available', False) else "unavailable",
        "webhook_version": "5.3",
        "webhook_available": WEBHOOK_AVAILABLE,
        "ai_query_version": AI_QUERY_SERVICE_VERSION,
        "cache_ttl": CACHE_TTL
    }


@app.get("/schema-info", tags=["Info"])
async def schema_info_endpoint(db: Session = Depends(get_db)):
    return get_schema_info(db)


@app.get("/upload-center", tags=["Upload"])
async def upload_center(request: Request, db: Session = Depends(get_db)):
    """Upload center page - uses analytics service"""
    try:
        AnalyticsServiceClass = get_analytics_service()
        analytics_service = AnalyticsServiceClass(db)
        
        latest_uploads = analytics_service.get_latest_uploads(20)
        total_batches = analytics_service.get_total_upload_batches()
        total_records = db.query(DeliveryReport).count()
        
        return templates.TemplateResponse(
            "upload_center.html",
            {
                "request": request,
                "latest_uploads": latest_uploads or [],
                "total_batches": total_batches,
                "total_records": total_records,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.exception("Upload center error")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/download-template", tags=["Upload"])
async def download_template():
    import pandas as pd
    import io
    from fastapi.responses import StreamingResponse
    
    template_data = {
        "DN No": ["DN12345"],
        "Customer Name": ["ABC Traders"],
        "DN Amount": [1000.00],
        "Ship To City": ["New York"],
        "Warehouse": ["Main Warehouse"]
    }
    
    df = pd.DataFrame(template_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Template', index=False)
    
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=logistics_template.xlsx"}
    )


@app.get("/upload-status", tags=["Upload"])
async def upload_status():
    return {
        "upload_folder_exists": os.path.exists("uploads"),
        "writable": os.access("uploads", os.W_OK) if os.path.exists("uploads") else False,
        "status": "ready"
    }


# ==========================================================
# API INFO ENDPOINT
# ==========================================================

@app.get("/api-info", tags=["Info"])
async def api_info():
    return {
        "current_version": "v1",
        "api_base": "/api/v1",
        "endpoints": {
            "chat": "/api/v1/chat",
            "health": "/api/v1/health",
            "status": "/api/v1/status"
        },
        "webhook_endpoints": {
            "verify": "/webhook (GET)",
            "receive": "/webhook (POST)",
            "health": "/webhook/health",
            "session": "/webhook/session/{phone_number}"
        },
        "legacy_endpoints": {
            "chat": "/chat",
            "dashboard": "/dashboard",
            "webhook": "/webhook/"
        },
        "documentation": "/api/docs" if config.ENVIRONMENT != "production" else "disabled"
    }


# ==========================================================
# DEGRADED MODE STATUS ENDPOINT
# ==========================================================

@app.get("/degraded-status", tags=["Health"])
async def degraded_status():
    """Check if app is running in degraded mode"""
    return {
        "ai_query_available": getattr(app.state, 'ai_query_available', False),
        "webhook_available": WEBHOOK_AVAILABLE,
        "degraded_mode": not getattr(app.state, 'ai_query_available', True),
        "error": getattr(app.state, 'ai_query_error', None),
        "message": "Application is running but some features may be limited" if not getattr(app.state, 'ai_query_available', True) else "All services available",
        "fallback_active": not getattr(app.state, 'ai_query_available', True)
    }


# ==========================================================
# SERVICE VERSIONS ENDPOINT
# ==========================================================

@app.get("/service-versions", tags=["Info"])
async def service_versions():
    """Get all service versions for debugging"""
    versions = {
        "app": "9.5.0",
        "webhook": "5.3",
        "ai_query": AI_QUERY_SERVICE_VERSION,
        "schema": APP_SCHEMA_VERSION,
        "environment": config.ENVIRONMENT,
        "cache_ttl": CACHE_TTL,
        "webhook_router_available": WEBHOOK_AVAILABLE
    }
    
    # Try to get service versions from compatibility layers
    if hasattr(app.state, 'ai_query_service') and app.state.ai_query_service:
        try:
            health = app.state.ai_query_service.health_check()
            versions["ai_service_details"] = health.get("service_versions", {})
        except Exception:
            pass
    
    return versions


# ==========================================================
# DEBUG ENDPOINTS (Hidden in production)
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/db-test", tags=["Debug"])
    async def db_test():
        try:
            connected = check_database_connection()
            health = get_database_health()
            return {
                "connected": connected,
                "database_url_exists": bool(DATABASE_URL),
                "health": health,
                "environment": config.ENVIRONMENT,
                "cache_ttl": CACHE_TTL
            }
        except Exception as e:
            logger.exception("DB test error")
            raise HTTPException(status_code=500, detail=str(e))
    
    @app.get("/ai-query-debug", tags=["Debug"])
    async def ai_query_debug():
        """Debug endpoint for AI Query Service"""
        if not getattr(app.state, 'ai_query_available', False):
            return {
                "initialized": False,
                "available": False,
                "error": getattr(app.state, 'ai_query_error', 'Not initialized'),
                "message": "AI Query Service not available - app in degraded mode",
                "fallback_active": True
            }
        
        try:
            query_service = get_query_service()
            return {
                "initialized": True,
                "available": True,
                "health": query_service.health_check(),
                "metrics": query_service.get_metrics()
            }
        except Exception as e:
            return {
                "initialized": False,
                "available": False,
                "error": str(e)
            }
    
    @app.get("/webhook-status", tags=["Debug"])
    async def webhook_status():
        """Debug endpoint for webhook AI service status"""
        try:
            from app.routes.webhook import is_ai_service_ready, _ai_service_initialization_error
            return {
                "ai_service_ready": is_ai_service_ready(),
                "initialization_error": _ai_service_initialization_error,
                "router_available": WEBHOOK_AVAILABLE,
                "fallback_active": not is_ai_service_ready()
            }
        except Exception as e:
            return {
                "error": str(e),
                "ai_service_ready": False,
                "router_available": WEBHOOK_AVAILABLE
            }
    
    @app.get("/routes", tags=["Debug"])
    async def list_routes():
        """List all registered routes for debugging"""
        routes = []
        for route in app.routes:
            routes.append({
                "path": route.path,
                "methods": list(route.methods) if hasattr(route, 'methods') else [],
                "name": route.name if hasattr(route, 'name') else None
            })
        return {"routes": routes, "count": len(routes)}


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v9.5.0 - FASTAPI WEBHOOK INTEGRATED")
logger.info("")
logger.info("   ALIGNED WITH:")
logger.info("   ✅ webhook.py v5.3 (FastAPI version)")
logger.info("   ✅ ai_query_service.py v52.1 (improved startup)")
logger.info("   ✅ logistics_query_service.py v9.2")
logger.info("   ✅ analytics_service.py")
logger.info("")
logger.info("   KEY FEATURES:")
logger.info("   ✅ Webhook AI service initialization at startup")
logger.info("   ✅ FastAPI webhook router integration")
logger.info("   ✅ Degraded mode - NO RAILWAY CRASHES")
logger.info("   ✅ Direct DN fallback when AI service down")
logger.info("   ✅ 30s webhook timeout alignment")
logger.info("   ✅ Service version tracking")
logger.info("   ✅ Debug endpoints for troubleshooting")
logger.info("   ✅ CACHE_TTL properly configured with fallback")
logger.info("   ✅ All original attributes preserved")
logger.info("")
logger.info(f"   AI SERVICE IMPORT: {'✓' if AI_QUERY_SERVICE_AVAILABLE else '✗'}")
logger.info(f"   AI SERVICE VERSION: {AI_QUERY_SERVICE_VERSION or 'unknown'}")
logger.info(f"   WEBHOOK ROUTER: {'✓' if WEBHOOK_AVAILABLE else '✗'}")
logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
logger.info("=" * 60)
