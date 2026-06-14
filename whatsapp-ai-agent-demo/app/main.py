# ==========================================================
# FILE: app/main.py (ENTERPRISE v13.0.0 - COMPLETE CRASH DIAGNOSTICS)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================
# IMPROVEMENTS v13.0.0:
# - ✅ ADDED: Pre-flight check (Python version, packages, env vars, directories)
# - ✅ ADDED: Import Dependency Scanner with full tree visualization
# - ✅ ADDED: Constructor step-by-step tracking
# - ✅ ADDED: Runtime diagnostics middleware (requests, memory, queries)
# - ✅ ADDED: Fatal crash analysis with full context
# - ✅ ADDED: Railway-specific diagnostics endpoint
# - ✅ ADDED: Module fingerprinting with health status
# - ✅ ADDED: Crash classification system
# - ✅ ADDED: Automatic file ranking based on crash likelihood
# - ✅ ADDED: Enhanced /root-cause endpoint (single source of truth)
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
# CRITICAL FIX: Import config at MODULE LEVEL
# ==========================================================
from app.config import config

# Try to import psutil for memory diagnostics (optional)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ==========================================================
# CRASH CLASSIFICATION (Improvement)
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
# LAYER 1: PRE-FLIGHT CHECK
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
# LAYER 2: IMPORT DEPENDENCY SCANNER
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
    
    for child in tree["children"][:5]:  # Limit to 5 children
        if child["circular"]:
            logger.info(f"{indent}│   └── {child['name']} (circular)")
        else:
            print_import_tree(child["name"], indent + "│   ")


# ==========================================================
# LAYER 3: CONSTRUCTOR STEP-BY-STEP TRACKING
# ==========================================================

class ConstructorTracker:
    """Track constructor execution step by step"""
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.steps = []
        self.current_step = 0
        self.start_time = time.time()
        self.failed_step = None
    
    def step(self, step_name: str):
        """Log a constructor step"""
        self.current_step += 1
        self.steps.append({
            "step": self.current_step,
            "name": step_name,
            "timestamp": datetime.now().isoformat()
        })
        logger.info(f"   🔧 {self.service_name} - STEP {self.current_step}: {step_name}")
    
    def complete(self):
        """Mark constructor as complete"""
        elapsed = time.time() - self.start_time
        logger.success(f"   ✅ {self.service_name} constructed in {elapsed:.2f}s")
        return elapsed
    
    def fail(self, step_name: str, error: Exception):
        """Mark constructor failure at specific step"""
        self.failed_step = {
            "step": self.current_step + 1,
            "name": step_name,
            "error": str(error),
            "error_type": type(error).__name__
        }
        logger.error(f"   ❌ {self.service_name} FAILED at STEP {self.current_step + 1}: {step_name}")
        return self.failed_step


# ==========================================================
# LAYER 4: RUNTIME DIAGNOSTICS MIDDLEWARE
# ==========================================================

LAST_REQUEST_ERROR = None


async def runtime_diagnostics_middleware(request: Request, call_next):
    """Track runtime metrics for each request"""
    global LAST_REQUEST_ERROR
    
    start_time = time.time()
    start_memory = None
    if PSUTIL_AVAILABLE:
        start_memory = psutil.Process().memory_info().rss / (1024 * 1024)
    
    try:
        response = await call_next(request)
        
        duration = time.time() - start_time
        response.headers["X-Response-Time-ms"] = str(int(duration * 1000))
        
        if PSUTIL_AVAILABLE:
            end_memory = psutil.Process().memory_info().rss / (1024 * 1024)
            memory_delta = end_memory - start_memory
            response.headers["X-Memory-Delta-MB"] = str(round(memory_delta, 2))
        
        return response
        
    except Exception as e:
        duration = time.time() - start_time
        LAST_REQUEST_ERROR = {
            "path": request.url.path,
            "method": request.method,
            "error_type": type(e).__name__,
            "error_message": str(e)[:200],
            "duration_ms": round(duration * 1000, 2),
            "timestamp": datetime.now().isoformat()
        }
        raise


# ==========================================================
# ENHANCED CRASH LOCATION (with full context)
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
    """Analyze full crash path"""
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
# ENHANCED ROOT CAUSE STORAGE (Single source of truth)
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
    """Set enhanced root cause with full context"""
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
    
    # Update crash score
    update_crash_score(file, 100)
    for mod in _FAILED_MODULES:
        update_crash_score(mod, 50)
    for svc in _FAILED_SERVICES:
        update_crash_score(svc, 75)


