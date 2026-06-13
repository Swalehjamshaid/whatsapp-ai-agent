# ==========================================================
# FILE: app/main.py (ENTERPRISE v10.2.0 - ENHANCED IMPORT DIAGNOSTICS)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v10.2.0:
# - ✅ ENHANCED: diagnose_import() with visual separator and detailed error output
# - ✅ ADDED: Individual module import testing with clear success/failure indicators
# - ✅ ADDED: Module-level crash location reporting
# - ✅ FIXED: Import failures now show exact module name
# - ✅ All original attributes preserved
# ==========================================================

from __future__ import annotations

import os
import sys
import json
import importlib
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
# CRASH LOCATION HELPER
# ==========================================================

def crash_location(exc: Exception) -> Optional[Dict[str, Any]]:
    """Extract the exact crash location from an exception"""
    tb = traceback.extract_tb(exc.__traceback__)
    
    for frame in reversed(tb):
        if "/app/" in frame.filename:
            return {
                "file": frame.filename,
                "line": frame.lineno,
                "function": frame.name,
                "code": frame.line if frame.line else "Unknown"
            }
    
    if tb:
        last_frame = tb[-1]
        return {
            "file": last_frame.filename,
            "line": last_frame.lineno,
            "function": last_frame.name,
            "code": last_frame.line if last_frame.line else "Unknown"
        }
    
    return None


def write_crash_report(exc: Exception, stage: str = "unknown"):
    """Write crash report to JSON file"""
    location = crash_location(exc)
    
    crash_data = {
        "stage": stage,
        "timestamp": datetime.now().isoformat(),
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "traceback": traceback.format_exc(),
        "crash_file": location["file"] if location else "Unknown",
        "crash_line": location["line"] if location else "Unknown",
        "crash_function": location["function"] if location else "Unknown",
    }
    
    try:
        with open("/tmp/startup_crash.json", "w") as f:
            json.dump(crash_data, f, indent=2)
        logger.error(f"Crash report written to /tmp/startup_crash.json")
    except Exception:
        pass


# ==========================================================
# ENHANCED IMPORT DIAGNOSTICS (Improvement - Most Important)
# ==========================================================

def diagnose_import(module_name: str):
    """
    Diagnose a module import with clear visual output.
    Shows exact module name, success/failure, and detailed error.
    """
    logger.info("=" * 80)
    logger.info(f"📦 IMPORTING: {module_name}")
    
    try:
        module = importlib.import_module(module_name)
        
        logger.success("=" * 80)
        logger.success(f"✅ SUCCESS: {module_name}")
        logger.success("=" * 80)
        
        return module
        
    except Exception as e:
        location = crash_location(e)
        
        logger.critical("=" * 80)
        logger.critical(f"❌ FAILED MODULE: {module_name}")
        logger.critical("=" * 80)
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)}")
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
        
        logger.critical("=" * 80)
        logger.exception("FULL TRACEBACK:")
        logger.critical("=" * 80)
        
        write_crash_report(e, f"import_{module_name}")
        raise


# ==========================================================
# SERVICE HEALTH MATRIX
# ==========================================================

SERVICE_STATUS = {
    "webhook": False,
    "ai_provider": False,
    "ai_query": False,
    "analytics": False,
    "kpi": False,
    "schema": False,
    "whatsapp": False,
    "logistics_query": False,
    "database": False,
}


# ==========================================================
# STARTUP DIAGNOSTICS REGISTRY
# ==========================================================

STARTUP_DIAGNOSTICS = {
    "startup_time": None,
    "startup_duration": None,
    "status": "STARTING",
    "services": {},
    "imports": {},
    "env_vars": {},
    "errors": [],
    "stages": []
}


# ==========================================================
# UNIVERSAL SERVICE CHECKER
# ==========================================================

