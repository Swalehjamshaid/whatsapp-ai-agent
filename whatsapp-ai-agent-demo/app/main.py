# ==========================================================
# FILE: app/main.py (ENTERPRISE v8.0 - PRODUCTION READY)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
import sys
import time
import uuid
import importlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any, List
from collections import defaultdict
from threading import Lock
from fastapi import FastAPI, Depends, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, func, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
from cachetools import TTLCache
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Prometheus metrics
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

# ==========================================================
# DATABASE IMPORTS (Lazy loaded)
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
# PRIORITY 5: THREAD-SAFE METRICS
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
            
            # Update average response time
            current_avg = self._metrics["avg_response_time_ms"]
            total = self._metrics["total_requests"]
            self._metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + duration_ms) / total
            
            # Track per endpoint
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
    
    @property
    def start_time(self):
        return self._metrics["start_time"]


request_metrics = ThreadSafeMetrics()


# ==========================================================
# PRIORITY 12: PROMETHEUS METRICS
# ==========================================================

# Counters
whatsapp_messages_total = Counter('whatsapp_messages_total', 'Total WhatsApp messages', ['type'])
ai_calls_total = Counter('ai_calls_total', 'Total AI calls', ['provider', 'status'])
query_duration = Histogram('query_duration_seconds', 'Query duration in seconds', ['query_type'])
db_query_duration = Histogram('db_query_duration_seconds', 'Database query duration', ['operation'])
active_requests = Gauge('active_requests', 'Active requests')


# ==========================================================
# PRIORITY 14: SERVICE REGISTRY
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
# PRIORITY 3: AI SERVICE REGISTRY
# ==========================================================

_ai_service_class = None

def get_ai_service_class():
    """Lazy load AI service class"""
    global _ai_service_class
    if _ai_service_class is None:
        try:
            from app.services.ai_query_service import AIQueryService
            _ai_service_class = AIQueryService
            ServiceRegistry.register_service("ai_query_service", AIQueryService)
            logger.info("✅ AI Service class registered")
        except Exception as e:
            logger.error(f"❌ AI Service initialization failed: {e}")
            _ai_service_class = None
    return _ai_service_class


# ==========================================================
# PRIORITY 1: LAZY ROUTER LOADING
# ==========================================================

def load_routers(app: FastAPI):
    """Lazy load all routers - prevents crash if one fails"""
    
    # Webhook router
    try:
        from app.routes.webhook import router as webhook_router
        app.include_router(webhook_router)
        ServiceRegistry.register_route("webhook", webhook_router, "/webhook")
        logger.info("✅ Webhook router loaded")
    except Exception as e:
        logger.exception(f"❌ Webhook router failed to load: {e}")
    
    # Upload router
    try:
        from app.routes.upload import router as upload_router
        app.include_router(upload_router)
        ServiceRegistry.register_route("upload", upload_router, "/upload")
        logger.info("✅ Upload router loaded")
    except Exception as e:
        logger.exception(f"❌ Upload router failed to load: {e}")


# ==========================================================
# PRIORITY 6: MIDDLEWARE
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
    # PRIORITY 10: Add Content-Security-Policy
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';"
    return response


# ==========================================================
# PRIORITY 11: HIDE DEBUG INFO IN PRODUCTION
# ==========================================================

