# ==========================================================
# FILE: app/main.py (ENTERPRISE v14.0 - FULLY INTEGRATED)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v14.0:
# - ✅ FULL INTEGRATION with webhook.py v11.0
# - ✅ Webhook services initialized in lifespan
# - ✅ Webhook stats exposed via /webhook-stats endpoint
# - ✅ All webhook debug endpoints integrated
# - ✅ CRITICAL FIX: preflight_result defined before use
# - ✅ CRITICAL FIX: app = FastAPI() created BEFORE any decorators
# - ✅ CRITICAL FIX: All problematic middleware DISABLED
# - ✅ ADDED: Debug endpoints (/debug/ping, /debug/health, /debug/routes, /debug/env)
# - ✅ ADDED: RAW endpoint (/raw-ping) - NO middleware, NO dependencies
# - ✅ ADDED: TrustedHostMiddleware DISABLED
# - ✅ ADDED: Global exception handler
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

# ==========================================================
# STEP 2: GLOBAL CRASH HANDLER (Must be at the very top)
# ==========================================================

def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    """Global exception handler to catch crashes before FastAPI starts"""
    print("=" * 80, file=sys.stderr)
    print("💥 UNCAUGHT EXCEPTION CAUGHT BY GLOBAL HANDLER", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    print(f"TYPE: {exc_type.__name__}", file=sys.stderr)
    print(f"ERROR: {exc_value}", file=sys.stderr)
    print("", file=sys.stderr)
    print("FULL TRACEBACK:", file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    traceback.print_exception(exc_type, exc_value, exc_traceback, file=sys.stderr)
    print("=" * 80, file=sys.stderr)
    
    # Try to write to file
    try:
        with open("/tmp/crash_dump.txt", "w") as f:
            f.write(f"TYPE: {exc_type.__name__}\n")
            f.write(f"ERROR: {exc_value}\n\n")
            f.write("TRACEBACK:\n")
            f.write("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
    except:
        pass
    
    # Call default handler
    sys.__excepthook__(exc_type, exc_value, exc_traceback)

# Install global crash hook
sys.excepthook = handle_uncaught_exception

# ==========================================================
# STARTUP CHECKPOINTS
# ==========================================================

print("=" * 60)
print("🚀 RAILWAY DEPLOYMENT STARTING")
print(f"TIME: {datetime.now().isoformat()}")
print("=" * 60)

print("CHECKPOINT 1 - BEGINNING MODULE LOAD")
print("CHECKPOINT 2 - CONFIG LOAD (next)")
print("CHECKPOINT 3 - DATABASE IMPORT (next)")
print("CHECKPOINT 4 - FASTAPI IMPORTS (next)")
print("CHECKPOINT 5 - ROUTE REGISTRATION (next)")
print("=" * 60)

# ==========================================================
# FASTAPI IMPORTS
# ==========================================================

print("CHECKPOINT 1 - IMPORTING FASTAPI MODULES")
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

print("✅ FastAPI modules imported")

# ==========================================================
# STEP 1: FIX BUGS - Module level imports
# ==========================================================

print("CHECKPOINT 2 - LOADING CONFIG")
from app.config import config
print(f"✅ Config loaded - ENVIRONMENT: {config.ENVIRONMENT}")

print("CHECKPOINT 3 - IMPORTING DATABASE (MODULE LEVEL)")
# CRITICAL FIX #1: Import get_db at module level
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
    print("✅ Database module imported at module level")
    print(f"   ├── get_db imported: {get_db is not None}")
    print(f"   ├── DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "   ├── DATABASE_URL: None")
except Exception as e:
    print(f"❌ CRITICAL: Database import failed at module level")
    print(f"   ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
    raise

# CRITICAL FIX #2: Define CACHE_TTL at module level
CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
print(f"✅ CACHE_TTL = {CACHE_TTL}s (defined at module level)")

# ==========================================================
# STEP 5: IMPORT CHAT SERVICE AT MODULE LEVEL (No lazy loading)
# ==========================================================

print("CHECKPOINT 4 - IMPORTING SERVICES")
CHAT_SERVICE_AVAILABLE = False
try:
    from app.services.chat_service import ChatService
    print(f"✅ ChatService imported directly from module level")
    CHAT_SERVICE_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ ChatService import failed: {e}")
    traceback.print_exc()
except Exception as e:
    print(f"⚠️ Unexpected error importing ChatService: {e}")
    traceback.print_exc()

# Try to import psutil for memory diagnostics (optional)
try:
    import psutil
    PSUTIL_AVAILABLE = True
    print("✅ psutil available")
except ImportError:
    PSUTIL_AVAILABLE = False
    print("⚠️ psutil not available")

print("=" * 60)

# ==========================================================
# REGISTER WEBHOOK ROUTER
# ==========================================================

print("CHECKPOINT 5 - REGISTERING WEBHOOK ROUTER")
webhook_router = None
try:
    from app.routes.webhook import router as webhook_router
    print("✅ Webhook router imported successfully")
except Exception as e:
    print(f"❌ Webhook router import failed: {e}")
    traceback.print_exc()

# ==========================================================
# PRE-FLIGHT CHECK
# ==========================================================

def preflight_check() -> Dict[str, Any]:
    """Run pre-startup diagnostics before FastAPI starts"""
    results = {
        "status": "PASSED",
        "checks": {},
        "errors": [],
        "warnings": []
    }
    
    logger.info("=" * 60)
    logger.info("🔧 PRE-FLIGHT CHECK")
    logger.info("=" * 60)
    
    # Check Python version
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    results["checks"]["python_version"] = python_version
    if sys.version_info.major >= 3 and sys.version_info.minor >= 9:
        logger.info(f"   ├── Python {python_version} ✓")
    else:
        logger.error(f"   ├── Python {python_version} ✗ (3.9+ required)")
        results["errors"].append(f"Python {python_version} < 3.9")
        results["status"] = "FAILED"
    
    # Check required packages
    required_packages = ["fastapi", "sqlalchemy", "loguru", "cachetools", "slowapi"]
    for pkg in required_packages:
        try:
            importlib.import_module(pkg)
            logger.info(f"   ├── {pkg} ✓")
            results["checks"][pkg] = True
        except ImportError:
            logger.error(f"   ├── {pkg} ✗ (not installed)")
            results["errors"].append(f"Missing package: {pkg}")
            results["status"] = "FAILED"
            results["checks"][pkg] = False
    
    # Check environment variables
    required_envs = ["DATABASE_URL", "GROQ_API_KEY", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
    for env in required_envs:
        value = os.getenv(env)
        if value:
            logger.info(f"   ├── {env} ✓")
            results["checks"][env] = True
        else:
            logger.error(f"   ├── {env} ✗ (missing)")
            results["errors"].append(f"Missing environment variable: {env}")
            results["status"] = "FAILED"
            results["checks"][env] = False
    
    # Check directories
    directories = ["uploads", "templates"]
    for dir_name in directories:
        try:
            os.makedirs(dir_name, exist_ok=True)
            if os.access(dir_name, os.W_OK):
                logger.info(f"   ├── {dir_name}/ ✓ (writable)")
                results["checks"][f"{dir_name}_dir"] = True
            else:
                logger.warning(f"   ├── {dir_name}/ ⚠ (not writable)")
                results["warnings"].append(f"{dir_name} directory not writable")
                results["checks"][f"{dir_name}_dir"] = False
        except Exception as e:
            logger.error(f"   ├── {dir_name}/ ✗ ({e})")
            results["errors"].append(f"Cannot create {dir_name} directory")
            results["status"] = "FAILED"
    
    # Check database URL format
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://") or db_url.startswith("sqlite://"):
        logger.info(f"   ├── DATABASE_URL format ✓")
        results["checks"]["db_url_format"] = True
    else:
        logger.error(f"   ├── DATABASE_URL format ✗ (invalid)")
        results["errors"].append("DATABASE_URL has invalid format")
        results["status"] = "FAILED"
        results["checks"]["db_url_format"] = False
    
    logger.info("=" * 60)
    
    if results["status"] == "PASSED":
        logger.success("✅ PRE-FLIGHT CHECK: PASSED")
    else:
        logger.error("❌ PRE-FLIGHT CHECK: FAILED")
    
    logger.info("=" * 60)
    
    return results


# ==========================================================
# EXECUTE PRE-FLIGHT CHECK
# ==========================================================

preflight_result = preflight_check()
print(f"✅ PRE-FLIGHT RESULT: {preflight_result['status']}")


# ==========================================================
# INTEGRATED LIFESPAN HANDLER (with Webhook initialization)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Integrated lifespan - initializes webhook services and more"""
    print("=" * 60)
    print("🚀 LIFESPAN STARTED - INTEGRATED MODE")
    print("=" * 60)
    
    STARTUP_DIAGNOSTICS["startup_time"] = datetime.now().isoformat()
    start_time = time.time()
    
    try:
        logger.info("=" * 80)
        logger.info("🤖 AI WHATSAPP AGENT STARTING v14.0 (INTEGRATED MODE)")
        logger.info("=" * 80)
        
        # ====================================================
        # INTEGRATION: Initialize Webhook Services
        # This calls the webhook.py's initialize_services() function
        # ====================================================
        try:
            from app.routes.webhook import initialize_services, get_webhook_stats
            webhook_init_result = await initialize_services()
            logger.info(f"✅ Webhook services initialized: {webhook_init_result}")
            
            # Store webhook stats function in app state for later use
            app.state.get_webhook_stats = get_webhook_stats
        except Exception as e:
            logger.error(f"❌ Webhook services initialization failed: {e}")
            logger.exception(e)
            webhook_init_result = {"services_loaded": 0, "health": "unknown", "env_configured": False}
        
        # Create directories
        os.makedirs("uploads", exist_ok=True)
        TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        
        startup_duration = time.time() - start_time
        STARTUP_DIAGNOSTICS["startup_duration"] = startup_duration
        STARTUP_DIAGNOSTICS["status"] = "COMPLETED"
        
        logger.info("=" * 80)
        logger.info(f"✅ Application startup complete in {startup_duration:.2f}s (INTEGRATED MODE)")
        logger.info(f"   Webhook Services: {webhook_init_result.get('services_loaded', 0)} loaded")
        logger.info(f"   Webhook Health: {webhook_init_result.get('health', 'unknown')}")
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
            logger.critical(f"CRASH CODE: {location['code']}")
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location['function'],
                error_type=type(e).__name__,
                error=str(e),
                code=location.get('code'),
                crash_type=classify_crash(e)
            )
        
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)[:200]}")
        logger.critical("=" * 80)
        logger.critical("FULL TRACEBACK:")
        logger.critical(traceback.format_exc())
        
        write_crash_report(e, "lifespan")
        raise
    
    finally:
        logger.info("🛑 SHUTTING DOWN")
        if 'engine' in dir():
            engine.dispose()
        dashboard_cache.clear()
        ServiceRegistry.clear()


# ==========================================================
# CREATE FASTAPI APP WITH LIFESPAN
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="14.0.0",
    docs_url="/api/docs" if config.ENVIRONMENT != "production" else None,
    redoc_url="/api/redoc" if config.ENVIRONMENT != "production" else None,
    openapi_url="/api/openapi.json" if config.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)


# ==========================================================
# ==========================================================
# RAW ENDPOINT - NO MIDDLEWARE, NO DEPENDENCIES
# Place this FIRST to test if app can respond at all
# ==========================================================
# ==========================================================

@app.get("/raw-ping")
async def raw_ping():
    """ULTRA-SIMPLE endpoint - tests if app is alive at all"""
    print("🔔 /raw-ping HIT - APP IS RESPONDING!")
    return {"ping": "pong", "timestamp": datetime.now().isoformat(), "status": "alive"}


# ==========================================================
# DEBUG ENDPOINTS - Minimal dependencies
# ==========================================================

@app.get("/debug/ping")
async def debug_ping():
    """Simple ping test"""
    print("🔔 /debug/ping HIT")
    return {"ping": "pong", "timestamp": datetime.now().isoformat()}


@app.get("/debug/health")
async def debug_health():
    """Simple health check - no database"""
    print("🔔 /debug/health HIT")
    return {
        "status": "alive",
        "version": "14.0.0",
        "timestamp": datetime.now().isoformat(),
        "preflight": preflight_result["status"]
    }


@app.get("/debug/routes")
async def debug_routes():
    """List all registered routes"""
    print("🔔 /debug/routes HIT")
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "methods": list(route.methods) if hasattr(route, "methods") else []
        })
    return {
        "total_routes": len(routes),
        "routes": routes[:20],
        "webhook_registered": any("/webhook" in r["path"] for r in routes)
    }


