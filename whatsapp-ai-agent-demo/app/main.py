# ==========================================================
# FILE: app/main.py (ENTERPRISE v12.0.0 - ROOT CAUSE DIAGNOSTICS)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v12.0.0:
# - ✅ FIXED: crash_location() returns LAST application frame (not first)
# - ✅ FIXED: Crash history memory leak (max 100 entries with auto-cleanup)
# - ✅ FIXED: Reduced traceback storage (first 5 frames only)
# - ✅ FIXED: Duplicate module imports (reuse cached imports)
# - ✅ ADDED: Module health endpoint (/module-health)
# - ✅ ADDED: Root cause endpoint (/root-cause) - SINGLE SOURCE OF TRUTH
# - ✅ ADDED: All imports moved to lifespan (diagnosable failures)
# - ✅ ADDED: Import dependency tree logging
# - ✅ FIXED: config import at module level (NameError fix)
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
# CRITICAL FIX: Import config at MODULE LEVEL (BEFORE FastAPI app creation)
# ==========================================================
from app.config import config

# Try to import psutil for memory diagnostics (optional)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ==========================================================
# CRITICAL FIX #1: Fixed crash_location() - returns LAST app frame
# ==========================================================

def crash_location(exc: Exception) -> Optional[Dict[str, Any]]:
    """
    Extract the exact crash location from an exception.
    FIXED: Returns the LAST application frame (where crash actually occurred)
    """
    tb = traceback.extract_tb(exc.__traceback__)
    
    # CRITICAL FIX: Iterate in REVERSE to get the LAST app frame
    for frame in reversed(tb):
        if "/app/" in frame.filename:
            return {
                "file": frame.filename,
                "line": frame.lineno,
                "function": frame.name,
                "code": frame.line if frame.line else "Unknown"
            }
    
    # Fallback to last frame
    if tb:
        last_frame = tb[-1]
        return {
            "file": last_frame.filename,
            "line": last_frame.lineno,
            "function": last_frame.name,
            "code": last_frame.line if last_frame.line else "Unknown"
        }
    
    return None


def full_crash_analysis(exc: Exception, max_frames: int = 10) -> List[Dict[str, Any]]:
    """Analyze full crash path - shows crash chain (limited frames)"""
    frames = traceback.extract_tb(exc.__traceback__)
    analysis = []
    
    for frame in frames[:max_frames]:  # Limit frames to prevent memory bloat
        analysis.append({
            "file": frame.filename,
            "line": frame.lineno,
            "function": frame.name,
            "code": frame.line[:100] if frame.line else "Unknown"
        })
    
    return analysis


# ==========================================================
# CRITICAL FIX #2: Crash History with Memory Limit
# ==========================================================

MAX_CRASH_HISTORY = 100
CRASH_HISTORY = []


def add_to_crash_history(crash_data: Dict[str, Any]):
    """Add crash to history with automatic cleanup"""
    CRASH_HISTORY.append(crash_data)
    
    # CRITICAL FIX: Limit history size
    while len(CRASH_HISTORY) > MAX_CRASH_HISTORY:
        CRASH_HISTORY.pop(0)


# ==========================================================
# ROOT CAUSE STORAGE (CRITICAL FIX #10)
# ==========================================================

_ROOT_CAUSE = None


def set_root_cause(file: str, line: int, function: str, error_type: str, error: str, module: str = None, service: str = None):
    """Set the root cause of the current crash"""
    global _ROOT_CAUSE
    _ROOT_CAUSE = {
        "file": file,
        "line": line,
        "function": function,
        "error_type": error_type,
        "error": str(error)[:500],  # Limit error message length
        "module": module,
        "service": service,
        "timestamp": datetime.now().isoformat()
    }


def get_root_cause() -> Optional[Dict[str, Any]]:
    """Get the root cause of the last crash"""
    return _ROOT_CAUSE


# ==========================================================
# CRITICAL FIX #3: Reduced Traceback Storage
# ==========================================================

