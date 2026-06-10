# ==========================================================
# FILE: app/main.py (ENTERPRISE v7.0 - PRODUCTION GRADE)
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
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, func, text, select
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError
from loguru import logger
from cachetools import TTLCache
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

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

import app.models

from app.models import (
    Customer,
    Conversation,
    Message,
    DeliveryReport,
    SystemSetting,
    AIResponseLog
)

from app.services.schema_service import (
    check_schema_version,
    get_schema_info,
    APP_SCHEMA_VERSION
)

from app.services.whatsapp_service import get_whatsapp_service

from app.routes.upload import router as upload_router
from app.routes.webhook import router as webhook_router

from app.config import config


# ==========================================================
# PRIORITY 9: CENTRALIZED CONFIGURATION
# ==========================================================

# All config values now come from app.config
ENVIRONMENT = config.ENVIRONMENT if hasattr(config, 'ENVIRONMENT') else os.getenv("ENVIRONMENT", "production")
FRONTEND_URL = config.FRONTEND_URL if hasattr(config, 'FRONTEND_URL') else os.getenv("FRONTEND_URL", "http://localhost:3000")
ALLOWED_HOSTS = config.ALLOWED_HOSTS if hasattr(config, 'ALLOWED_HOSTS') else os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app").split(",")


# ==========================================================
# PRIORITY 14: SERVICE REGISTRY
# ==========================================================

class ServiceRegistry:
    """Centralized service registry for dependency injection"""
    _services = {}
    
    @classmethod
    def register(cls, name: str, service):
        cls._services[name] = service
    
    @classmethod
    def get(cls, name: str):
        return cls._services.get(name)
    
    @classmethod
    def clear(cls):
        cls._services.clear()


# ==========================================================
# PRIORITY 5: REQUEST METRICS
# ==========================================================

request_metrics = {
    "total_requests": 0,
    "successful_requests": 0,
    "failed_requests": 0,
    "error_count": 0,
    "avg_response_time_ms": 0,
    "start_time": time.time(),
    "endpoints": {}
}


def update_metrics(endpoint: str, status_code: int, duration_ms: float):
    request_metrics["total_requests"] += 1
    if 200 <= status_code < 300:
        request_metrics["successful_requests"] += 1
    else:
        request_metrics["failed_requests"] += 1
        request_metrics["error_count"] += 1
    
    # Update average response time
    current_avg = request_metrics["avg_response_time_ms"]
    total = request_metrics["total_requests"]
    request_metrics["avg_response_time_ms"] = ((current_avg * (total - 1)) + duration_ms) / total
    
    # Track per endpoint
    if endpoint not in request_metrics["endpoints"]:
        request_metrics["endpoints"][endpoint] = {"count": 0, "errors": 0}
    request_metrics["endpoints"][endpoint]["count"] += 1


# ==========================================================
# PRIORITY 7: REQUEST ID MIDDLEWARE (with metrics)
# ==========================================================

async def add_request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    start_time = time.time()
    
    with logger.contextualize(request_id=request_id):
        logger.debug(f"Request started: {request.method} {request.url.path}")
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            update_metrics(request.url.path, response.status_code, duration_ms)
            logger.debug(f"Request completed: {response.status_code} in {duration_ms:.2f}ms")
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Response-Time-Ms"] = str(int(duration_ms))
            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            update_metrics(request.url.path, 500, duration_ms)
            logger.error(f"Request failed: {e} in {duration_ms:.2f}ms")
            raise


# ==========================================================
# PRIORITY 12: SECURITY HEADERS MIDDLEWARE
# ==========================================================

async def add_security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


# ==========================================================
# PRIORITY 10: STARTUP REPORT
# ==========================================================

class StartupReport:
    @staticmethod
    def print_report(results: Dict[str, Any]):
        logger.info("=" * 60)
        logger.info("APP STARTUP REPORT")
        logger.info("=" * 60)
        
        for check, passed in results.items():
            status = "PASS" if passed else "FAIL"
            icon = "✅" if passed else "❌"
            logger.info(f"{icon} {check}: {status}")
        
        logger.info("=" * 60)


# ==========================================================
# PRIORITY 11: GLOBAL DEPENDENCY VALIDATION
# ==========================================================

def validate_dependencies() -> Dict[str, bool]:
    """Validate all required packages are installed"""
    required_packages = [
        "groq",
        "cachetools",
        "slowapi",
        "pandas",
        "sqlalchemy",
        "fastapi",
        "loguru"
    ]
    
    results = {}
    for package in required_packages:
        try:
            importlib.import_module(package)
            results[f"Package {package}"] = True
        except ImportError:
            results[f"Package {package}"] = False
            logger.error(f"Missing required package: {package}")
    
    return results


# ==========================================================
# PRIORITY 3: STARTUP SERVICE (with non-blocking validation)
# ==========================================================

