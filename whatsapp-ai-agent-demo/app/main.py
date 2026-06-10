# ==========================================================
# FILE: app/main.py (ENTERPRISE v6.0 - ALL IMPROVEMENTS INLINE)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, func, text, select
from sqlalchemy.orm import Session
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


# ==========================================================
# PRIORITY 7: REQUEST ID TRACKING
# ==========================================================

async def add_request_id_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())[:8]
    request.state.request_id = request_id
    
    with logger.contextualize(request_id=request_id):
        logger.debug(f"Request started: {request.method} {request.url.path}")
        start_time = time.time()
        
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            logger.debug(f"Request completed: {response.status_code} in {duration_ms:.2f}ms")
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
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
# PRIORITY 3: STARTUP SERVICE (INLINE)
# ==========================================================

class StartupService:
    @staticmethod
    def validate_environment():
        required_vars = ["DATABASE_URL", "GROQ_API_KEY", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_PHONE_NUMBER_ID"]
        missing = [v for v in required_vars if not os.getenv(v)]
        if missing:
            error_msg = f"Missing required env vars: {', '.join(missing)}"
            logger.error(error_msg)
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
        else:
            logger.info("✅ All required environment variables are set")
    
    @staticmethod
    def validate_database():
        if not check_database_connection():
            logger.error("Database connection failed")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
        else:
            logger.info("✅ Database connected")
    
    @staticmethod
    def validate_models():
        for model in [Customer, Conversation, Message, DeliveryReport, SystemSetting]:
            try:
                inspect(model)
                logger.debug(f"✅ Model {model.__name__} validated")
            except Exception as e:
                logger.error(f"❌ Model {model.__name__} validation failed: {e}")
                raise
    
    @staticmethod
    def validate_groq():
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            logger.error("GROQ_API_KEY not set")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
        else:
            logger.info("✅ Groq API key configured")
    
    @staticmethod
    def validate_whatsapp():
        token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        if not token or not phone_id:
            logger.error("WhatsApp configuration incomplete")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
        else:
            logger.info("✅ WhatsApp configured")
    
    @staticmethod
    def validate_upload_folder():
        os.makedirs("uploads", exist_ok=True)
        if os.access("uploads", os.W_OK):
            logger.info("✅ Upload folder ready")
        else:
            logger.error("❌ Upload folder not writable")


# ==========================================================
# PRIORITY 10: AI SERVICE SINGLETON WRAPPER
# ==========================================================

_ai_service = None

def get_ai_service():
    global _ai_service
    if _ai_service is None:
        try:
            from app.services.ai_query_service import AIQueryService
            db = SessionLocal()
            _ai_service = AIQueryService(db)
            db.close()
            logger.info("✅ AI Service singleton initialized")
        except Exception as e:
            logger.error(f"❌ AI Service initialization failed: {e}")
            _ai_service = None
    return _ai_service


# ==========================================================
# PRIORITY 1: CHAT SERVICE (INLINE)
# ==========================================================

class ChatService:
    def __init__(self, db: Session):
        self.db = db
        self.ai_service = get_ai_service()
    
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
            logger.exception(f"Chat DB error: {e}")
            return "⚠️ Unable to save your message. Please try again."
    
    def _get_ai_response(self, message: str, phone_number: str = None) -> str:
        if not self.ai_service:
            return "⚠️ AI service is temporarily unavailable."
        try:
            result = self.ai_service.process_query(question=message, user_phone=phone_number or "web_chat")
            return result.get("response", "Thank you for contacting support.")
        except Exception as e:
            logger.exception(f"AI error: {e}")
            return "⚠️ AI processing error. Please try again."
    
    def _get_or_create_customer(self, name: str, phone: str = None) -> Customer:
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
# PRIORITY 2 & 8 & 9: DASHBOARD SERVICE WITH CACHING (INLINE)
# ==========================================================

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
        # PRIORITY 8: Single aggregation query for all counts
        stats = self.db.query(
            func.count(DeliveryReport.id).label('total_records'),
            func.sum(DeliveryReport.pending_flag.cast(type_=int)).label('pending_deliveries'),
            func.sum((DeliveryReport.pod_status == 'Pending').cast(type_=int)).label('pending_pod'),
            func.sum((DeliveryReport.pgi_status == 'Pending').cast(type_=int)).label('pending_pgi'),
            func.sum(DeliveryReport.dn_amount).label('total_amount'),
            func.count(DeliveryReport.ship_to_city.distinct()).label('cities'),
            func.count(DeliveryReport.warehouse.distinct()).label('warehouses'),
            func.count(Customer.id).label('total_customers'),
            func.count(Conversation.id).label('total_conversations')
        ).first()
        
        return {
            "total_records": stats.total_records or 0,
            "pending_deliveries": stats.pending_deliveries or 0,
            "pending_pod": stats.pending_pod or 0,
            "pending_pgi": stats.pending_pgi or 0,
            "pending_amount": 0,
            "completed_deliveries": (stats.total_records or 0) - (stats.pending_deliveries or 0),
            "total_amount": float(stats.total_amount or 0),
            "cities": stats.cities or 0,
            "warehouses": stats.warehouses or 0,
            "total_customers": stats.total_customers or 0,
            "total_conversations": stats.total_conversations or 0,
            "top_dealers": self._get_top_dealers(5),
            "top_cities": self._get_top_cities(5),
            "top_warehouses": self._get_top_warehouses(5),
            "latest_uploads": self._get_latest_uploads(5),
            "total_uploads": self.db.query(DeliveryReport.upload_batch_id).distinct().count(),
            "total_imported_rows": stats.total_records or 0,
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
# PRIORITY 6: GLOBAL EXCEPTION HANDLER
# ==========================================================

async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, 'request_id', 'unknown')
    logger.exception(f"Unhandled exception [req:{request_id}]: {exc}")
    
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": "Internal server error",
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat()
        }
    )