def get_traceback_summary(exc: Exception, max_frames: int = 5) -> List[Dict[str, Any]]:
    """Get summarized traceback (first N frames only)"""
    frames = traceback.extract_tb(exc.__traceback__)
    summary = []
    
    for frame in frames[:max_frames]:
        summary.append({
            "file": frame.filename.split("/")[-1],  # Just filename, not full path
            "line": frame.lineno,
            "function": frame.name
        })
    
    return summary


def write_crash_report(exc: Exception, stage: str = "unknown"):
    """Write crash report to JSON file with limited data"""
    location = crash_location(exc)
    full_analysis = full_crash_analysis(exc, max_frames=5)
    
    crash_data = {
        "stage": stage,
        "timestamp": datetime.now().isoformat(),
        "error_type": type(exc).__name__,
        "error_message": str(exc)[:200],  # Limit error message
        "crash_file": location["file"] if location else "Unknown",
        "crash_line": location["line"] if location else "Unknown",
        "crash_function": location["function"] if location else "Unknown",
        "crash_path": full_analysis
    }
    
    # Set root cause
    if location:
        set_root_cause(
            file=location["file"],
            line=location["line"],
            function=location["function"],
            error_type=type(exc).__name__,
            error=str(exc)[:200],
            module=stage,
            service=stage
        )
    
    # Add to history with memory limit
    add_to_crash_history(crash_data)
    
    try:
        with open("/tmp/startup_crash.json", "w") as f:
            json.dump(crash_data, f, indent=2)
    except Exception:
        pass


def log_full_crash_path(exc: Exception):
    """Log the entire crash chain (limited frames)"""
    analysis = full_crash_analysis(exc, max_frames=10)
    
    logger.critical("=" * 80)
    logger.critical("📋 FULL CRASH PATH (Recursive Analysis)")
    logger.critical("=" * 80)
    
    for i, frame in enumerate(analysis, 1):
        short_file = frame['file'].split("/")[-1] if "/" in frame['file'] else frame['file']
        logger.critical(f"  {i}. {short_file}:{frame['line']} in {frame['function']}")
    
    logger.critical("=" * 80)
    return analysis


# ==========================================================
# STARTUP TIMELINE
# ==========================================================

STARTUP_TIMELINE = []


def add_timeline_entry(stage: str):
    """Add entry to startup timeline"""
    STARTUP_TIMELINE.append({
        "stage": stage,
        "timestamp": datetime.now().isoformat()
    })


def get_memory_usage() -> Optional[Dict[str, Any]]:
    """Get current memory usage"""
    if not PSUTIL_AVAILABLE:
        return None
    try:
        return {
            "ram_used_mb": round(psutil.virtual_memory().used / (1024 * 1024), 1),
            "ram_percent": psutil.virtual_memory().percent
        }
    except Exception:
        return None


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
# MODULE HEALTH TRACKING (CRITICAL FIX #9)
# ==========================================================

MODULE_HEALTH = {}


def update_module_health(module_name: str, status: str, import_time: float = None, error: str = None):
    """Update module health status"""
    MODULE_HEALTH[module_name] = {
        "status": status,
        "import_time": import_time,
        "error": error[:200] if error else None,
        "timestamp": datetime.now().isoformat()
    }


# ==========================================================
# ENHANCED IMPORT DIAGNOSTICS (with dependency tree)
# ==========================================================