class StartupService:
    @staticmethod
    def validate_environment() -> Dict[str, bool]:
        """Validate required environment variables - non-blocking"""
        required_vars = ["DATABASE_URL", "GROQ_API_KEY", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
        results = {}
        
        for var in required_vars:
            value = os.getenv(var) or getattr(config, var, None)
            results[var] = bool(value)
            if not value:
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
    def validate_models() -> Dict[str, bool]:
        results = {}
        for model in [Customer, Conversation, Message, DeliveryReport, SystemSetting]:
            try:
                inspect(model)
                results[model.__name__] = True
            except Exception as e:
                logger.error(f"Model {model.__name__} validation failed: {e}")
                results[model.__name__] = False
        return results
    
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
    def validate_upload_folder() -> bool:
        try:
            os.makedirs("uploads", exist_ok=True)
            return os.access("uploads", os.W_OK)
        except Exception:
            return False


# ==========================================================
# PRIORITY 10: AI SERVICE SINGLETON (with proper session handling)
# ==========================================================

_ai_service = None

def get_ai_service(db: Session = None):
    """Get AI service with proper session handling - PRIORITY 2 fix"""
    global _ai_service
    if _ai_service is None:
        try:
            from app.services.ai_query_service import AIQueryService
            # Don't store db in service - pass on each request
            _ai_service = AIQueryService
            logger.info("✅ AI Service class registered")
        except Exception as e:
            logger.error(f"❌ AI Service initialization failed: {e}")
            _ai_service = None
    return _ai_service


# ==========================================================
# PRIORITY 1: CHAT SERVICE (with proper session)
# ==========================================================

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.ai_service_class = get_ai_service()
    
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
        except SQLAlchemyError as e:
            self.db.rollback()
            logger.exception(f"Chat DB error: {e}")
            return "⚠️ Database error. Please try again."
        except Exception as e:
            self.db.rollback()
            logger.exception(f"Chat error: {e}")
            return "⚠️ Unable to save your message. Please try again."
    
    def _get_ai_response(self, message: str, phone_number: str = None) -> str:
        if not self.ai_service_class:
            return "⚠️ AI service is temporarily unavailable."
        try:
            ai_service = self.ai_service_class(self.db)  # Create service with current session
            result = ai_service.process_query(question=message, user_phone=phone_number or "web_chat")
            return result.get("response", "Thank you for contacting support.")
        except Exception as e:
            logger.exception(f"AI error: {e}")
            return "⚠️ AI processing error. Please try again."
    
    def _get_or_create_customer(self, name: str, phone: str = None) -> Customer:
        import re
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
# PRIORITY 8: DASHBOARD SERVICE (with separate queries)
# ==========================================================

# PRIORITY 3: Use Redis-ready cache (can be replaced with Redis)
dashboard_cache = TTLCache(maxsize=100, ttl=60)

class DashboardService:
    def __init__(self, db: Session):
        self.db = db
    
    def get_cached_dashboard_data(self) -> Dict[str, Any]:
        cache_key = "dashboard_data"
        if cache_key in dashboard_cache:
            logger.debug("Returning cached dashboard data")
            return dashboard_cache[cache_key]
        data = self._compute_dashboard_data()
        dashboard_cache[cache_key] = data
        return data
    
    def _compute_dashboard_data(self) -> Dict[str, Any]:
        # PRIORITY 7: Separate queries to avoid Cartesian joins
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
        return self.db.query(
            DeliveryReport.dealer_code,
            DeliveryReport.customer_name,
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.dealer_code, DeliveryReport.customer_name
        ).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_top_cities(self, limit=5):
        return self.db.query(
            DeliveryReport.ship_to_city.label('city'),
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.ship_to_city).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_top_warehouses(self, limit=5):
        return self.db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label('count')
        ).group_by(DeliveryReport.warehouse).order_by(func.count(DeliveryReport.id).desc()).limit(limit).all()
    
    def _get_latest_uploads(self, limit=5):
        return self.db.query(
            DeliveryReport.upload_batch_id,
            DeliveryReport.source_file,
            DeliveryReport.imported_at,
            func.count(DeliveryReport.id).label('record_count')
        ).group_by(DeliveryReport.upload_batch_id, DeliveryReport.source_file, DeliveryReport.imported_at
        ).order_by(DeliveryReport.imported_at.desc()).limit(limit).all()
    
    def get_dashboard_conversations(self, limit=10):
        return self.db.query(
            Conversation.id,
            Customer.name.label('customer_name'),
            Conversation.created_at,
            Conversation.status
        ).join(Customer).order_by(Conversation.created_at.desc()).limit(limit).all()
    
    def get_daily_message_stats(self, limit=7):
        return self.db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(func.date(Message.created_at).desc()).limit(limit).all()
    
    def get_latest_uploads_for_center(self, limit=20):
        return self._get_latest_uploads(limit)