@app.get("/debug/env")
async def debug_env():
    """Check environment configuration (safe, no secrets)"""
    print("🔔 /debug/env HIT")
    return {
        "environment": getattr(config, 'ENVIRONMENT', 'not set'),
        "database_configured": bool(os.getenv("DATABASE_URL")),
        "whatsapp_token_configured": bool(os.getenv("WHATSAPP_ACCESS_TOKEN")),
        "whatsapp_phone_id_configured": bool(os.getenv("WHATSAPP_PHONE_NUMBER_ID")),
        "groq_configured": bool(os.getenv("GROQ_API_KEY")),
        "cache_ttl": CACHE_TTL,
        "chat_service_available": CHAT_SERVICE_AVAILABLE,
        "webhook_router_available": webhook_router is not None
    }


# ==========================================================
# WEBHOOK INTEGRATION ENDPOINT (New)
# ==========================================================

@app.get("/webhook-stats")
async def webhook_integration_stats():
    """Get webhook integration statistics from webhook.py"""
    print("🔔 /webhook-stats HIT")
    if hasattr(app.state, 'get_webhook_stats'):
        stats = app.state.get_webhook_stats()
        return {
            "status": "ok",
            "integration": "100%",
            "webhook_version": "11.0",
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }
    return {
        "status": "degraded",
        "integration": "webhook stats not available",
        "message": "Webhook services may not be fully initialized"
    }