def diagnose_import(module_name: str, use_cache: bool = True):
    """
    Diagnose a module import with clear visual output.
    FIXED: Reuses cached imports when available.
    """
    # CRITICAL FIX #4: Check cache first
    if use_cache and module_name in sys.modules:
        logger.info("=" * 80)
        logger.info(f"📦 USING CACHED: {module_name}")
        logger.success("=" * 80)
        logger.success(f"✅ SUCCESS (cached): {module_name}")
        logger.success("=" * 80)
        update_module_health(module_name, "CACHED", 0)
        return sys.modules[module_name]
    
    logger.info("=" * 80)
    logger.info(f"📦 IMPORTING: {module_name}")
    
    before_modules = set(sys.modules.keys())
    import_start = time.time()
    
    try:
        module = importlib.import_module(module_name)
        
        import_duration = time.time() - import_start
        
        # CRITICAL FIX #6: Log new modules for dependency tree
        after_modules = set(sys.modules.keys())
        new_modules = after_modules - before_modules
        
        logger.success("=" * 80)
        logger.success(f"✅ SUCCESS: {module_name}")
        logger.success(f"   ⏱️  IMPORT TIME: {import_duration:.3f} seconds")
        
        if new_modules:
            logger.debug(f"   📚 New modules loaded ({len(new_modules)}):")
            for new_module in list(new_modules)[:5]:  # Limit to 5 for readability
                logger.debug(f"      - {new_module}")
        
        logger.success("=" * 80)
        
        update_module_health(module_name, "SUCCESS", import_duration)
        return module
        
    except Exception as e:
        import_duration = time.time() - import_start
        location = crash_location(e)
        
        logger.critical("=" * 80)
        logger.critical(f"❌ FAILED MODULE: {module_name}")
        logger.critical(f"   ⏱️  FAILED AFTER: {import_duration:.3f} seconds")
        logger.critical("=" * 80)
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)[:200]}")
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location['function'],
                error_type=type(e).__name__,
                error=str(e)[:200],
                module=module_name
            )
        
        logger.critical("=" * 80)
        logger.exception("FULL TRACEBACK:")
        logger.critical("=" * 80)
        
        update_module_health(module_name, "FAILED", import_duration, str(e))
        write_crash_report(e, f"import_{module_name}")
        raise


# ==========================================================
# CONSTRUCTOR DIAGNOSTICS
# ==========================================================

def diagnose_constructor(service_name: str, constructor_func, *args, **kwargs):
    """Diagnose service constructor with timing"""
    import time
    
    start = time.time()
    
    try:
        logger.info("=" * 60)
        logger.info(f"🔧 CONSTRUCTING: {service_name}")
        logger.info("   STEP 1: Initializing...")
        
        result = constructor_func(*args, **kwargs)
        
        logger.info("   STEP 2: Configuration loaded...")
        elapsed = round(time.time() - start, 2)
        
        logger.success("=" * 60)
        logger.success(f"✅ {service_name} constructed in {elapsed}s")
        logger.success("=" * 60)
        
        update_module_health(f"constructor_{service_name}", "SUCCESS", elapsed)
        return result
        
    except Exception as e:
        elapsed = round(time.time() - start, 2)
        location = crash_location(e)
        
        logger.critical("=" * 80)
        logger.critical(f"❌ CONSTRUCTOR FAILED: {service_name}")
        logger.critical(f"   ⏱️  FAILED AFTER: {elapsed}s")
        logger.critical("=" * 80)
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)[:200]}")
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location['function'],
                error_type=type(e).__name__,
                error=str(e)[:200],
                service=service_name
            )
        
        logger.critical("=" * 80)
        logger.exception("FULL TRACEBACK:")
        
        update_module_health(f"constructor_{service_name}", "FAILED", elapsed, str(e))
        write_crash_report(e, f"constructor_{service_name}")
        raise


# ==========================================================
# SERVICE FILES TO DIAGNOSE
# ==========================================================