def diagnose_service(service_name: str, func, *args, **kwargs):
    """Universal service checker with timing and error capture"""
    import time
    
    start = time.time()
    
    try:
        logger.info("=" * 60)
        logger.info(f"🚀 STARTING: {service_name}")
        
        result = func(*args, **kwargs)
        
        elapsed = round(time.time() - start, 2)
        
        STARTUP_DIAGNOSTICS["services"][service_name] = {
            "status": "SUCCESS",
            "load_time": elapsed,
            "timestamp": datetime.now().isoformat()
        }
        
        # Update service status matrix
        for key in SERVICE_STATUS.keys():
            if key.lower() in service_name.lower():
                SERVICE_STATUS[key] = True
                break
        
        logger.success("=" * 60)
        logger.success(f"✅ {service_name} loaded in {elapsed}s")
        logger.success("=" * 60)
        
        return result
        
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        location = crash_location(e)
        
        error_data = {
            "service": service_name,
            "error": str(e),
            "error_type": type(e).__name__,
            "load_time": elapsed,
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now().isoformat(),
            "crash_file": location["file"] if location else "Unknown",
            "crash_line": location["line"] if location else "Unknown"
        }
        
        STARTUP_DIAGNOSTICS["services"][service_name] = {
            "status": "FAILED",
            "load_time": elapsed,
            "error": str(e),
            "error_type": type(e).__name__,
            "crash_file": location["file"] if location else "Unknown",
            "crash_line": location["line"] if location else "Unknown"
        }
        
        STARTUP_DIAGNOSTICS["errors"].append(error_data)
        
        logger.critical("=" * 80)
        logger.critical(f"❌ {service_name} FAILED after {elapsed}s")
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)}")
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
        
        logger.critical("=" * 80)
        logger.exception("FULL TRACEBACK:")
        
        write_crash_report(e, service_name)
        raise


# ==========================================================
# SERVICE FILES TO DIAGNOSE (In order)
# ==========================================================

SERVICE_FILES = [
    "app.services.ai_provider_service",
    "app.services.ai_query_service",
    "app.services.analytics_service",
    "app.services.kpi_service",
    "app.services.logistics_query_service",
    "app.services.schema_service",
    "app.services.whatsapp_service",
    "app.routes.webhook"
]

# ==========================================================
# REQUIRED ENVIRONMENT VARIABLES
# ==========================================================

REQUIRED_ENVS = [
    "DATABASE_URL",
    "GROQ_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_VERIFY_TOKEN"
]

OPTIONAL_ENVS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "REDIS_URL"
]


# ==========================================================
# SAFE IMPORTS (These won't crash the app)
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
    SERVICE_STATUS["database"] = True
    logger.info("✅ Database module loaded")
except Exception as e:
    logger.error(f"❌ Database module failed: {e}")
    SERVICE_STATUS["database"] = False

try:
    from app.config import config
    logger.info("✅ Config module loaded")
except Exception as e:
    logger.error(f"❌ Config module failed: {e}")
    raise

# ==========================================================
# CACHE_TTL with fallback
# ==========================================================
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
CACHE_TTL_SESSION = getattr(config, 'CACHE_TTL_SESSION', 1800)
CACHE_ENABLED = getattr(config, 'CACHE_ENABLED', True)


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
# ENVIRONMENT VARIABLE DIAGNOSTICS
# ==========================================================

def diagnose_environment_variables():
    """Check all required and optional environment variables"""
    logger.info("=" * 60)
    logger.info("🔍 ENVIRONMENT VARIABLES DIAGNOSTICS")
    logger.info("=" * 60)
    
    for env in REQUIRED_ENVS:
        value = os.getenv(env)
        if value:
            STARTUP_DIAGNOSTICS["env_vars"][env] = {"status": "SET"}
            logger.info(f"   ✅ {env}: SET")
        else:
            STARTUP_DIAGNOSTICS["env_vars"][env] = {"status": "MISSING"}
            error_data = {"env": env, "error": "Missing required environment variable"}
            STARTUP_DIAGNOSTICS["errors"].append(error_data)
            logger.error(f"   ❌ {env}: MISSING - REQUIRED!")
    
    for env in OPTIONAL_ENVS:
        value = os.getenv(env)
        if value:
            STARTUP_DIAGNOSTICS["env_vars"][env] = {"status": "SET"}
            logger.info(f"   ✅ {env}: SET (optional)")
        else:
            STARTUP_DIAGNOSTICS["env_vars"][env] = {"status": "NOT_SET"}
            logger.warning(f"   ⚠️ {env}: NOT SET (optional)")
    
    logger.info("=" * 60)


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
# DEPENDENCY TREE OUTPUT
# ==========================================================