def safe_error_response(request_id: str, error_type: str = "internal_error") -> Dict[str, Any]:
    """Return safe error response without exposing internals"""
    response = {
        "success": False,
        "error": "Internal server error",
        "request_id": request_id,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    # Only add error_type in development
    if config.ENVIRONMENT != "production":
        response["error_type"] = error_type
    
    return response


# ==========================================================
# PRIORITY 15: STARTUP SERVICE
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
    def validate_routes():
        """Validate routes are properly registered"""
        routes_loaded = len(ServiceRegistry.get_routes()) > 0
        if not routes_loaded and config.ENVIRONMENT == "production":
            logger.warning("⚠️ No routes loaded - check router imports")
        return routes_loaded
    
    @staticmethod
    def validate_templates():
        """Validate templates directory exists"""
        templates_dir = os.path.join(os.path.dirname(__file__), "templates")
        return os.path.exists(templates_dir)


# ==========================================================
# LIFESPAN HANDLER
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()
    
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v8.0")
    logger.info("=" * 80)
    
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
    
    # Warmup AI service (detect issues early)
    try:
        ai_class = get_ai_service_class()
        if ai_class:
            logger.info("✅ AI Service warmup complete")
    except Exception as e:
        logger.warning(f"⚠️ AI Service warmup warning: {e}")
    
    # Load routers
    load_routers(app)
    
    # Validate routes
    StartupService.validate_routes()
    
    # Create upload directory
    os.makedirs("uploads", exist_ok=True)
    
    startup_duration = time.time() - start_time
    logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
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
    version="8.0.0",
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
# CACHE
# ==========================================================

dashboard_cache = TTLCache(maxsize=100, ttl=60)


# ==========================================================
# REQUEST MODELS
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15)


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# CHAT SERVICE (Inline for now - can be moved to separate file)
# ==========================================================

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.ai_service_class = get_ai_service_class()
    
    def process_chat(self, message: str, customer_name: str, phone_number: str = None) -> str:
        ai_reply = self._get_ai_response(message, phone_number)
        
        try:
            customer = self._get_or_create_customer(customer_name, phone_number)
            self.db.flush()
            
            conversation = Conversation(customer_id=customer.id, status="active")
            self.db.add(conversation)
            self.db.flush()
            
            user_msg = Message(conversation_id=conversation.id, sender="user", content=message, message_type="text")
            ai_msg = Message(conversation_id=conversation.id, sender="assistant", content=ai_reply, message_type="text")
            ai_log = AIResponseLog(conversation_id=conversation.id, prompt=message, ai_response=ai_reply, model_name="groq", success=True)
            
            self.db.add_all([user_msg, ai_msg, ai_log])
            self.db.commit()
            
            return ai_reply
        except Exception as e:
            self.db.rollback()
            logger.exception(f"Chat error: {e}")
            return "⚠️ Unable to process your request. Please try again."
    
    def _get_ai_response(self, message: str, phone_number: str = None) -> str:
        if not self.ai_service_class:
            return "⚠️ AI service is temporarily unavailable."
        try:
            ai_service = self.ai_service_class(self.db)
            result = ai_service.process_query(question=message, user_phone=phone_number or "web_chat")
            return result.get("response", "Thank you for contacting support.")
        except Exception as e:
            logger.exception(f"AI error: {e}")
            return "⚠️ AI processing error. Please try again."
    
    def _get_or_create_customer(self, name: str, phone: str = None) -> Customer:
        import re
        from app.models import Customer
        if phone:
            customer = self.db.query(Customer).filter(Customer.phone_number == phone).first()
            if customer:
                return customer
        safe_name = re.sub(r'[^a-z0-9]', '_', name.lower()).strip('_')
        customer = Customer(
            name=name,
            phone_number=phone or f"temp_{uuid.uuid4().hex[:12]}",
            email=f"{safe_name}@temp.com"
        )
        self.db.add(customer)
        return customer


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
            "groq": "configured" if groq_key else "not_configured"
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
        "schema_version": APP_SCHEMA_VERSION,
        "environment": config.ENVIRONMENT,
        "timestamp": datetime.utcnow().isoformat()
    }


# PRIORITY 13: Dedicated health endpoints
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
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


# ==========================================================
# PROMETHEUS METRICS ENDPOINT
# ==========================================================

@app.get("/metrics", tags=["Metrics"])
async def metrics():
    return JSONResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/cache-status", tags=["Admin"])
async def cache_status():
    return {
        "cache_size": len(dashboard_cache),
        "cache_maxsize": dashboard_cache.maxsize,
        "cache_ttl_seconds": 60,
        "type": "in_memory_ttlcache"
    }


# ==========================================================
# API VERSION 1 ENDPOINTS
# ==========================================================