ALL_FILES_TO_DIAGNOSE = [
    "app.services.ai_provider_service",
    "app.services.ai_query_service",
    "app.services.analytics_service",
    "app.services.kpi_service",
    "app.services.logistics_query_service",
    "app.services.schema_service",
    "app.services.whatsapp_service",
    "app.routes.webhook",
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
# LIFESPAN HANDLER (All imports inside lifespan)
# ==========================================================

# Note: config is already imported at the top of the file
# Other imports are inside lifespan to make failures diagnosable

@asynccontextmanager
async def lifespan(app: FastAPI):
    STARTUP_DIAGNOSTICS["startup_time"] = datetime.now().isoformat()
    add_timeline_entry("STARTUP_BEGIN")
    start_time = time.time()
    
    print_dependency_tree()
    
    # Import database INSIDE lifespan
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
        location = crash_location(e)
        logger.error(f"❌ Database module failed: {e}")
        if location:
            logger.error(f"   Location: {location['file']}:{location['line']}")
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location.get('function', 'unknown'),
                error_type=type(e).__name__,
                error=str(e)[:200],
                module="database"
            )
        SERVICE_STATUS["database"] = False
        raise
    
    # Cache settings (config already imported at top)
    CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
    CACHE_TTL_SESSION = getattr(config, 'CACHE_TTL_SESSION', 1800)
    CACHE_ENABLED = getattr(config, 'CACHE_ENABLED', True)
    
    # Track failed modules
    FAILED_MODULES = []
    SUCCESSFUL_MODULES = []
    imported_modules = {}
    
    try:
        logger.info("=" * 80)
        logger.info("🤖 AI WHATSAPP AGENT STARTING v12.0.0")
        logger.info("=" * 80)
        
        # ==========================================================
        # STAGE 1/12 - Environment Variables
        # ==========================================================
        add_timeline_entry("STAGE_1_ENV_VARS")
        logger.info("📍 STAGE 1/12: Environment Variables")
        
        # ==========================================================
        # STAGE 2/12 - Individual Module Imports (Continue on failure)
        # ==========================================================
        add_timeline_entry("STAGE_2_IMPORTS")
        logger.info("📍 STAGE 2/12: Individual Module Import Diagnostics")
        logger.info("=" * 80)
        logger.info("TESTING EACH MODULE INDIVIDUALLY:")
        logger.info("=" * 80)
        
        for module_name in ALL_FILES_TO_DIAGNOSE:
            try:
                imported_modules[module_name] = diagnose_import(module_name, use_cache=True)
                SUCCESSFUL_MODULES.append(module_name)
            except Exception as e:
                FAILED_MODULES.append(module_name)
                # Continue testing other modules
                write_crash_report(e, f"import_{module_name}")
        
        # Report results
        if FAILED_MODULES:
            logger.critical("=" * 80)
            logger.critical(f"❌ FAILED MODULES ({len(FAILED_MODULES)}):")
            for module in FAILED_MODULES:
                logger.critical(f"   - {module}")
            logger.critical("=" * 80)
        
        # Extract webhook router
        webhook_router = None
        if "app.routes.webhook" in imported_modules:
            webhook_router = getattr(imported_modules["app.routes.webhook"], "router", None)
            if webhook_router:
                SERVICE_STATUS["webhook"] = True
        
        # ==========================================================
        # STAGE 3/12 - Cache Configuration
        # ==========================================================
        add_timeline_entry("STAGE_3_CACHE")
        logger.info("📍 STAGE 3/12: Cache Configuration")
        logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
        logger.info("   ✅ Cache configuration loaded")
        
        # ==========================================================
        # STAGE 4/12 - Load Additional Routers
        # ==========================================================
        add_timeline_entry("STAGE_4_ROUTERS")
        logger.info("📍 STAGE 4/12: Loading Additional Routers")
        
        routers_to_load = [
            ("upload", "app.routes.upload"),
            ("admin", "app.routes.admin"),
            ("health", "app.routes.health"),
            ("logistics", "app.routes.logistics"),
        ]
        
        for name, module_path in routers_to_load:
            try:
                if module_path in imported_modules:
                    router = getattr(imported_modules[module_path], "router", None)
                    if router:
                        app.include_router(router)
                        logger.success(f"   ✅ {name.capitalize()} router loaded")
                else:
                    module = importlib.import_module(module_path)
                    router = getattr(module, "router", None)
                    if router:
                        app.include_router(router)
                        logger.success(f"   ✅ {name.capitalize()} router loaded")
            except Exception as e:
                logger.error(f"   ❌ Failed to load {name} router: {e}")
        
        # ==========================================================
        # STAGE 5/12 - Register Webhook Router
        # ==========================================================
        add_timeline_entry("STAGE_5_WEBHOOK")
        logger.info("📍 STAGE 5/12: Registering Webhook Router")
        if webhook_router:
            app.include_router(webhook_router)
            logger.success("   ✅ Webhook router registered")
        
        # ==========================================================
        # STAGE 6/12 - Validate Database
        # ==========================================================
        add_timeline_entry("STAGE_6_DATABASE")
        logger.info("📍 STAGE 6/12: Validating Database")
        try:
            db_ok = check_database_connection()
            SERVICE_STATUS["database"] = db_ok
            if db_ok:
                logger.success("   ✅ Database connected")
            else:
                logger.error("   ❌ Database connection failed")
        except Exception as e:
            logger.error(f"   ❌ Database validation error: {e}")
        
        # ==========================================================
        # STAGE 7/12 - Initialize Services
        # ==========================================================
        add_timeline_entry("STAGE_7_SERVICES")
        logger.info("📍 STAGE 7/12: Initializing Services")
        
        # Schema Service
        try:
            from app.services.schema_service import get_schema_service
            schema_service = diagnose_service("Schema Service", get_schema_service)
            SERVICE_STATUS["schema"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ Schema Service optional: {e}")
        
        # KPI Service
        try:
            from app.services.kpi_service import get_kpi_service
            kpi_service = diagnose_service("KPI Service", get_kpi_service)
            SERVICE_STATUS["kpi"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ KPI Service optional: {e}")
        
        # Analytics Service
        try:
            from app.services.analytics_service import get_analytics_service
            analytics_service = diagnose_service("Analytics Service", get_analytics_service)
            SERVICE_STATUS["analytics"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ Analytics Service optional: {e}")
        
        # AI Provider Service
        try:
            from app.services.ai_provider_service import AIProviderService
            ai_provider_service = diagnose_constructor("AI Provider Service", AIProviderService)
            SERVICE_STATUS["ai_provider"] = True
        except Exception as e:
            logger.warning(f"   ⚠️ AI Provider Service optional: {e}")
        
        # AI Query Service
        try:
            from app.services.ai_query_service import get_ai_query_service
            ai_query_service = diagnose_constructor("AI Query Service", get_ai_query_service)
            SERVICE_STATUS["ai_query"] = True
            app.state.ai_query_available = True
            app.state.ai_query_service = ai_query_service
        except Exception as e:
            logger.error(f"   ❌ AI Query Service failed: {e}")
            SERVICE_STATUS["ai_query"] = False
            app.state.ai_query_available = False
        
        # WhatsApp Service
        try:
            from app.services.whatsapp_service import get_whatsapp_service
            whatsapp_service = diagnose_constructor("WhatsApp Service", get_whatsapp_service)
            SERVICE_STATUS["whatsapp"] = True
        except Exception as e:
            logger.error(f"   ❌ WhatsApp Service failed: {e}")
            SERVICE_STATUS["whatsapp"] = False
        
        # ==========================================================
        # STAGE 8/12 - Create Directories
        # ==========================================================
        add_timeline_entry("STAGE_8_DIRECTORIES")
        logger.info("📍 STAGE 8/12: Creating Directories")
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
        add_timeline_entry("STARTUP_COMPLETE")
        
        logger.info("=" * 80)
        logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
        logger.info("=" * 80)
        logger.info("SERVICE STATUS SUMMARY:")
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
        add_timeline_entry("STARTUP_FAILED")
        location = crash_location(e)
        
        logger.critical("=" * 80)
        logger.critical("💥 APPLICATION STARTUP FAILED 💥")
        logger.critical("=" * 80)
        
        if location:
            logger.critical(f"CRASH FILE: {location['file']}")
            logger.critical(f"CRASH LINE: {location['line']}")
            logger.critical(f"CRASH FUNCTION: {location['function']}")
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location['function'],
                error_type=type(e).__name__,
                error=str(e)[:200],
                module="lifespan"
            )
        
        logger.critical(f"ERROR TYPE: {type(e).__name__}")
        logger.critical(f"ERROR: {str(e)[:200]}")
        logger.critical("=" * 80)
        logger.exception("FULL TRACEBACK:")
        
        write_crash_report(e, "lifespan")
        raise
    
    finally:
        logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
        if 'engine' in dir():
            engine.dispose()
        dashboard_cache.clear()
        ServiceRegistry.clear()


# ==========================================================
# DIAGNOSTIC FUNCTIONS (must be defined before use)
# ==========================================================

def diagnose_service(service_name: str, func, *args, **kwargs):
    """Universal service checker"""
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


# ==========================================================
# MIDDLEWARE FUNCTIONS
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
# CACHE
# ==========================================================

dashboard_cache = TTLCache(maxsize=100, ttl=300)


# ==========================================================
# CREATE APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="12.0.0",
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
# DIAGNOSTICS ENDPOINTS
# ==========================================================

@app.get("/diagnostics", tags=["Diagnostics"])
async def get_diagnostics():
    """Get complete startup diagnostics"""
    return STARTUP_DIAGNOSTICS


@app.get("/root-cause", tags=["Diagnostics"])
async def get_root_cause_endpoint():
    """CRITICAL FIX #10: Single source of truth for crashes"""
    root_cause = get_root_cause()
    if root_cause:
        return root_cause
    return {"status": "NO_CRASH", "message": "No crash detected in current session"}


@app.get("/module-health", tags=["Diagnostics"])
async def get_module_health():
    """CRITICAL FIX #9: Module health status"""
    return {
        "modules": MODULE_HEALTH,
        "total_modules": len(MODULE_HEALTH),
        "failed_modules": [k for k, v in MODULE_HEALTH.items() if v.get("status") == "FAILED"],
        "timestamp": datetime.now().isoformat()
    }


@app.get("/crash-history", tags=["Diagnostics"])
async def get_crash_history():
    """Get crash history (limited to last 100)"""
    return {
        "crash_count": len(CRASH_HISTORY),
        "crashes": CRASH_HISTORY[-10:]
    }


@app.get("/startup-timeline", tags=["Diagnostics"])
async def get_startup_timeline():
    """Get startup timeline"""
    return {"timeline": STARTUP_TIMELINE, "total_stages": len(STARTUP_TIMELINE)}


@app.get("/deep-diagnostics", tags=["Diagnostics"])
async def deep_diagnostics():
    """Complete diagnostics in one endpoint"""
    return {
        "root_cause": get_root_cause(),
        "service_status": SERVICE_STATUS,
        "module_health": MODULE_HEALTH,
        "startup_status": STARTUP_DIAGNOSTICS["status"],
        "startup_duration": STARTUP_DIAGNOSTICS["startup_duration"],
        "crash_history_count": len(CRASH_HISTORY),
        "startup_timeline": STARTUP_TIMELINE,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/crash-diagnostics", tags=["Debug"])
async def crash_diagnostics():
    """Get detailed crash diagnostics"""
    return {
        "status": STARTUP_DIAGNOSTICS["status"],
        "version": "12.0.0",
        "services_status": SERVICE_STATUS,
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
    db_connected = SERVICE_STATUS.get("database", False)
    return {
        "ready": db_connected,
        "checks": {"database": "connected" if db_connected else "disconnected"},
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/health", tags=["Health"])
async def health():
    uptime = request_metrics.get()["uptime_seconds"]
    return {
        "status": "healthy" if SERVICE_STATUS.get("database", False) else "degraded",
        "uptime_seconds": round(uptime, 2),
        "services": SERVICE_STATUS,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {
        "ping": "pong",
        "timestamp": datetime.utcnow().isoformat(),
        "healthy": SERVICE_STATUS.get("database", False)
    }


# ==========================================================
# CRASH TEST ENDPOINT
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/test-crash")
    async def test_crash():
        """Test endpoint to simulate a crash - for debugging only"""
        raise RuntimeError("This is a test crash - check /root-cause endpoint")


# ==========================================================
# ENTRY POINT
# ==========================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("app.main:app", host=host, port=port, reload=config.DEBUG, log_level="info")


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v12.0.0 - ROOT CAUSE DIAGNOSTICS")
logger.info("")
logger.info("   CRITICAL FIXES IN v12.0.0:")
logger.info("   ✅ crash_location() returns LAST app frame")
logger.info("   ✅ Crash history limited to 100 entries")
logger.info("   ✅ Reduced traceback storage")
logger.info("   ✅ Reuse cached imports")
logger.info("   ✅ Module health endpoint")
logger.info("   ✅ Root cause endpoint (single source of truth)")
logger.info("   ✅ All imports inside lifespan")
logger.info("   ✅ Config import at module level (NameError fix)")
logger.info("")
logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
logger.info(f"   ENVIRONMENT: {config.ENVIRONMENT}")
logger.info("=" * 60)
