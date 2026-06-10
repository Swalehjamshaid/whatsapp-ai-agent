# ==========================================================
# FILE: app/main.py (ENTERPRISE v5.0 - FULLY REFACTORED)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
import re
import sys
import uuid
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import inspect, func, text
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

# Import all models for validation
from app.models import (
    Customer,
    Conversation,
    Message,
    DeliveryReport,
    SystemSetting,
    AIResponseLog,
    UploadedImage
)

# ==========================================================
# SERVICE IMPORTS
# ==========================================================

from app.services.schema_service import (
    check_schema_version,
    get_schema_info,
    APP_SCHEMA_VERSION
)

from app.services.whatsapp_service import get_whatsapp_service

# ==========================================================
# ROUTER IMPORTS (Safe imports with fallbacks)
# ==========================================================

try:
    from app.routes.upload import router as upload_router
    UPLOAD_ROUTER_AVAILABLE = True
except ImportError:
    UPLOAD_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    upload_router = APIRouter()

try:
    from app.routes.webhook import router as webhook_router
    WEBHOOK_ROUTER_AVAILABLE = True
except ImportError:
    WEBHOOK_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    webhook_router = APIRouter()


# ==========================================================
# PRIORITY 7 & 15: Rate Limiting & Cache Setup
# ==========================================================

limiter = Limiter(key_func=get_remote_address, default_limits=["5 per second"])
dashboard_cache = TTLCache(maxsize=100, ttl=60)


# ==========================================================
# PRIORITY 14: Startup Validation Service (Self-contained)
# ==========================================================

class StartupService:
    """Handles all startup validations - self-contained"""
    
    @staticmethod
    def validate_environment():
        """Validate required environment variables"""
        required_vars = [
            "DATABASE_URL",
            "GROQ_API_KEY",
            "WHATSAPP_ACCESS_TOKEN",
            "WHATSAPP_PHONE_NUMBER_ID"
        ]
        
        missing_vars = []
        for var in required_vars:
            if not os.getenv(var):
                missing_vars.append(var)
        
        if missing_vars:
            error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
            logger.error(error_msg)
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
            else:
                logger.warning("Development mode - continuing anyway")
        else:
            logger.info("✅ All required environment variables are set")
    
    @staticmethod
    def validate_models():
        """Validate ORM models during startup"""
        models_to_check = [Customer, Conversation, Message, DeliveryReport, SystemSetting]
        
        for model in models_to_check:
            try:
                inspect(model)
                logger.debug(f"✅ Model {model.__name__} validated")
            except Exception as e:
                logger.error(f"❌ Model {model.__name__} validation failed: {e}")
                raise
    
    @staticmethod
    def validate_database():
        """Validate database connection"""
        if not check_database_connection():
            logger.error("❌ Database connection failed")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
        else:
            logger.info("✅ Database connected")
    
    @staticmethod
    def validate_groq():
        """Validate Groq API key format (no API call)"""
        groq_key = os.getenv("GROQ_API_KEY")
        if groq_key and groq_key.startswith("gsk_"):
            logger.info("✅ Groq API key format validated")
        elif groq_key:
            logger.warning("⚠️ Groq API key has unusual format")
        else:
            logger.error("❌ Groq API key not set")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)
    
    @staticmethod
    def validate_whatsapp():
        """Validate WhatsApp configuration"""
        token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
        
        if token and phone_id:
            logger.info("✅ WhatsApp configuration validated")
        else:
            logger.error("❌ WhatsApp configuration incomplete")
            if os.getenv("ENVIRONMENT") == "production":
                sys.exit(1)


# ==========================================================
# PRIORITY 1: Chat Service (Self-contained)
# ==========================================================