# ==========================================================
# SIMPLE ENDPOINTS
# ==========================================================

@app.get("/")
async def root():
    """Root endpoint"""
    print("🔔 / HIT")
    return {
        "status": "ok",
        "message": "AI WhatsApp Logistics Assistant is running",
        "version": "14.0.0",
        "debug_endpoints": ["/raw-ping", "/debug/ping", "/debug/health", "/debug/routes", "/debug/env", "/alive", "/health", "/webhook-stats"]
    }


@app.get("/alive")
async def alive():
    """Simple alive check"""
    print("🔔 /alive HIT")
    return {"alive": True, "timestamp": datetime.now().isoformat()}


@app.get("/ping")
async def ping():
    """Ping endpoint"""
    print("🔔 /ping HIT")
    return {"ping": "pong", "timestamp": datetime.now().isoformat()}


@app.get("/health")
async def health():
    """Health check endpoint"""
    print("🔔 /health HIT")
    return {
        "status": "healthy",
        "version": "14.0.0",
        "timestamp": datetime.now().isoformat(),
        "preflight": preflight_result["status"]
    }


@app.get("/liveness")
async def liveness():
    """Liveness probe"""
    print("🔔 /liveness HIT")
    return {"alive": True, "timestamp": datetime.now().isoformat()}