# ==========================================================
# PRIORITY 6: GLOBAL EXCEPTION HANDLER (with categories)
# ==========================================================

async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'unknown')
    
    # PRIORITY 15: Exception categorization
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
        content={
            "success": False,
            "error": "Internal server error",
            "error_type": error_type,
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# ==========================================================
# PRIORITY 4 & 13: LIFESPAN HANDLER (non-blocking)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()
    
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v7.0")
    logger.info("=" * 80)
    
    # PRIORITY 11: Validate dependencies
    dep_results = validate_dependencies()
    
    # Run validations (non-blocking - log warnings but don't crash)
    env_results = StartupService.validate_environment()
    db_ok = StartupService.validate_database()
    models_ok = StartupService.validate_models()
    groq_ok = StartupService.validate_groq()
    whatsapp_ok = StartupService.validate_whatsapp()
    upload_ok = StartupService.validate_upload_folder()
    
    # PRIORITY 10: Startup report
    report = {
        "Environment Variables": all(env_results.values()),
        "Database Connection": db_ok,
        "Models": all(models_ok.values()),
        "Groq API": groq_ok,
        "WhatsApp API": whatsapp_ok,
        "Upload Folder": upload_ok,
        **dep_results
    }
    StartupReport.print_report(report)
    
    # Schema check (non-blocking)
    try:
        db = SessionLocal()
        check_schema_version(db)
        db.close()
        logger.info("✅ Schema version verified")
    except Exception as e:
        logger.warning(f"⚠️ Schema verification warning: {e}")
    
    # Service validation (non-blocking)
    try:
        ai_service_class = get_ai_service()
        if ai_service_class:
            logger.info("✅ AI Service available")
    except Exception as e:
        logger.warning(f"⚠️ AI Service validation warning: {e}")
    
    try:
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        logger.info(f"✅ WhatsApp Service: {'configured' if whatsapp_health.get('configured') else 'not configured'}")
    except Exception as e:
        logger.warning(f"⚠️ WhatsApp validation warning: {e}")
    
    startup_duration = time.time() - start_time
    logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
    logger.info("=" * 80)
    
    yield
    
    # PRIORITY 13: Graceful shutdown
    logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
    engine.dispose()
    dashboard_cache.clear()
    ServiceRegistry.clear()
    logger.info("✅ Resources cleaned up")


# ==========================================================
# CREATE APP (PRIORITY 23: OpenAPI Metadata)
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="7.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan
)

# Set up rate limiter
limiter = Limiter(key_func=get_remote_address, default_limits=["5 per second"])
limiter._app = app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Global exception handler
app.add_exception_handler(Exception, global_exception_handler)


# ==========================================================
# MIDDLEWARE (After app creation)
# ==========================================================

app.middleware("http")(add_request_id_middleware)
app.middleware("http")(add_security_headers_middleware)

@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.debug(f"{request.method} {request.url.path} | {response.status_code} | {duration_ms:.2f}ms")
    return response


# PRIORITY 12: Secure CORS for production
if ENVIRONMENT == "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_URL] if FRONTEND_URL != "*" else [],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        max_age=3600,
    )
    logger.info(f"CORS configured for production with origin: {FRONTEND_URL}")
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS configured for development (allow all)")

# Trusted Host Middleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


# ==========================================================
# REGISTER ROUTERS
# ==========================================================

app.include_router(upload_router)
app.include_router(webhook_router)