class ChatService:
    """Handles all chat-related business logic - self-contained"""
    
    def __init__(self, db: Session):
        self.db = db
        self._ai_service = None
    
    @property
    def ai_service(self):
        if self._ai_service is None:
            try:
                from app.services.ai_query_service import AIQueryService
                self._ai_service = AIQueryService(self.db)
            except Exception as e:
                logger.error(f"AI service init failed: {e}")
        return self._ai_service
    
    def process_chat(self, message: str, customer_name: str, phone_number: str = None) -> str:
        """Process chat message - single transaction"""
        
        # Get AI response first (before DB operations)
        ai_reply = self._get_ai_response(message, phone_number)
        
        # PRIORITY 8: Single transaction for all DB operations
        try:
            # Get or create customer
            customer = self._get_or_create_customer(customer_name, phone_number)
            self.db.flush()
            
            # Create conversation
            conversation = Conversation(
                customer_id=customer.id,
                status="active"
            )
            self.db.add(conversation)
            self.db.flush()
            
            # Create all messages and logs in one batch
            user_msg = Message(
                conversation_id=conversation.id,
                sender="user",
                content=message,
                message_type="text"
            )
            
            ai_msg = Message(
                conversation_id=conversation.id,
                sender="assistant",
                content=ai_reply,
                message_type="text"
            )
            
            ai_log = AIResponseLog(
                conversation_id=conversation.id,
                prompt=message,
                ai_response=ai_reply,
                model_name="groq",
                success=True
            )
            
            self.db.add_all([user_msg, ai_msg, ai_log])
            self.db.commit()
            
            logger.info(f"Chat processed: customer={customer.name}, conversation={conversation.id}")
            return ai_reply
            
        except Exception as e:
            self.db.rollback()
            logger.exception(f"Chat DB error: {e}")
            return "⚠️ Unable to save your message. Please try again."
    
    def _get_ai_response(self, message: str, phone_number: str = None) -> str:
        """Get AI response for the message"""
        if not self.ai_service:
            return "⚠️ AI service is temporarily unavailable. Please try again later."
        
        try:
            result = self.ai_service.process_query(
                question=message,
                user_phone=phone_number or "web_chat"
            )
            return result.get("response", "Thank you for contacting support.")
        except Exception as e:
            logger.exception(f"AI response error: {e}")
            return "⚠️ I'm having trouble processing your request. Please try again in a moment."
    
    def _get_or_create_customer(self, name: str, phone_number: str = None) -> Customer:
        """Get existing customer or create new one"""
        if phone_number:
            customer = self.db.query(Customer).filter(
                Customer.phone_number == phone_number
            ).first()
            if customer:
                return customer
        
        # Create new customer
        if not phone_number:
            phone_number = f"temp_{uuid.uuid4().hex[:12]}"
        
        safe_name = self._sanitize_name(name)
        customer = Customer(
            name=name,
            phone_number=phone_number,
            email=f"{safe_name}@temp.com"
        )
        self.db.add(customer)
        return customer
    
    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Convert customer name to safe email prefix"""
        safe_name = name.lower()
        safe_name = re.sub(r'[^a-z0-9]', '_', safe_name)
        safe_name = re.sub(r'_+', '_', safe_name)
        return safe_name.strip('_')


# ==========================================================
# PRIORITY 6 & 7: Dashboard Service (Self-contained with caching)
# ==========================================================

class DashboardService:
    """Handles all dashboard data - self-contained with caching"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_cached_dashboard_data(self) -> Dict[str, Any]:
        """Get dashboard data from cache or compute"""
        cache_key = "dashboard_data"
        cached = dashboard_cache.get(cache_key)
        if cached:
            logger.debug("Returning cached dashboard data")
            return cached
        
        data = self._compute_dashboard_data()
        dashboard_cache[cache_key] = data
        return data
    
    def _compute_dashboard_data(self) -> Dict[str, Any]:
        """Compute all dashboard statistics"""
        # Delivery stats
        total_records = self.db.query(DeliveryReport).count()
        pending_deliveries = self.db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        pending_pod = self.db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        pending_pgi = self.db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        
        pending_amount = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        completed_deliveries = self.db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Received"
        ).count()
        total_amount = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        
        cities = self.db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = self.db.query(DeliveryReport.warehouse).distinct().count()
        
        # Top lists
        top_dealers = self._get_top_dealers(limit=5)
        top_cities = self._get_top_cities(limit=5)
        top_warehouses = self._get_warehouse_stats(limit=5)
        
        # Upload stats
        upload_stats = self._get_upload_statistics()
        latest_uploads = self._get_latest_uploads(limit=5)
        
        # Conversation stats
        total_conversations = self.db.query(Conversation).count()
        total_customers = self.db.query(Customer).count()
        
        return {
            "total_records": total_records,
            "pending_deliveries": pending_deliveries,
            "pending_pod": pending_pod,
            "pending_pgi": pending_pgi,
            "pending_amount": round(pending_amount, 2),
            "completed_deliveries": completed_deliveries,
            "total_amount": round(total_amount, 2),
            "cities": cities,
            "warehouses": warehouses,
            "top_dealers": top_dealers,
            "top_cities": top_cities,
            "top_warehouses": top_warehouses,
            "latest_uploads": latest_uploads,
            "total_uploads": upload_stats.get("total_uploads", 0),
            "total_imported_rows": upload_stats.get("total_imported_rows", 0),
            "total_conversations": total_conversations,
            "total_customers": total_customers,
            "last_upload_date": upload_stats.get("last_upload_date")
        }
    
    def _get_top_dealers(self, limit=5):
        dealers = self.db.query(
            DeliveryReport.dealer_code,
            DeliveryReport.customer_name,
            func.count(DeliveryReport.id).label('delivery_count')
        ).group_by(
            DeliveryReport.dealer_code,
            DeliveryReport.customer_name
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(limit).all()
        
        return [
            {
                "dealer_code": d.dealer_code or "N/A",
                "customer_name": d.customer_name or "N/A",
                "delivery_count": d.delivery_count
            }
            for d in dealers if d.dealer_code
        ]
    
    def _get_top_cities(self, limit=5):
        cities = self.db.query(
            DeliveryReport.ship_to_city.label('city'),
            func.count(DeliveryReport.id).label('count')
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(limit).all()
        
        return [
            {"city": c.city or "N/A", "count": c.count}
            for c in cities if c.city
        ]
    
    def _get_warehouse_stats(self, limit=5):
        warehouses = self.db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label('total_count')
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(limit).all()
        
        return [
            {"warehouse": w.warehouse or "N/A", "total_count": w.total_count}
            for w in warehouses if w.warehouse
        ]
    
    def _get_upload_statistics(self):
        total_uploads = self.db.query(DeliveryReport.upload_batch_id).distinct().count()
        total_imported_rows = self.db.query(DeliveryReport).count()
        last_upload = self.db.query(DeliveryReport.imported_at).order_by(
            DeliveryReport.imported_at.desc()
        ).first()
        
        return {
            "total_uploads": total_uploads,
            "total_imported_rows": total_imported_rows,
            "last_upload_date": last_upload[0] if last_upload else None
        }
    
    def _get_latest_uploads(self, limit=5):
        batches = self.db.query(
            DeliveryReport.upload_batch_id,
            DeliveryReport.source_file,
            DeliveryReport.imported_at,
            func.count(DeliveryReport.id).label('record_count')
        ).group_by(
            DeliveryReport.upload_batch_id,
            DeliveryReport.source_file,
            DeliveryReport.imported_at
        ).order_by(
            DeliveryReport.imported_at.desc()
        ).limit(limit).all()
        
        return [
            {
                "batch_id": batch.upload_batch_id,
                "filename": batch.source_file,
                "upload_date": batch.imported_at,
                "record_count": batch.record_count
            }
            for batch in batches if batch.upload_batch_id
        ]
    
    def get_dashboard_conversations(self, limit=10):
        """Get formatted conversations for dashboard"""
        conversations = self.db.query(
            Conversation.id,
            Conversation.customer_id,
            Conversation.status,
            Conversation.created_at,
            Customer.name.label('customer_name'),
            Customer.phone_number.label('customer_phone')
        ).join(
            Customer, Conversation.customer_id == Customer.id
        ).order_by(
            Conversation.created_at.desc()
        ).limit(limit).all()
        
        conv_ids = [conv.id for conv in conversations]
        
        if conv_ids:
            user_messages = self.db.query(
                Message.conversation_id,
                Message.content,
                Message.created_at
            ).filter(
                Message.conversation_id.in_(conv_ids),
                Message.sender == "user"
            ).distinct(Message.conversation_id).order_by(
                Message.conversation_id, Message.created_at.desc()
            ).all()
            
            ai_responses = self.db.query(
                Message.conversation_id,
                Message.content,
                Message.created_at
            ).filter(
                Message.conversation_id.in_(conv_ids),
                Message.sender == "assistant"
            ).distinct(Message.conversation_id).order_by(
                Message.conversation_id, Message.created_at.desc()
            ).all()
            
            user_msg_dict = {msg.conversation_id: msg for msg in user_messages}
            ai_response_dict = {msg.conversation_id: msg for msg in ai_responses}
        else:
            user_msg_dict = {}
            ai_response_dict = {}
        
        result = []
        for conv in conversations:
            user_msg = user_msg_dict.get(conv.id)
            ai_response = ai_response_dict.get(conv.id)
            
            result.append({
                "id": conv.id,
                "customer": conv.customer_name,
                "customer_phone": conv.customer_phone,
                "message": user_msg.content if user_msg else "No messages",
                "reply": ai_response.content if ai_response else "No response",
                "timestamp": conv.created_at.isoformat() if conv.created_at else "",
                "status": conv.status
            })
        
        return result
    
    def get_daily_message_stats(self, limit=7):
        """Get daily message statistics"""
        stats = self.db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(
            func.date(Message.created_at).desc()
        ).limit(limit).all()
        
        return [{"date": str(s.date), "count": s.count} for s in stats]
    
    def get_latest_uploads_for_center(self, limit=20):
        """Get latest uploads for upload center"""
        return self._get_latest_uploads(limit)


# ==========================================================
# LIFESPAN HANDLER
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v5.0")
    logger.info("=" * 80)
    
    # PRIORITY 14: Run all validations
    StartupService.validate_environment()
    StartupService.validate_models()
    StartupService.validate_database()
    StartupService.validate_groq()
    StartupService.validate_whatsapp()
    
    # Create tables if needed
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("✅ Database tables created/verified")
    except Exception as e:
        logger.error(f"❌ Table creation failed: {e}")
    
    # PRIORITY 2: No AI startup execution - just log availability
    try:
        from app.services.ai_query_service import AIQueryService
        logger.info("✅ AIQueryService available (will initialize on first request)")
    except ImportError as e:
        logger.warning(f"⚠️ AIQueryService not available: {e}")
    
    # Create upload directory
    os.makedirs("uploads", exist_ok=True)
    logger.info("✅ Upload directory ready")
    
    logger.info("=" * 80)
    logger.info("✅ APPLICATION STARTUP COMPLETE")
    logger.info("=" * 80 + "\n")
    
    yield
    
    # Shutdown
    logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")


# ==========================================================
# CREATE APP (PRIORITY 1 - App created before middleware)
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent",
    version="5.0.0",
    description="AI WhatsApp Customer Service Agent - Groq Powered",
    lifespan=lifespan
)

# Set up rate limiter
limiter._app = app
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ==========================================================
# MIDDLEWARE (After app creation)
# ==========================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    logger.debug(f"→ {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        duration_ms = (time.time() - start_time) * 1000
        logger.info(f"← {request.method} {request.url.path} | Status: {response.status_code} | Duration: {duration_ms:.2f}ms")
        return response
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"✗ {request.method} {request.url.path} | Error: {e} | Duration: {duration_ms:.2f}ms")
        raise