@app.get("/startup-check")
async def startup_check():
    """Startup verification endpoint"""
    print("🔔 /startup-check HIT")
    webhook_stats = {}
    if hasattr(app.state, 'get_webhook_stats'):
        webhook_stats = app.state.get_webhook_stats()
    
    return {
        "chat_service_available": CHAT_SERVICE_AVAILABLE,
        "environment": config.ENVIRONMENT,
        "cache_ttl": CACHE_TTL,
        "webhook_router_registered": webhook_router is not None,
        "webhook_stats": webhook_stats,
        "preflight_status": preflight_result["status"],
        "status": "running",
        "version": "14.0.0"
    }


# ==========================================================
# REGISTER WEBHOOK ROUTER (If available)
# ==========================================================

if webhook_router:
    app.include_router(webhook_router)
    logger.success("✅ Webhook router registered (v11.0 integrated)")
else:
    logger.error("❌ Webhook router not available")


# ==========================================================
# ==========================================================
# ALL MIDDLEWARE IS TEMPORARILY DISABLED
# This is to isolate if middleware is causing the crash
# ==========================================================
# ==========================================================

# TEMPORARILY DISABLED: Request logger middleware
# @app.middleware("http")
# async def request_logger(request: Request, call_next):
#     logger.info(f"📥 REQUEST: {request.method} {request.url.path}")
#     try:
#         response = await call_next(request)
#         logger.info(f"📤 RESPONSE: {request.method} {request.url.path} -> {response.status_code}")
#         return response
#     except Exception as e:
#         logger.exception(f"💥 CRASH: {request.method} {request.url.path} - {e}")
#         raise

# TEMPORARILY DISABLED: Runtime diagnostics middleware
# app.middleware("http")(runtime_diagnostics_middleware)

# TEMPORARILY DISABLED: Request ID middleware
# app.middleware("http")(add_request_id_middleware)

# TEMPORARILY DISABLED: Security headers middleware
# app.middleware("http")(add_security_headers_middleware)


# ==========================================================
# GLOBAL EXCEPTION HANDLER (To catch all errors)
# ==========================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler to catch all errors"""
    import traceback
    error_details = traceback.format_exc()
    print(f"💥 GLOBAL EXCEPTION HANDLER CAUGHT:")
    print(f"   Path: {request.method} {request.url.path}")
    print(f"   Error: {type(exc).__name__}: {exc}")
    print(f"   Traceback:\n{error_details}")
    
    logger.error(f"GLOBAL ERROR: {request.method} {request.url.path} - {exc}")
    logger.error(f"Traceback: {error_details}")
    
    return JSONResponse(
        status_code=500,
        content={
            "error": str(exc),
            "type": type(exc).__name__,
            "path": request.url.path,
            "method": request.method
        }
    )


# ==========================================================
# CORS CONFIGURATION (Kept - this is generally safe)
# ==========================================================

FRONTEND_URL = getattr(config, 'FRONTEND_URL', os.getenv("FRONTEND_URL", "http://localhost:3000"))

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


# ==========================================================
# TRUSTED HOST MIDDLEWARE - DISABLED (Blocks Railway)
# ==========================================================
# app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# ==========================================================
# RATE LIMITER (Kept - uses limiter, not middleware)
# ==========================================================

limiter = Limiter(key_func=get_remote_address, default_limits=["5 per second"])
limiter._app = app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ==========================================================
# ==========================================================
# ALL ORIGINAL ATTRIBUTES PRESERVED BELOW
# ==========================================================
# ==========================================================

# ==========================================================
# CRASH CLASSIFICATION
# ==========================================================

class CrashType:
    IMPORT_ERROR = "IMPORT_ERROR"
    CONFIG_ERROR = "CONFIG_ERROR"
    DATABASE_ERROR = "DATABASE_ERROR"
    ROUTER_ERROR = "ROUTER_ERROR"
    SERVICE_ERROR = "SERVICE_ERROR"
    AI_PROVIDER_ERROR = "AI_PROVIDER_ERROR"
    MEMORY_ERROR = "MEMORY_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


def classify_crash(exc: Exception) -> str:
    """Classify crash type based on exception"""
    error_type = type(exc).__name__
    error_msg = str(exc).lower()
    
    if "import" in error_type.lower() or "module" in error_msg:
        return CrashType.IMPORT_ERROR
    elif "config" in error_msg or "setting" in error_msg:
        return CrashType.CONFIG_ERROR
    elif "database" in error_msg or "sql" in error_msg or "postgres" in error_msg:
        return CrashType.DATABASE_ERROR
    elif "router" in error_msg:
        return CrashType.ROUTER_ERROR
    elif "ai" in error_msg or "provider" in error_msg or "groq" in error_msg or "openai" in error_msg:
        return CrashType.AI_PROVIDER_ERROR
    elif "memory" in error_msg or "out of memory" in error_msg:
        return CrashType.MEMORY_ERROR
    elif "timeout" in error_msg:
        return CrashType.TIMEOUT_ERROR
    elif "syntax" in error_type.lower():
        return CrashType.SYNTAX_ERROR
    elif "service" in error_msg:
        return CrashType.SERVICE_ERROR
    else:
        return CrashType.UNKNOWN_ERROR