# ==========================================================
# TEMPLATES
# ==========================================================

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


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
# PRIORITY 5: HEALTH & METRICS ENDPOINTS
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
    """PRIORITY 5: Structured health check"""
    db_connected = check_database_connection()
    uptime = time.time() - request_metrics["start_time"]
    
    whatsapp_status = "unknown"
    try:
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        whatsapp_status = "healthy" if whatsapp_health.get("configured") else "not_configured"
    except Exception as e:
        whatsapp_status = "error"
    
    return {
        "status": "healthy" if db_connected else "degraded",
        "uptime_seconds": round(uptime, 2),
        "database": "connected" if db_connected else "disconnected",
        "whatsapp": whatsapp_status,
        "schema_version": APP_SCHEMA_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/metrics", tags=["Metrics"])
async def metrics():
    """PRIORITY 6: Request metrics endpoint"""
    uptime = time.time() - request_metrics["start_time"]
    
    return {
        "uptime_seconds": round(uptime, 2),
        "total_requests": request_metrics["total_requests"],
        "successful_requests": request_metrics["successful_requests"],
        "failed_requests": request_metrics["failed_requests"],
        "error_count": request_metrics["error_count"],
        "avg_response_time_ms": round(request_metrics["avg_response_time_ms"], 2),
        "success_rate": round((request_metrics["successful_requests"] / max(1, request_metrics["total_requests"])) * 100, 2),
        "endpoints": request_metrics["endpoints"]
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ai-status", tags=["AI"])
async def ai_status():
    ai_service_class = get_ai_service()
    return {
        "status": "available" if ai_service_class else "unavailable",
        "provider": "groq"
    }


@app.get("/ai-provider-status", tags=["AI"])
async def ai_provider_status():
    return {
        "provider": "groq",
        "model": os.getenv("GROQ_MODEL", "mixtral-8x7b-32768"),
        "status": "healthy",
        "available": True
    }


@app.get("/cache-status", tags=["Admin"])
async def cache_status():
    return {
        "cache_size": len(dashboard_cache),
        "cache_maxsize": dashboard_cache.maxsize,
        "cache_ttl_seconds": 60,
        "type": "in_memory_ttlcache",
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# DASHBOARD ENDPOINT
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    try:
        dashboard_service = DashboardService(db)
        dashboard_data = dashboard_service.get_cached_dashboard_data()
        dashboard_conversations = dashboard_service.get_dashboard_conversations(limit=5)
        daily_stats = dashboard_service.get_daily_message_stats(limit=7)
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        schema_info = get_schema_info(db)
        last_refresh = datetime.utcnow()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                **dashboard_data,
                "conversations": dashboard_conversations,
                "stats": [{"date": str(s.date), "count": s.count} for s in daily_stats],
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "groq_status": "Online" if groq_key else "Offline",
                "schema_version": schema_info.get("app_version", "7.0"),
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================================
# CHAT ENDPOINT
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
@limiter.limit("5 per second")
async def chat(request: ChatRequest, req: Request, db: Session = Depends(get_db)):
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


# ==========================================================
# STATUS ENDPOINT WITH CACHING
# ==========================================================

@app.get("/status", tags=["Status"])
async def status(db: Session = Depends(get_db)):
    cache_key = "system_status"
    cached = dashboard_cache.get(cache_key)
    if cached:
        return cached
    
    dashboard_service = DashboardService(db)
    dashboard_data = dashboard_service.get_cached_dashboard_data()
    schema_info = get_schema_info(db)
    
    result = {
        "application": "AI WhatsApp Agent",
        "version": "7.0.0",
        "database": "postgresql",
        "ai_provider": "groq",
        "whatsapp": "active",
        "statistics": {
            "total_customers": dashboard_data.get("total_customers", 0),
            "total_conversations": dashboard_data.get("total_conversations", 0),
            "total_delivery_records": dashboard_data.get("total_records", 0)
        },
        "schema": {
            "app_version": schema_info.get("app_version", "7.0"),
            "needs_migration": schema_info.get("needs_migration", False)
        },
        "timestamp": datetime.utcnow().isoformat()
    }
    
    dashboard_cache[cache_key] = result
    return result


# ==========================================================
# ROOT & INFO ENDPOINTS
# ==========================================================

@app.get("/", tags=["Root"])
async def home():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Logistics Assistant",
        "version": "7.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION,
        "ai_provider": "groq"
    }


@app.get("/schema-info", tags=["Info"])
async def schema_info_endpoint(db: Session = Depends(get_db)):
    return get_schema_info(db)


# ==========================================================
# UPLOAD ENDPOINTS
# ==========================================================

@app.get("/upload-center", tags=["Upload"])
async def upload_center(request: Request, db: Session = Depends(get_db)):
    try:
        dashboard_service = DashboardService(db)
        latest_uploads = dashboard_service.get_latest_uploads_for_center(limit=20)
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

if ENVIRONMENT != "production":
    @app.get("/db-test", tags=["Debug"])
    async def db_test():
        try:
            connected = check_database_connection()
            health = get_database_health()
            return {
                "connected": connected,
                "database_url_exists": bool(DATABASE_URL),
                "health": health,
                "environment": ENVIRONMENT
            }
        except Exception as e:
            logger.exception("DB test error")
            raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# API VERSIONING
# ==========================================================

@app.get("/api-info", tags=["Info"])
async def api_info():
    return {
        "current_version": "v1",
        "endpoints": {
            "chat": "/chat",
            "dashboard": "/dashboard",
            "health": "/health",
            "metrics": "/metrics",
            "webhook": "/webhook/"
        },
        "documentation": "/api/docs"
    }


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v7.0 - Enterprise Grade")
logger.info("   Features: Request Metrics | Service Registry | Startup Report")
logger.info("   Caching: Dashboard (60s) | Status (60s) | In-Memory Cache")
logger.info("   Security: CORS (Prod) | Trusted Host | Rate Limiting")
logger.info("   Monitoring: /metrics | /health | /readiness")
logger.info("=" * 60)