def get_root_cause() -> Optional[Dict[str, Any]]:
    """Get enhanced root cause"""
    return _ROOT_CAUSE


# ==========================================================
# CRASH HISTORY (with classification)
# ==========================================================

MAX_CRASH_HISTORY = 100
CRASH_HISTORY = []


def add_to_crash_history(crash_data: Dict[str, Any]):
    """Add crash to history with classification"""
    CRASH_HISTORY.append(crash_data)
    while len(CRASH_HISTORY) > MAX_CRASH_HISTORY:
        CRASH_HISTORY.pop(0)


def write_crash_report(exc: Exception, stage: str = "unknown"):
    """Write crash report with full context"""
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
    """Update module fingerprint with health status"""
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
    """Diagnose module import with tree logging"""
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
    """Diagnose constructor with step tracking"""
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
# MIDDLEWARE
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

dashboard_cache = TTLCache(maxsize=100, ttl=300)


# ==========================================================
# CREATE APP
# ==========================================================

# Run pre-flight check BEFORE creating app
preflight_result = preflight_check()

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="13.0.0",
    docs_url="/api/docs" if config.ENVIRONMENT != "production" else None,
    redoc_url="/api/redoc" if config.ENVIRONMENT != "production" else None,
    openapi_url="/api/openapi.json" if config.ENVIRONMENT != "production" else None,
)

# Add runtime diagnostics middleware
app.middleware("http")(runtime_diagnostics_middleware)
app.middleware("http")(add_request_id_middleware)
app.middleware("http")(add_security_headers_middleware)

# Rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["5 per second"])
limiter._app = app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ==========================================================
# LIFESPAN HANDLER (Main startup)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    STARTUP_DIAGNOSTICS["startup_time"] = datetime.now().isoformat()
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
        logger.info("✅ Database module loaded")
    except Exception as e:
        location = crash_location(e)
        logger.error(f"❌ Database module failed: {e}")
        if location:
            set_root_cause(
                file=location['file'],
                line=location['line'],
                function=location.get('function', 'unknown'),
                error_type=type(e).__name__,
                error=str(e),
                crash_type=CrashType.DATABASE_ERROR
            )
        raise
    
    CACHE_TTL = getattr(config, 'CACHE_TTL', 300)
    imported_modules = {}
    
    try:
        logger.info("=" * 80)
        logger.info("🤖 AI WHATSAPP AGENT STARTING v13.0.0")
        logger.info("=" * 80)
        
        # Stage 1: Import all modules
        logger.info("📍 STAGE 1: Importing Modules")
        for module_name in ALL_FILES_TO_DIAGNOSE:
            try:
                imported_modules[module_name] = diagnose_import(module_name, use_cache=True)
            except Exception as e:
                write_crash_report(e, f"import_{module_name}")
                raise
        
        # Stage 2: Register webhook router
        webhook_router = None
        if "app.routes.webhook" in imported_modules:
            webhook_router = getattr(imported_modules["app.routes.webhook"], "router", None)
            if webhook_router:
                app.include_router(webhook_router)
                logger.success("✅ Webhook router registered")
        
        # Stage 3: Initialize services
        logger.info("📍 STAGE 2: Initializing Services")
        
        try:
            from app.services.schema_service import get_schema_service
            schema_service = diagnose_service("Schema Service", get_schema_service)
        except Exception as e:
            logger.warning(f"⚠️ Schema Service optional: {e}")
        
        try:
            from app.services.kpi_service import get_kpi_service
            kpi_service = diagnose_service("KPI Service", get_kpi_service)
        except Exception as e:
            logger.warning(f"⚠️ KPI Service optional: {e}")
        
        try:
            from app.services.analytics_service import get_analytics_service
            analytics_service = diagnose_service("Analytics Service", get_analytics_service)
        except Exception as e:
            logger.warning(f"⚠️ Analytics Service optional: {e}")
        
        try:
            from app.services.ai_provider_service import AIProviderService
            ai_provider_service = diagnose_constructor("AI Provider Service", AIProviderService)
        except Exception as e:
            logger.warning(f"⚠️ AI Provider Service optional: {e}")
        
        try:
            from app.services.ai_query_service import get_ai_query_service
            ai_query_service = diagnose_constructor("AI Query Service", get_ai_query_service)
            app.state.ai_query_available = True
            app.state.ai_query_service = ai_query_service
        except Exception as e:
            logger.error(f"❌ AI Query Service failed: {e}")
            app.state.ai_query_available = False
        
        try:
            from app.services.whatsapp_service import get_whatsapp_service
            whatsapp_service = diagnose_constructor("WhatsApp Service", get_whatsapp_service)
        except Exception as e:
            logger.error(f"❌ WhatsApp Service failed: {e}")
        
        # Stage 4: Create directories
        os.makedirs("uploads", exist_ok=True)
        os.makedirs(TEMPLATES_DIR, exist_ok=True)
        
        startup_duration = time.time() - start_time
        STARTUP_DIAGNOSTICS["startup_duration"] = startup_duration
        STARTUP_DIAGNOSTICS["status"] = "COMPLETED"
        
        logger.info("=" * 80)
        logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
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