# ==========================================================
# FILE RANKING SYSTEM
# ==========================================================

CRASH_SCORE = defaultdict(int)


def update_crash_score(file_path: str, score: int):
    """Update crash score for a file"""
    short_name = file_path.split("/")[-1] if "/" in file_path else file_path
    CRASH_SCORE[short_name] += score


def get_top_crash_files(limit: int = 5) -> List[Tuple[str, int]]:
    """Get top files most likely to have caused the crash"""
    return sorted(CRASH_SCORE.items(), key=lambda x: x[1], reverse=True)[:limit]


# ==========================================================
# IMPORT DEPENDENCY SCANNER
# ==========================================================

IMPORT_TREE = {}


def build_import_tree(module_name: str, depth: int = 0, visited: set = None) -> Dict[str, Any]:
    """Build import dependency tree for a module"""
    if visited is None:
        visited = set()
    
    if module_name in visited:
        return {"name": module_name, "circular": True, "children": []}
    
    visited.add(module_name)
    
    tree = {
        "name": module_name,
        "children": [],
        "circular": False
    }
    
    try:
        if module_name in sys.modules:
            module = sys.modules[module_name]
        else:
            module = importlib.import_module(module_name)
        
        # Get imported modules
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if hasattr(attr, "__module__"):
                child_name = attr.__module__
                if child_name and child_name.startswith("app.") and child_name != module_name:
                    child_tree = build_import_tree(child_name, depth + 1, visited.copy())
                    tree["children"].append(child_tree)
    except Exception:
        pass
    
    return tree


def print_import_tree(module_name: str, indent: str = ""):
    """Print import tree for visualization"""
    tree = build_import_tree(module_name)
    
    logger.info(f"{indent}├── {module_name}")
    
    for child in tree["children"][:5]:
        if child["circular"]:
            logger.info(f"{indent}│   └── {child['name']} (circular)")
        else:
            print_import_tree(child["name"], indent + "│   ")


# ==========================================================
# CONSTRUCTOR STEP-BY-STEP TRACKING
# ==========================================================

class ConstructorTracker:
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.steps = []
        self.current_step = 0
        self.start_time = time.time()
        self.failed_step = None
    
    def step(self, step_name: str):
        self.current_step += 1
        self.steps.append({
            "step": self.current_step,
            "name": step_name,
            "timestamp": datetime.now().isoformat()
        })
        logger.info(f"   🔧 {self.service_name} - STEP {self.current_step}: {step_name}")
    
    def complete(self):
        elapsed = time.time() - self.start_time
        logger.success(f"   ✅ {self.service_name} constructed in {elapsed:.2f}s")
        return elapsed
    
    def fail(self, step_name: str, error: Exception):
        self.failed_step = {
            "step": self.current_step + 1,
            "name": step_name,
            "error": str(error),
            "error_type": type(error).__name__
        }
        logger.error(f"   ❌ {self.service_name} FAILED at STEP {self.current_step + 1}: {step_name}")
        return self.failed_step


# ==========================================================
# RUNTIME DIAGNOSTICS (Preserved but disabled)
# ==========================================================

LAST_REQUEST_ERROR = None


# ==========================================================
# CRASH LOCATION
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
                "code": frame.line[:200] if frame.line else "Unknown"
            }
    
    if tb:
        last_frame = tb[-1]
        return {
            "file": last_frame.filename,
            "line": last_frame.lineno,
            "function": last_frame.name,
            "code": last_frame.line[:200] if last_frame.line else "Unknown"
        }
    
    return None


def full_crash_analysis(exc: Exception, max_frames: int = 10) -> List[Dict[str, Any]]:
    frames = traceback.extract_tb(exc.__traceback__)
    analysis = []
    
    for frame in frames[:max_frames]:
        analysis.append({
            "file": frame.filename,
            "line": frame.lineno,
            "function": frame.name,
            "code": frame.line[:100] if frame.line else "Unknown"
        })
    
    return analysis


# ==========================================================
# ROOT CAUSE STORAGE
# ==========================================================

_ROOT_CAUSE = None
_FAILED_MODULES = []
_FAILED_SERVICES = []
_IMPORT_CHAIN = []
_CONSTRUCTOR_CHAIN = []


def set_root_cause(
    file: str, 
    line: int, 
    function: str, 
    error_type: str, 
    error: str, 
    code: str = None,
    module: str = None, 
    service: str = None,
    crash_type: str = None
):
    global _ROOT_CAUSE
    _ROOT_CAUSE = {
        "crash_file": file,
        "crash_line": line,
        "crash_function": function,
        "crash_code": code or "Unknown",
        "error_type": error_type,
        "error_message": str(error)[:500],
        "crash_type": crash_type or classify_crash(Exception(error)),
        "module": module,
        "service": service,
        "failed_modules": _FAILED_MODULES.copy(),
        "failed_services": _FAILED_SERVICES.copy(),
        "import_chain": _IMPORT_CHAIN.copy(),
        "constructor_chain": _CONSTRUCTOR_CHAIN.copy(),
        "environment": os.getenv("ENVIRONMENT", "unknown"),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "timestamp": datetime.now().isoformat()
    }
    
    update_crash_score(file, 100)
    for mod in _FAILED_MODULES:
        update_crash_score(mod, 50)
    for svc in _FAILED_SERVICES:
        update_crash_score(svc, 75)