# ==========================================================
# LIFESPAN HANDLER (PRIORITY 4, 14, 15, 16, 17, 18)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_time = time.time()
    
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v6.0")
    logger.info("=" * 80)
    
    # PRIORITY 3: Run all validations
    StartupService.validate_environment()
    StartupService.validate_database()
    StartupService.validate_models()
    StartupService.validate_groq()
    StartupService.validate_whatsapp()
    StartupService.validate_upload_folder()
    
    # PRIORITY 4: Use schema check instead of create_all
    try:
        db = SessionLocal()
        check_schema_version(db)
        db.close()
        logger.info("✅ Schema version verified")
    except Exception as e:
        logger.error(f"❌ Schema verification failed: {e}")
    
    # PRIORITY 17: Validate AI service
    try:
        ai_service = get_ai_service()
        if ai_service:
            health = ai_service.health_check()
            logger.info(f"✅ AI Service: {health.get('status', 'unknown')}")
    except Exception as e:
        logger.warning(f"⚠️ AI Service validation warning: {e}")
    
    # PRIORITY 18: Validate WhatsApp service
    try:
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        logger.info(f"✅ WhatsApp Service: {'configured' if whatsapp_health.get('configured') else 'not configured'}")
    except Exception as e:
        logger.warning(f"⚠️ WhatsApp validation warning: {e}")
    
    # PRIORITY 14: Startup duration
    startup_duration = time.time() - start_time
    logger.info(f"✅ Application startup complete in {startup_duration:.2f}s")
    logger.info("=" * 80)
    
    yield
    
    # PRIORITY 15: Graceful shutdown
    logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
    engine.dispose()
    dashboard_cache.clear()
    logger.info("✅ Resources cleaned up")


# ==========================================================
# CREATE APP (PRIORITY 23: OpenAPI Metadata)
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Logistics Assistant",
    description="Enterprise Logistics AI Platform - WhatsApp Integration",
    version="6.0.0",
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

# PRIORITY 6: Global exception handler
app.add_exception_handler(Exception, global_exception_handler)


# ==========================================================
# MIDDLEWARE (After app creation)
# ==========================================================

app.middleware("http")(add_request_id_middleware)
app.middleware("http")(add_security_headers_middleware)

# PRIORITY 21: Reduce INFO logging - use DEBUG for requests
@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.debug(f"{request.method} {request.url.path} | {response.status_code} | {duration_ms:.2f}ms")
    return response


# PRIORITY 11: Tighten CORS for production
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

if ENVIRONMENT == "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_URL],
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
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app").split(",")
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
# REQUEST MODELS (PRIORITY 9 - Validation)
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=100)
    message: str = Field(min_length=1, max_length=2000)
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15)


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# PRIORITY 4 & 5 & 19: LIVENESS & READINESS & AI STATUS
# ==========================================================