@app.get("/api/v1/chat", response_model=ChatResponse, tags=["API v1"])
@limiter.limit("5 per second")
async def chat_v1(request: ChatRequest, req: Request, db: Session = Depends(get_db), background_tasks: BackgroundTasks = None):
    """Versioned chat endpoint - API v1"""
    try:
        chat_service = ChatService(db)
        result = chat_service.process_chat(
            message=request.message,
            customer_name=request.customer_name,
            phone_number=request.phone_number
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
# DASHBOARD ENDPOINT (Legacy - maintained for compatibility)
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        # Lazy import to avoid circular imports
        from app.models import Customer, Conversation, Message, DeliveryReport, AIResponseLog
        
        dashboard_service = DashboardService(db)
        dashboard_data = dashboard_service.get_cached_dashboard_data()
        
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
                "schema_version": schema_info.get("app_version", "8.0"),
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================================
# DASHBOARD SERVICE (Inline for now)
# ==========================================================

class DashboardService:
    def __init__(self, db: Session):
        self.db = db
    
    def get_cached_dashboard_data(self) -> Dict[str, Any]:
        cache_key = "dashboard_data"
        if cache_key in dashboard_cache:
            return dashboard_cache[cache_key]
        data = self._compute_dashboard_data()
        dashboard_cache[cache_key] = data
        return data
    
    def _compute_dashboard_data(self) -> Dict[str, Any]:
        from app.models import Customer, Conversation, DeliveryReport
        
        total_records = self.db.query(func.count(DeliveryReport.id)).scalar() or 0
        pending_deliveries = self.db.query(func.count(DeliveryReport.id)).filter(DeliveryReport.pending_flag.is_(True)).scalar() or 0
        pending_pod = self.db.query(func.count(DeliveryReport.id)).filter(DeliveryReport.pod_status == "Pending").scalar() or 0
        pending_pgi = self.db.query(func.count(DeliveryReport.id)).filter(DeliveryReport.pgi_status == "Pending").scalar() or 0
        total_amount = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        cities = self.db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = self.db.query(DeliveryReport.warehouse).distinct().count()
        total_customers = self.db.query(func.count(Customer.id)).scalar() or 0
        total_conversations = self.db.query(func.count(Conversation.id)).scalar() or 0
        
        return {
            "total_records": total_records,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": 0,
            "completed_deliveries": total_records - pending_deliveries,
            "total_amount": float(total_amount),
            "cities": cities,
            "warehouses": warehouses,
            "total_customers": total_customers,
            "total_conversations": total_conversations,
            "top_dealers": self._get_top_dealers(5),
            "top_cities": self._get_top_cities(5),
            "top_warehouses": self._get_top_warehouses(5),
            "latest_uploads": self._get_latest_uploads(5),
            "total_uploads": self.db.query(DeliveryReport.upload_batch_id).distinct().count(),
            "total_imported_rows": total_records,
            "last_upload_date": self.db.query(DeliveryReport.imported_at).order_by(DeliveryReport.imported_at.desc()).first()
        }
    
    def _get_top_dealers(self, limit=5):
        from app.models import DeliveryReport
        return self.db.query(
            DeliveryReport.dealer_code,
            DeliveryReport.customer_name,
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.dealer_code, DeliveryReport.customer_name
        ).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_top_cities(self, limit=5):
        from app.models import DeliveryReport
        return self.db.query(
            DeliveryReport.ship_to_city.label('city'),
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.ship_to_city).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_top_warehouses(self, limit=5):
        from app.models import DeliveryReport
        return self.db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.warehouse).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_latest_uploads(self, limit=5):
        from app.models import DeliveryReport
        return self.db.query(
            DeliveryReport.upload_batch_id,
            DeliveryReport.source_file,
            DeliveryReport.imported_at,
            func.count(DeliveryReport.id).label('record_count')
        ).group_by(DeliveryReport.upload_batch_id, DeliveryReport.source_file, DeliveryReport.imported_at
        ).order_by(DeliveryReport.imported_at.desc()).limit(limit).all()


# ==========================================================
# LEGACY ENDPOINTS (Maintained for backward compatibility)
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
@limiter.limit("5 per second")
async def chat_legacy(request: ChatRequest, req: Request, db: Session = Depends(get_db)):
    """Legacy chat endpoint - maintained for compatibility"""
    try:
        chat_service = ChatService(db)
        result = chat_service.process_chat(
            message=request.message,
            customer_name=request.customer_name,
            phone_number=request.phone_number
        )
        return {"success": True, "reply": result}
    except Exception as e:
        logger.exception("Chat endpoint error")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/status", tags=["Status"])
async def status_legacy(db: Session = Depends(get_db)):
    """Legacy status endpoint"""
    cache_key = "system_status"
    cached = dashboard_cache.get(cache_key)
    if cached:
        return cached
    
    from app.models import Customer, Conversation, DeliveryReport
    
    result = {
        "application": "AI WhatsApp Agent",
        "version": "8.0.0",
        "database": "postgresql",
        "ai_provider": "groq",
        "whatsapp": "active",
        "statistics": {
            "total_customers": db.query(func.count(Customer.id)).scalar() or 0,
            "total_conversations": db.query(func.count(Conversation.id)).scalar() or 0,
            "total_delivery_records": db.query(func.count(DeliveryReport.id)).scalar() or 0
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    dashboard_cache[cache_key] = result
    return result


@app.get("/", tags=["Root"])
async def home():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Logistics Assistant",
        "version": "8.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION,
        "ai_provider": "groq"
    }


@app.get("/schema-info", tags=["Info"])
async def schema_info_endpoint(db: Session = Depends(get_db)):
    return get_schema_info(db)


@app.get("/upload-center", tags=["Upload"])
async def upload_center(request: Request, db: Session = Depends(get_db)):
    from app.models import DeliveryReport
    
    try:
        dashboard_service = DashboardService(db)
        latest_uploads = dashboard_service._get_latest_uploads(20)
        total_batches = db.query(DeliveryReport.upload_batch_id).distinct().count()
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
                "environment": config.ENVIRONMENT
            }
        except Exception as e:
            logger.exception("DB test error")
            raise HTTPException(status_code=500, detail=str(e))


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
        "legacy_endpoints": {
            "chat": "/chat",
            "dashboard": "/dashboard",
            "webhook": "/webhook/"
        },
        "documentation": "/api/docs" if config.ENVIRONMENT != "production" else "disabled"
    }


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v8.0 - Production Ready")
logger.info("   Features: Lazy Router Loading | Prometheus Metrics | Thread-Safe")
logger.info("   API Versioning: /api/v1")
logger.info("   Monitoring: /metrics | /health | /readiness | /liveness")
logger.info("   Security: CORS (Prod) | Trusted Host | Security Headers | CSP")
logger.info("=" * 60)