def print_dependency_tree():
    """Print dependency tree at startup"""
    tree = """
╔══════════════════════════════════════════════════════════════════╗
║                      DEPENDENCY TREE                             ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  main.py                                                         ║
║   ├── database.py                                                ║
║   ├── config.py                                                  ║
║   ├── models.py                                                  ║
║   │                                                              ║
║   ├── routes/                                                    ║
║   │    ├── webhook.py                                            ║
║   │    ├── upload.py                                             ║
║   │    ├── admin.py                                              ║
║   │    ├── health.py                                             ║
║   │    └── logistics.py                                          ║
║   │                                                              ║
║   └── services/                                                  ║
║        ├── ai_provider_service.py                                ║
║        ├── ai_query_service.py                                   ║
║        ├── analytics_service.py                                  ║
║        ├── kpi_service.py                                        ║
║        ├── logistics_query_service.py                            ║
║        ├── schema_service.py                                     ║
║        └── whatsapp_service.py                                   ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
"""
    logger.info(tree)


# ==========================================================
# LIFESPAN HANDLER (All imports tested individually)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    STARTUP_DIAGNOSTICS["startup_time"] = datetime.now().isoformat()
    start_time = time.time()
    
    # Print dependency tree
    print_dependency_tree()
    
    try:
        logger.info("=" * 80)
        logger.info("🤖 AI WHATSAPP AGENT STARTING v10.2.0")
        logger.info("=" * 80)
        
        # ==========================================================
        # STAGE 1/12 - Environment Variables
        # ==========================================================
        logger.info("📍 STAGE 1/12: Environment Variables")
        diagnose_environment_variables()
        
        # ==========================================================
        # STAGE 2/12 - Individual Module Imports (CRITICAL)
        # ==========================================================
        logger.info("📍 STAGE 2/12: Individual Module Import Diagnostics")
        logger.info("=" * 80)
        logger.info("TESTING EACH MODULE INDIVIDUALLY:")
        logger.info("=" * 80)
        
        # Test each module import individually
        imported_modules = {}
        for module_name in SERVICE_FILES:
            try:
                imported_modules[module_name] = diagnose_import(module_name)
            except Exception as e:
                # The diagnose_import function already logs the error
                # Re-raise to stop startup
                raise
        
        # Extract webhook router if available
        webhook_router = None
        if "app.routes.webhook" in imported_modules:
            webhook_router = getattr(imported_modules["app.routes.webhook"], "router", None)
            if webhook_router:
                SERVICE_STATUS["webhook"] = True
                logger.success("✅ Webhook router extracted successfully")
        
        # ==========================================================
        # STAGE 3/12 - Cache Configuration
        # ==========================================================
        logger.info("📍 STAGE 3/12: Cache Configuration")
        logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
        logger.info(f"   CACHE_TTL_SESSION: {CACHE_TTL_SESSION}s")
        logger.info(f"   CACHE_ENABLED: {CACHE_ENABLED}")
        logger.info("   ✅ Cache configuration loaded")
        
        # ==========================================================
        # STAGE 4/12 - Load Additional Routers
        # ==========================================================
        logger.info("📍 STAGE 4/12: Loading Additional Routers")
        
        routers_to_load = [
            ("upload", "app.routes.upload"),
            ("admin", "app.routes.admin"),
            ("health", "app.routes.health"),
            ("logistics", "app.routes.logistics"),
        ]
        
        for name, module_path in routers_to_load:
            try:
                logger.info(f"   📍 Loading {name} router...")
                module = importlib.import_module(module_path)
                router = getattr(module, "router", None)
                if router:
                    app.include_router(router)
                    logger.success(f"   ✅ {name.capitalize()} router loaded")
                else:
                    logger.warning(f"   ⚠️ No router found in {module_path}")
            except Exception as e:
                location = crash_location(e)
                logger.error(f"   ❌ Failed to load {name} router: {e}")
                if location:
                    logger.error(f"      Location: {location['file']}:{location['line']}")
                write_crash_report(e, f"router_{name}")
                raise
        
        # ==========================================================
        # STAGE 5/12 - Register Webhook Router
        # ==========================================================
        logger.info("📍 STAGE 5/12: Registering Webhook Router")
        if webhook_router:
            app.include_router(webhook_router)
            ServiceRegistry.register_route("webhook_direct", webhook_router)
            logger.success("   ✅ Webhook router registered successfully")
        else:
            logger.warning("   ⚠️ Webhook router not available")
        
        # ==========================================================
        # STAGE 6/12 - Validate Database Connection
        # ==========================================================
        logger.info("📍 STAGE 6/12: Validating Database Connection")
        db_ok = check_database_connection()
        SERVICE_STATUS["database"] = db_ok
        if db_ok:
            logger.success("   ✅ Database connected")
        else:
            logger.error("   ❌ Database connection failed")
        
        # ==========================================================
        # STAGE 7/12 - Initialize Schema Service
        # ==========================================================
        logger.info("📍 STAGE 7/12: Initializing Schema Service")
        try:
            from app.services.schema_service import get_schema_service
            schema_service = diagnose_service("Schema Service", get_schema_service)
            SERVICE_STATUS["schema"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ Schema Service optional: {e}")
            SERVICE_STATUS["schema"] = False
        
        # ==========================================================
        # STAGE 8/12 - Initialize KPI Service
        # ==========================================================
        logger.info("📍 STAGE 8/12: Initializing KPI Service")
        try:
            from app.services.kpi_service import get_kpi_service
            kpi_service = diagnose_service("KPI Service", get_kpi_service)
            SERVICE_STATUS["kpi"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ KPI Service optional: {e}")
            SERVICE_STATUS["kpi"] = False
        
        # ==========================================================
        # STAGE 9/12 - Initialize Analytics Service
        # ==========================================================
        logger.info("📍 STAGE 9/12: Initializing Analytics Service")
        try:
            from app.services.analytics_service import get_analytics_service
            analytics_service = diagnose_service("Analytics Service", get_analytics_service)
            SERVICE_STATUS["analytics"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ Analytics Service optional: {e}")
            SERVICE_STATUS["analytics"] = False
        
        # ==========================================================
        # STAGE 10/12 - Initialize AI Provider Service
        # ==========================================================
        logger.info("📍 STAGE 10/12: Initializing AI Provider Service")
        try:
            from app.services.ai_provider_service import AIProviderService
            ai_provider_service = diagnose_service("AI Provider Service", AIProviderService)
            SERVICE_STATUS["ai_provider"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ AI Provider Service optional: {e}")
            SERVICE_STATUS["ai_provider"] = False
        
        # ==========================================================
        # STAGE 11/12 - Initialize AI Query Service
        # ==========================================================
        logger.info("📍 STAGE 11/12: Initializing AI Query Service")
        try:
            from app.services.ai_query_service import get_ai_query_service
            ai_query_service = diagnose_service("AI Query Service", get_ai_query_service)
            SERVICE_STATUS["ai_query"] = True
            app.state.ai_query_available = True
            app.state.ai_query_service = ai_query_service
        except Exception as e:
            logger.error(f"   ❌ AI Query Service failed: {e}")
            SERVICE_STATUS["ai_query"] = False
            app.state.ai_query_available = False
            app.state.ai_query_service = None
        
        # ==========================================================
        # STAGE 12/12 - Initialize WhatsApp Service
        # ==========================================================
        logger.info("📍 STAGE 12/12: Initializing WhatsApp Service")
        try:
            from app.services.whatsapp_service import get_whatsapp_service
            whatsapp_service = diagnose_service("WhatsApp Service", get_whatsapp_service)
            SERVICE_STATUS["whatsapp"] = True
        except Exception as e:
            logger.error(f"   ❌ WhatsApp Service failed: {e}")
            SERVICE_STATUS["whatsapp"] = False
        
        # ==========================================================
        # Create Directories
        # ==========================================================
        logger.info("📍 Creating Directories")
        os.makedirs("uploads", exist_ok=True)
        TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        logger.success("   ✅ Directories created")
        
        # ==========================================================
        # STARTUP COMPLETE
        # ==========================================================
        startup_duration = time.time() - start_time
        STARTUP_DIAGNOSTICS["startup_duration"] = startup_duration
        STARTUP_DIAGNOSTICS["status"] = "COMPLETED"
        
        logger.info("=" * 80)
        logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
        logger.info("=" * 80)
        logger.info("SERVICE STATUS SUMMARY:")
        logger.info("-" * 40)
        for service, status in SERVICE_STATUS.items():
            status_icon = "✅" if status else "❌"
            logger.info(f"{status_icon} {service}: {'LOADED' if status else 'FAILED'}")
        logger.info("-" * 40)
        logger.info("🚀 APPLICATION STARTED SUCCESSFULLY")
        logger.info("📡 READY FOR TRAFFIC")
        logger.info("=" * 80)
        
        yield
        
    except Exception as e:
        STARTUP_DIAGNOSTICS["status"] = "FAILED"
        location = crash_location(e)
        
        logger.critical("=" * 80)
        logger.critical("💥 APPLICATION STARTUP FAILED 💥")
        logger.critical("=" * 80)
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
        
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)}")
        logger.critical("=" * 80)
        logger.critical("FULL TRACEBACK:")
        logger.critical(traceback.format_exc())
        logger.critical("=" * 80)
        
        write_crash_report(e, "lifespan")
        raise
    
    finally:
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
    version="10.2.0",
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
    location = crash_location(exc)
    
    if location:
        logger.error(f"💥 CRASH [req:{request_id}] at {location['file']}:{location['line']}")
    else:
        logger.error(f"💥 CRASH [req:{request_id}]: {type(exc).__name__}: {str(exc)}")
    
    if isinstance(exc, SQLAlchemyError):
        error_type = "database_error"
    elif hasattr(exc, 'status_code') and exc.status_code == 429:
        error_type = "rate_limit"
    else:
        error_type = "internal_error"
    
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
# DIAGNOSTICS ENDPOINTS
# ==========================================================

@app.get("/diagnostics", tags=["Diagnostics"])
async def get_diagnostics():
    """Get complete startup diagnostics"""
    return STARTUP_DIAGNOSTICS


@app.get("/last-error", tags=["Diagnostics"])
async def get_last_error():
    """Get the last error that occurred during startup"""
    if not STARTUP_DIAGNOSTICS["errors"]:
        return {"status": "NO_ERRORS"}
    return STARTUP_DIAGNOSTICS["errors"][-1]


@app.get("/startup-crash", tags=["Diagnostics"])
async def get_startup_crash():
    """Read the startup crash JSON file"""
    try:
        with open("/tmp/startup_crash.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"status": "NO_CRASH"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/service-status", tags=["Diagnostics"])
async def get_service_status():
    """Get service health matrix"""
    return {
        "services": SERVICE_STATUS,
        "timestamp": datetime.now().isoformat(),
        "overall_health": SERVICE_STATUS["database"] and SERVICE_STATUS["webhook"]
    }


@app.get("/crash-diagnostics", tags=["Debug"])
async def crash_diagnostics():
    """Get detailed crash diagnostics"""
    return {
        "status": STARTUP_DIAGNOSTICS["status"],
        "version": "10.2.0",
        "services_status": SERVICE_STATUS,
        "errors_count": len(STARTUP_DIAGNOSTICS["errors"]),
        "imports_count": len(STARTUP_DIAGNOSTICS["imports"])
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
    from app.services.chat_service import ChatService
    return ChatService


@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
@limiter.limit("5 per second")
async def chat_endpoint(chat_request: ChatRequest, req: Request, db: Session = Depends(get_db)):
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
    
    return {
        "ready": db_connected and bool(groq_key),
        "checks": {
            "database": "connected" if db_connected else "disconnected",
            "groq": "configured" if groq_key else "not_configured",
        },
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health", tags=["Health"])
async def health():
    db_connected = check_database_connection()
    uptime = request_metrics.get()["uptime_seconds"]
    
    return {
        "status": "healthy" if db_connected else "degraded",
        "uptime_seconds": round(uptime, 2),
        "database": "connected" if db_connected else "disconnected",
        "environment": config.ENVIRONMENT,
        "services": SERVICE_STATUS,
        "cache_ttl": CACHE_TTL,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {
        "ping": "pong", 
        "timestamp": datetime.utcnow().isoformat(),
        "healthy": SERVICE_STATUS["database"] and SERVICE_STATUS["webhook"]
    }


# ==========================================================
# CRASH TEST ENDPOINT
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/test-crash")
    async def test_crash():
        raise RuntimeError("This is a test crash - check logs for file and line number")


# ==========================================================
# ENTRY POINT
# ==========================================================

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=config.DEBUG,
        log_level="info"
    )


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v10.2.0 - ENHANCED IMPORT DIAGNOSTICS")
logger.info("")
logger.info("   ENHANCED FEATURES:")
logger.info("   ✅ Individual module import testing")
logger.info("   ✅ Visual separators for each import")
logger.info("   ✅ Exact module name on failure")
logger.info("   ✅ Error type and message display")
logger.info("   ✅ Crash file and line location")
logger.info("")
logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
logger.info(f"   MODULES TO TEST: {len(SERVICE_FILES)}")
logger.info("=" * 60)