def get_root_cause() -> Optional[Dict[str, Any]]:
    return _ROOT_CAUSE


# ==========================================================
# CRASH HISTORY
# ==========================================================

MAX_CRASH_HISTORY = 100
CRASH_HISTORY = []


def add_to_crash_history(crash_data: Dict[str, Any]):
    CRASH_HISTORY.append(crash_data)
    while len(CRASH_HISTORY) > MAX_CRASH_HISTORY:
        CRASH_HISTORY.pop(0)


def write_crash_report(exc: Exception, stage: str = "unknown"):
    location = crash_location(exc)
    crash_type = classify_crash(exc)
    full_analysis = full_crash_analysis(exc, max_frames=5)
    
    crash_data = {
        "stage": stage,
        "timestamp": datetime.now().isoformat(),
        "crash_type": crash_type,
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:200],
        "crash_file": location["file"] if location else "Unknown",
        "crash_line": location["line"] if location else "Unknown",
        "crash_function": location["function"] if location else "Unknown",
        "crash_code": location["code"] if location else "Unknown",
        "crash_path": full_analysis,
        "failed_modules": _FAILED_MODULES.copy(),
        "failed_services": _FAILED_SERVICES.copy()
    }
    
    if location:
        set_root_cause(
            file=location["file"],
            line=location["line"],
            function=location["function"],
            error_type=type(exc).__name__,
            error=str(exc),
            code=location.get("code"),
            crash_type=crash_type
        )
    
    add_to_crash_history(crash_data)
    update_crash_score(location["file"] if location else "unknown", 100)
    
    try:
        with open("/tmp/startup_crash.json", "w") as f:
            json.dump(crash_data, f, indent=2)
    except Exception:
        pass


# ==========================================================
# MODULE FINGERPRINTING
# ==========================================================

MODULE_FINGERPRINTS = {}


def update_module_fingerprint(module_name: str, status: str, import_time: float = None, constructor_time: float = None, error: str = None):
    MODULE_FINGERPRINTS[module_name] = {
        "module": module_name,
        "loaded": status == "SUCCESS",
        "status": status,
        "import_time": import_time,
        "constructor_time": constructor_time,
        "error": error[:200] if error else None,
        "timestamp": datetime.now().isoformat()
    }


# ==========================================================
# ENHANCED IMPORT DIAGNOSTICS
# ==========================================================

def diagnose_import(module_name: str, use_cache: bool = True):
    if use_cache and module_name in sys.modules:
        logger.info(f"📦 USING CACHED: {module_name}")
        update_module_fingerprint(module_name, "CACHED", 0)
        return sys.modules[module_name]
    
    logger.info("=" * 80)
    logger.info(f"📦 IMPORTING: {module_name}")
    _IMPORT_CHAIN.append(module_name)
    
    import_start = time.time()
    
    try:
        module = importlib.import_module(module_name)
        import_duration = time.time() - import_start
        
        logger.success(f"✅ SUCCESS: {module_name} ({import_duration:.3f}s)")
        update_module_fingerprint(module_name, "SUCCESS", import_duration)
        return module
        
    except Exception as e:
        import_duration = time.time() - import_start
        location = crash_location(e)
        
        _FAILED_MODULES.append(module_name)
        
        logger.critical("=" * 80)
        logger.critical(f"❌ FAILED MODULE: {module_name}")
        logger.critical(f"   ERROR: {type(e).__name__}: {str(e)[:200]}")
        
        if location:
            logger.critical(f"   CRASH FILE: {location['file']}")
            logger.critical(f"   CRASH LINE: {location['line']}")
        
        update_module_fingerprint(module_name, "FAILED", import_duration, error=str(e))
        write_crash_report(e, f"import_{module_name}")
        raise


# ==========================================================
# ENHANCED CONSTRUCTOR DIAGNOSTICS
# ==========================================================

def diagnose_constructor(service_name: str, constructor_func, *args, **kwargs):
    tracker = ConstructorTracker(service_name)
    _CONSTRUCTOR_CHAIN.append(service_name)
    
    start = time.time()
    
    try:
        tracker.step("Initializing...")
        result = constructor_func(*args, **kwargs)
        
        tracker.step("Configuration validation...")
        elapsed = tracker.complete()
        
        update_module_fingerprint(f"constructor_{service_name}", "SUCCESS", constructor_time=elapsed)
        return result
        
    except Exception as e:
        location = crash_location(e)
        _FAILED_SERVICES.append(service_name)
        
        tracker.fail("Failed", e)
        
        logger.critical(f"❌ CONSTRUCTOR FAILED: {service_name}")
        if location:
            logger.critical(f"   CRASH FILE: {location['file']}")
            logger.critical(f"   CRASH LINE: {location['line']}")
        
        update_module_fingerprint(f"constructor_{service_name}", "FAILED", error=str(e))
        write_crash_report(e, f"constructor_{service_name}")
        raise