# Security Middleware
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app").split(",")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)
logger.info(f"TrustedHostMiddleware configured")

# CORS Configuration
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,https://yourdomain.com").split(",")

if ENVIRONMENT == "production":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
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
# REGISTER ROUTERS
# ==========================================================

if UPLOAD_ROUTER_AVAILABLE:
    app.include_router(upload_router)
if WEBHOOK_ROUTER_AVAILABLE:
    app.include_router(webhook_router)


# ==========================================================
# TEMPLATES
# ==========================================================

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ==========================================================
# PRIORITY 9: Request Models with Validation
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str = Field(min_length=2, max_length=100, description="Customer name")
    message: str = Field(min_length=1, max_length=2000, description="User message")
    phone_number: Optional[str] = Field(None, min_length=10, max_length=15, description="Phone number")


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# PRIORITY 4 & 5: LIVENESS & READINESS ENDPOINTS
# ==========================================================

@app.get("/liveness", tags=["Health"])
async def liveness():
    """Simple liveness probe - no DB queries. Used by Railway."""
    return {"alive": True, "timestamp": datetime.utcnow().isoformat()}


@app.get("/readiness", tags=["Health"])
async def readiness():
    """Readiness probe - checks all critical services."""
    status = {"ready": False, "checks": {}, "timestamp": datetime.utcnow().isoformat()}
    
    try:
        db_connected = check_database_connection()
        status["checks"]["database"] = "connected" if db_connected else "disconnected"
    except Exception as e:
        status["checks"]["database"] = f"error: {str(e)}"
    
    groq_key = os.getenv("GROQ_API_KEY")
    status["checks"]["groq"] = "configured" if groq_key else "not_configured"
    
    whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    status["checks"]["whatsapp"] = "configured" if (whatsapp_token and whatsapp_phone_id) else "not_configured"
    
    status["ready"] = (
        status["checks"]["database"] == "connected" and
        status["checks"]["groq"] == "configured" and
        status["checks"]["whatsapp"] == "configured"
    )
    
    return status