@app.get("/liveness", tags=["Health"])
async def liveness():
    return {"alive": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/readiness", tags=["Health"])
async def readiness():
    status = {"ready": False, "checks": {}, "timestamp": datetime.utcnow().isoformat()}
    
    try:
        db_connected = check_database_connection()
        status["checks"]["database"] = "connected" if db_connected else "disconnected"
    except Exception as e:
        status["checks"]["database"] = f"error: {str(e)}"
    
    groq_key = os.getenv("GROQ_API_KEY")
    status["checks"]["groq"] = "configured" if groq_key else "not_configured"
    
    try:
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        status["checks"]["whatsapp"] = "healthy" if whatsapp_health.get("configured") else "not_configured"
    except Exception as e:
        status["checks"]["whatsapp"] = f"error: {str(e)}"
    
    status["ready"] = (
        status["checks"]["database"] == "connected" and
        status["checks"]["groq"] == "configured" and
        status["checks"]["whatsapp"] == "healthy"
    )
    
    return status


@app.get("/health", tags=["Health"])
async def health():
    db_connected = check_database_connection()
    
    whatsapp_status = "unknown"
    try:
        whatsapp = get_whatsapp_service()
        whatsapp_health = whatsapp.health_check()
        whatsapp_status = "healthy" if whatsapp_health.get("configured") else "not_configured"
    except Exception as e:
        whatsapp_status = "error"
    
    ai_service = get_ai_service()
    
    return {
        "status": "healthy" if db_connected else "degraded",
        "database": "connected" if db_connected else "disconnected",
        "whatsapp": whatsapp_status,
        "ai_service": ai_service.health_check() if ai_service else {"status": "unavailable"},
        "schema_version": APP_SCHEMA_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ai-status", tags=["AI"])
async def ai_status():
    ai_service = get_ai_service()
    return ai_service.health_check() if ai_service else {"status": "unavailable", "error": "AI service not initialized"}


# PRIORITY 19: AI Provider Status Endpoint
@app.get("/ai-provider-status", tags=["AI"])
async def ai_provider_status():
    ai_service = get_ai_service()
    return {
        "provider": "groq",
        "model": os.getenv("GROQ_MODEL", "mixtral-8x7b-32768"),
        "status": "healthy" if ai_service else "unavailable",
        "available": ai_service is not None
    }


# PRIORITY 22: Memory Cache Metrics Endpoint
@app.get("/cache-status", tags=["Admin"])
async def cache_status():
    return {
        "cache_size": len(dashboard_cache),
        "cache_maxsize": dashboard_cache.maxsize,
        "cache_ttl_seconds": 60,
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# DASHBOARD ENDPOINT (PRIORITY 8 - Optimized)
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
                "schema_version": schema_info.get("app_version", "6.0"),
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.exception("Dashboard error")
        raise HTTPException(status_code=500, detail="Internal server error")


# ==========================================================
# CHAT ENDPOINT (PRIORITY 1 - Using ChatService)
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
# PRIORITY 9: STATUS ENDPOINT WITH CACHING
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
        "version": "6.0.0",
        "database": "postgresql",
        "ai_provider": "groq",
        "whatsapp": "active",
        "statistics": {
            "total_customers": dashboard_data.get("total_customers", 0),
            "total_conversations": dashboard_data.get("total_conversations", 0),
            "total_delivery_records": dashboard_data.get("total_records", 0)
        },
        "schema": {
            "app_version": schema_info.get("app_version", "6.0"),
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
        "version": "6.0.0",
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
# PRIORITY 25: API VERSIONING (Legacy routes preserved)
# ==========================================================
# All existing routes are at root level for backward compatibility
# New API version can be added as: /api/v1/chat, etc.

# Add API version prefix info
@app.get("/api-info", tags=["Info"])
async def api_info():
    return {
        "current_version": "v1",
        "endpoints": {
            "chat": "/chat",
            "dashboard": "/dashboard",
            "health": "/health",
            "webhook": "/webhook/"
        },
        "documentation": "/api/docs"
    }


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v6.0 - Enterprise Grade (All Improvements Inline)")
logger.info("   Features: Request ID | Security Headers | Global Error Handler")
logger.info("   Caching: Dashboard (60s) | Status (60s) | Memory Cache")
logger.info("   Security: CORS | Trusted Host | Rate Limiting")
logger.info("   No New Files Created - All Services Inline")
logger.info("=" * 60)