# ==========================================================
# SERVICE FILES TO DIAGNOSE (Preserved for later)
# ==========================================================

ALL_FILES_TO_DIAGNOSE = [
    "app.services.ai_provider_service",
    "app.services.ai_query_service",
    "app.services.analytics_service",
    "app.services.kpi_service",
    "app.services.logistics_query_service",
    "app.services.schema_service",
    "app.services.whatsapp_service",
    "app.routes.upload",
    "app.routes.admin",
    "app.routes.health",
    "app.routes.logistics",
]

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
# THREAD-SAFE METRICS
# ==========================================================

class ThreadSafeMetrics:
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
    _services = {}
    _routes = {}
    
    @classmethod
    def register_service(cls, name: str, service):
        cls._services[name] = service
    
    @classmethod
    def get_service(cls, name: str):
        return cls._services.get(name)
    
    @classmethod
    def clear(cls):
        cls._services.clear()
        cls._routes.clear()


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def diagnose_service(service_name: str, func, *args, **kwargs):
    import time
    start = time.time()
    try:
        logger.info(f"🚀 STARTING: {service_name}")
        result = func(*args, **kwargs)
        elapsed = round(time.time() - start, 2)
        logger.success(f"✅ {service_name} loaded in {elapsed}s")
        return result
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        location = crash_location(e)
        logger.error(f"❌ {service_name} FAILED after {elapsed}s: {e}")
        if location:
            logger.error(f"   Location: {location['file']}:{location['line']}")
        raise


def print_dependency_tree():
    tree = """
╔══════════════════════════════════════════════════════════════════╗
║                      DEPENDENCY TREE                             ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  main.py                                                         ║
║   ├── database.py (✅ IMPORTED AT MODULE LEVEL)                 ║
║   ├── config.py                                                  ║
║   ├── models.py                                                  ║
║   │                                                              ║
║   ├── routes/                                                    ║
║   │    ├── webhook.py (✅ REGISTERED - v11.0 INTEGRATED)        ║
║   │    ├── upload.py                                             ║
║   │    ├── admin.py                                              ║
║   │    ├── health.py                                             ║
║   │    └── logistics.py                                          ║
║   │                                                              ║
║   └── services/                                                  ║
║        ├── ai_provider_service.py                                ║
║        ├── ai_query_service.py                                   ║
║        ├── analytics_service.py                                  ║
║        ├── chat_service.py (✅ IMPORTED AT MODULE LEVEL)        ║
║        ├── kpi_service.py                                        ║
║        ├── logistics_query_service.py                            ║
║        ├── schema_service.py                                     ║
║        └── whatsapp_service.py                                   ║
║                                                                  ║
╠══════════════════════════════════════════════════════════════════╣
║  CRITICAL FIXES v14.0:                                           ║
║  ✅ FULL INTEGRATION with webhook.py v11.0                       ║
║  ✅ Webhook services initialized in lifespan                     ║
║  ✅ Webhook stats endpoint (/webhook-stats)                      ║
║  ✅ preflight_result defined BEFORE use                          ║
║  ✅ app = FastAPI() created BEFORE any decorators                ║
║  ✅ All problematic middleware DISABLED                          ║
║  ✅ RAW endpoint (/raw-ping) - NO dependencies                   ║
║  ✅ Debug endpoints added (/debug/*)                             ║
║  ✅ Global exception handler added                               ║
╚══════════════════════════════════════════════════════════════════╝
"""
    logger.info(tree)


# ==========================================================
# ADDITIONAL MIDDLEWARE (All disabled for debugging)
# ==========================================================

async def add_request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    start_time = time.time()
    active_requests.inc()
    
    with logger.contextualize(request_id=request_id):
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            request_metrics.update(request.url.path, response.status_code, duration_ms)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = str(int(duration_ms))
            active_requests.dec()
            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            request_metrics.update(request.url.path, 500, duration_ms)
            logger.error(f"Request failed: {e}")
            active_requests.dec()
            raise


async def add_security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


def safe_error_response(request_id: str, error_type: str = "internal_error") -> Dict[str, Any]:
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
# CACHE
# ==========================================================

dashboard_cache = TTLCache(maxsize=100, ttl=CACHE_TTL)


# ==========================================================
# DIAGNOSTICS ENDPOINTS
# ==========================================================

@app.get("/root-cause", tags=["Diagnostics"])
async def get_root_cause_endpoint():
    root_cause = get_root_cause()
    if root_cause:
        return root_cause
    return {"status": "NO_CRASH", "message": "No crash detected"}