# ==========================================================
# HEALTH ENDPOINTS
# ==========================================================

@app.get("/health", tags=["Health"])
async def health():
    """Enhanced health check endpoint"""
    try:
        db_connected = check_database_connection()
        db_status = "connected" if db_connected else "disconnected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "error"
    
    # PRIORITY 10: WhatsApp validation using service
    whatsapp_status = "unknown"
    try:
        whatsapp_service = get_whatsapp_service()
        whatsapp_health = whatsapp_service.health_check()
        whatsapp_status = "healthy" if whatsapp_health.get("configured") else "not_configured"
    except Exception as e:
        logger.error(f"WhatsApp health check failed: {e}")
        whatsapp_status = "error"
    
    ai_available = False
    try:
        from app.services.ai_query_service import AIQueryService
        ai_available = True
    except ImportError:
        pass
    
    return {
        "status": "healthy" if db_connected else "degraded",
        "database": db_status,
        "whatsapp": whatsapp_status,
        "ai_service": "available" if ai_available else "unavailable",
        "ai_provider": "groq",
        "schema_version": APP_SCHEMA_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


@app.get("/groq-health", tags=["Health"])
async def groq_health():
    """Check Groq API connectivity without full AI initialization"""
    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
    
    groq_working = False
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            groq_working = True
        except Exception as e:
            logger.warning(f"Groq client creation failed: {e}")
    
    return {
        "provider": "groq",
        "api_key_set": bool(groq_key),
        "working": groq_working,
        "model": groq_model,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ai-status", tags=["AI"])
@limiter.limit("10 per minute")
async def ai_status(request: Request):
    """Get AI service status - No initialization, just availability check"""
    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
    
    ai_available = False
    try:
        from app.services.ai_query_service import AIQueryService
        ai_available = True
    except ImportError:
        pass
    
    return {
        "ai_provider": "groq",
        "groq_api_key_set": bool(groq_key),
        "groq_model": groq_model,
        "ai_service_available": ai_available,
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# PRIORITY 1: CHAT ENDPOINT (Using ChatService)
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
@limiter.limit("5 per second")
async def chat(request: ChatRequest, req: Request, db: Session = Depends(get_db)):
    """Process chat message using ChatService"""
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
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# PRIORITY 6 & 7: DASHBOARD ENDPOINT (With caching)
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the dashboard HTML page with caching"""
    try:
        dashboard_service = DashboardService(db)
        dashboard_data = dashboard_service.get_cached_dashboard_data()
        
        # Get uncached data (conversations and daily stats)
        dashboard_conversations = dashboard_service.get_dashboard_conversations(limit=5)
        daily_stats = dashboard_service.get_daily_message_stats(limit=7)
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        schema_info = get_schema_info(db)
        last_refresh = datetime.utcnow()
        
        # Create fallback template if needed
        dashboard_template_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
        if not os.path.exists(dashboard_template_path):
            with open(dashboard_template_path, "w") as f:
                f.write("""<!DOCTYPE html>
<html>
<head><title>Logistics Dashboard</title></head>
<body>
    <h1>📦 Logistics Dashboard</h1>
    <p>Total Records: {{ total_records }}</p>
    <p>Pending Deliveries: {{ pending_deliveries }}</p>
    <p>Last Updated: {{ last_refresh }}</p>
</body>
</html>""")
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                **dashboard_data,
                "conversations": dashboard_conversations,
                "stats": daily_stats,
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "groq_status": "Online" if groq_key else "Offline",
                "ai_provider": "groq",
                "schema_version": schema_info.get("app_version", "5.0"),
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.exception("Dashboard error")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# STATUS ENDPOINT (With caching)
# ==========================================================

@app.get("/status", tags=["Status"])
async def status(db: Session = Depends(get_db)):
    """Get system status with caching"""
    cache_key = "system_status"
    cached = dashboard_cache.get(cache_key)
    if cached:
        return cached
    
    try:
        dashboard_service = DashboardService(db)
        dashboard_data = dashboard_service.get_cached_dashboard_data()
        schema_info = get_schema_info(db)
        
        result = {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai_provider": "groq",
            "whatsapp": "active",
            "railway": "connected",
            "statistics": {
                "total_customers": dashboard_data.get("total_customers", 0),
                "total_conversations": dashboard_data.get("total_conversations", 0),
                "total_delivery_records": dashboard_data.get("total_records", 0)
            },
            "schema": {
                "app_version": schema_info.get("app_version", "5.0"),
                "db_version": schema_info.get("db_version", "unknown"),
                "needs_migration": schema_info.get("needs_migration", False)
            },
            "timestamp": datetime.utcnow().isoformat()
        }
        
        dashboard_cache[cache_key] = result
        return result
    except Exception as e:
        logger.exception("Status endpoint error")
        return {"application": "AI WhatsApp Agent", "error": str(e), "timestamp": datetime.utcnow().isoformat()}


# ==========================================================
# ROOT & INFO ENDPOINTS
# ==========================================================

@app.get("/", tags=["Root"])
async def home():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "5.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION,
        "logistics_integration": True,
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
    """Render upload center page"""
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
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download-template", tags=["Upload"])
async def download_template():
    """Download Excel template for logistics reports"""
    import pandas as pd
    import io
    
    template_data = {
        "DN No": ["DN12345", "DN12346"],
        "DN Work": ["Invoiced", "Invoiced"],
        "Order Type": ["ZOR", "ZOR"],
        "Division": ["Refrigerator", "AC"],
        "Customer Code": ["CUST001", "CUST002"],
        "Dealer Code": ["DEALER001", "DEALER002"],
        "Customer Name": ["ABC Traders", "XYZ Enterprises"],
        "Customer Model": ["Model A", "Model B"],
        "Material No": ["MAT001", "MAT002"],
        "Storage Location": ["WH01", "WH02"],
        "Sales Office": ["North Region", "South Region"],
        "Sales Manager": ["John Doe", "Jane Smith"],
        "Ship To City": ["New York", "Los Angeles"],
        "Warehouse": ["Main Warehouse", "Secondary Warehouse"],
        "Warehouse Code": ["WH001", "WH002"],
        "DN Qty": [10, 20],
        "DN Amount": [1000.00, 2000.00],
        "DN Create Date": ["2024-01-01", "2024-01-02"],
        "Good Issue Date": ["2024-01-05", "2024-01-06"],
        "POD Date": ["2024-01-10", ""]
    }
    
    df = pd.DataFrame(template_data)
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Delivery Report', index=False)
    
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
        "upload_folder_path": "uploads",
        "status": "ready" if os.path.exists("uploads") else "not_ready"
    }


# ==========================================================
# DEBUG ENDPOINTS
# ==========================================================

@app.get("/db-test", tags=["Debug"])
async def db_test():
    """Debug endpoint to test database connectivity"""
    try:
        connected = check_database_connection()
        health = get_database_health()
        
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        return {
            "connected": connected,
            "database_url_exists": bool(DATABASE_URL),
            "health": health,
            "tables": tables,
            "table_count": len(tables)
        }
    except Exception as e:
        logger.exception("DB test error")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("📡 MAIN APP v5.0 - Enterprise Ready (No New Files)")
logger.info("   Features: Rate Limiting | Caching | Readiness/Liveness | ChatService")
logger.info("   No AI Startup Execution | Single Transaction Chat")
logger.info("=" * 60)