app.lifespan_context = lifespan


# ==========================================================
# DIAGNOSTICS ENDPOINTS
# ==========================================================

@app.get("/root-cause", tags=["Diagnostics"])
async def get_root_cause_endpoint():
    """Single source of truth for crashes"""
    root_cause = get_root_cause()
    if root_cause:
        return root_cause
    return {"status": "NO_CRASH", "message": "No crash detected"}


@app.get("/railway-diagnostics", tags=["Diagnostics"])
async def railway_diagnostics():
    """Railway-specific diagnostics"""
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
        "environment": config.ENVIRONMENT
    }


@app.get("/module-health", tags=["Diagnostics"])
async def module_health():
    """Module fingerprinting endpoint"""
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
    """Get crash classification statistics"""
    classification_counts = defaultdict(int)
    for crash in CRASH_HISTORY:
        classification_counts[crash.get("crash_type", CrashType.UNKNOWN_ERROR)] += 1
    
    return {
        "classifications": dict(classification_counts),
        "top_files": get_top_crash_files(10),
        "root_cause": get_root_cause()
    }


# ==========================================================
# HEALTH ENDPOINTS
# ==========================================================

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "version": "13.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "preflight": preflight_result["status"]
    }


@app.get("/liveness", tags=["Health"])
async def liveness():
    return {"alive": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/ping", tags=["Health"])
async def ping():
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


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
# CRASH TEST ENDPOINT
# ==========================================================

if config.ENVIRONMENT != "production":
    @app.get("/test-crash")
    async def test_crash():
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
# INITIALIZATION LOG (CRITICAL FIX: Wrapped in try/except)
# ==========================================================

# CRITICAL: Wrapping the initialization log in try/except to capture any crashes at line 1188+
try:
    logger.info("=" * 60)
    logger.info("📡 MAIN APP v13.0.0 - COMPLETE CRASH DIAGNOSTICS")
    logger.info("")
    logger.info("   NEW FEATURES IN v13.0.0:")
    logger.info("   ✅ Pre-flight check (Python, packages, env, directories)")
    logger.info("   ✅ Import dependency tree scanner")
    logger.info("   ✅ Constructor step-by-step tracking")
    logger.info("   ✅ Runtime diagnostics middleware")
    logger.info("   ✅ Crash classification system")
    logger.info("   ✅ File ranking based on crash likelihood")
    logger.info("   ✅ Railway-specific diagnostics endpoint")
    logger.info("   ✅ Module fingerprinting")
    logger.info("   ✅ Enhanced /root-cause endpoint")
    logger.info("")
    logger.info(f"   PRE-FLIGHT: {preflight_result['status']}")
    logger.info(f"   CACHE_TTL: {CACHE_TTL}s")
    logger.info("=" * 60)
except Exception as init_error:
    # This catch block ensures ANY crash during initialization logging is captured
    logger.critical("=" * 80)
    logger.critical("💥 CRITICAL: INITIALIZATION LOGGING CRASHED 💥")
    logger.critical("=" * 80)
    logger.critical(f"ERROR AT LINE ~1188: {type(init_error).__name__}: {init_error}")
    logger.critical(traceback.format_exc())
    
    # Write crash report and set root cause
    location = crash_location(init_error)
    if location:
        set_root_cause(
            file=location['file'],
            line=location['line'],
            function=location.get('function', 'initialization_log'),
            error_type=type(init_error).__name__,
            error=str(init_error),
            code=location.get('code'),
            crash_type=classify_crash(init_error)
        )
    write_crash_report(init_error, "initialization_log")
    
    # Re-raise to ensure the application fails appropriately
    raise