@app.get("/railway-diagnostics", tags=["Diagnostics"])
async def railway_diagnostics():
    mem_info = None
    if PSUTIL_AVAILABLE:
        mem_info = {
            "memory_used_mb": round(psutil.Process().memory_info().rss / (1024 * 1024), 1),
            "memory_percent": psutil.virtual_memory().percent,
            "cpu_percent": psutil.cpu_percent(interval=0.1)
        }
    
    return {
        "startup_status": STARTUP_DIAGNOSTICS["status"],
        "startup_duration": STARTUP_DIAGNOSTICS["startup_duration"],
        "memory": mem_info,
        "failed_modules": _FAILED_MODULES,
        "failed_services": _FAILED_SERVICES,
        "top_crash_files": get_top_crash_files(5),
        "root_cause": get_root_cause(),
        "python_version": sys.version,
        "environment": config.ENVIRONMENT,
        "chat_service_available": CHAT_SERVICE_AVAILABLE
    }


@app.get("/module-health", tags=["Diagnostics"])
async def module_health():
    return {
        "modules": MODULE_FINGERPRINTS,
        "total_modules": len(MODULE_FINGERPRINTS),
        "failed_modules": [k for k, v in MODULE_FINGERPRINTS.items() if v.get("status") == "FAILED"],
        "failed_count": len([v for v in MODULE_FINGERPRINTS.values() if v.get("status") == "FAILED"]),
        "timestamp": datetime.now().isoformat()
    }


@app.get("/diagnostics", tags=["Diagnostics"])
async def get_diagnostics():
    return STARTUP_DIAGNOSTICS


@app.get("/crash-history", tags=["Diagnostics"])
async def get_crash_history():
    return {"crash_count": len(CRASH_HISTORY), "crashes": CRASH_HISTORY[-10:]}


@app.get("/last-error", tags=["Diagnostics"])
async def get_last_error():
    if LAST_REQUEST_ERROR:
        return LAST_REQUEST_ERROR
    return {"status": "NO_ERRORS"}


@app.get("/crash-classification", tags=["Diagnostics"])
async def crash_classification():
    classification_counts = defaultdict(int)
    for crash in CRASH_HISTORY:
        classification_counts[crash.get("crash_type", CrashType.UNKNOWN_ERROR)] += 1
    
    return {
        "classifications": dict(classification_counts),
        "top_files": get_top_crash_files(10),
        "root_cause": get_root_cause()
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
# CHAT ENDPOINT (Disabled for isolation test)
# ==========================================================

@app.get("/chat-status", tags=["Chat"])
async def chat_status():
    """Returns chat service status"""
    return {
        "status": "chat_endpoint_disabled_for_testing",
        "chat_service_available": CHAT_SERVICE_AVAILABLE,
        "message": "If you see this, the app started successfully."
    }


# ==========================================================
# CRASH TEST ENDPOINT
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/test-crash")
    async def test_crash():
        raise RuntimeError("This is a test crash - check logs for full traceback")


# ==========================================================
# ENTRY POINT
# ==========================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"🚀 Starting uvicorn on {host}:{port}")
    uvicorn.run("app.main:app", host=host, port=port, reload=config.DEBUG, log_level="info")


# ==========================================================
# INITIALIZATION LOG (Now safe)
# ==========================================================

try:
    logger.info("=" * 60)
    logger.info("📡 MAIN APP v14.0 - FULLY INTEGRATED")
    logger.info("")
    logger.info("   CRITICAL FIXES IN v14.0:")
    logger.info("   🔧 FULL INTEGRATION with webhook.py v11.0")
    logger.info("   🔧 Webhook services initialized in lifespan")
    logger.info("   🔧 Webhook stats endpoint (/webhook-stats)")
    logger.info("   🔧 preflight_result defined BEFORE use")
    logger.info("   🔧 app = FastAPI() created BEFORE any decorators")
    logger.info("   🔧 All problematic middleware DISABLED")
    logger.info("   🔧 RAW endpoint (/raw-ping) - NO dependencies")
    logger.info("   🔧 Debug endpoints (/debug/*)")
    logger.info("   🔧 Global exception handler")
    logger.info("")
    logger.info(f"   PRE-FLIGHT: {preflight_result['status']}")
    logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
    logger.info(f"   CHAT_SERVICE_AVAILABLE: {CHAT_SERVICE_AVAILABLE}")
    logger.info(f"   WEBHOOK_ROUTER_REGISTERED: {webhook_router is not None}")
    logger.info("")
    logger.info("   🔍 TEST ENDPOINTS (in order):")
    logger.info("   1. GET /raw-ping - ULTRA SIMPLE (NO middleware)")
    logger.info("   2. GET /debug/ping - Simple ping")
    logger.info("   3. GET /debug/health - Health check")
    logger.info("   4. GET /alive - Basic alive")
    logger.info("   5. GET /health - Full health")
    logger.info("   6. GET /webhook-stats - Webhook integration status")
    logger.info("   7. GET /webhook/self-test - Webhook self test")
    logger.info("=" * 60)
except Exception as init_error:
    logger.critical("=" * 80)
    logger.critical("💥 INITIALIZATION LOG ERROR")
    logger.critical("=" * 80)
    logger.critical(f"ERROR: {type(init_error).__name__}: {init_error}")
    logger.critical(traceback.format_exc())
    raise
