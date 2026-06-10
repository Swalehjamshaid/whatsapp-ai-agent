# ==========================================================
# FILE: app/main.py (ENTERPRISE v4.0 - FULLY FIXED)
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
import re
import uuid
import time
import sys
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload, selectinload
from sqlalchemy import inspect, func, text, case
from loguru import logger

# ==========================================================
# DATABASE IMPORTS (CRITICAL FIX #2 - Safe imports with fallbacks)
# ==========================================================

from app.database import (
    engine,
    DATABASE_URL,
    Base,
    get_db,
    SessionLocal,
)

# Safe imports for functions that may not exist yet
try:
    from app.database import check_database_connection
except ImportError:
    # Fallback definition
    def check_database_connection():
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1"))
            db.close()
            return True
        except:
            return False
    logger.warning("⚠️ check_database_connection not found in database.py, using fallback")

try:
    from app.database import validate_database_setup
except ImportError:
    def validate_database_setup():
        logger.info("Database setup validation skipped (function not available)")
        return True
    logger.warning("⚠️ validate_database_setup not found in database.py, using fallback")

try:
    from app.database import get_database_health
except ImportError:
    def get_database_health():
        return {"connected": check_database_connection(), "database_type": "unknown"}
    logger.warning("⚠️ get_database_health not found in database.py, using fallback")

import app.models

# Import all models explicitly
from app.models import (
    Customer,
    Conversation,
    Message,
    UploadedImage,
    AIResponseLog,
    DeliveryReport,
    SystemSetting
)

# Import schema service functions
from app.services.schema_service import (
    check_schema_version,
    get_schema_info,
    APP_SCHEMA_VERSION
)

# WhatsApp service import
from app.services.whatsapp_service import send_text_message

# ==========================================================
# CRITICAL FIX #3: Safe Router Imports with Fallbacks
# ==========================================================

# Try to import routers with fallbacks
try:
    from app.routes.upload import router as upload_router
    UPLOAD_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Upload router not available: {e}")
    UPLOAD_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    upload_router = APIRouter()
    @upload_router.get("/upload-status")
    async def upload_status_fallback():
        return {"status": "router_not_available"}

try:
    from app.routes.webhook import router as webhook_router
    WEBHOOK_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Webhook router not available: {e}")
    WEBHOOK_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    webhook_router = APIRouter()

try:
    from app.routes.dashboard import router as dashboard_router
    DASHBOARD_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Dashboard router not available: {e}")
    DASHBOARD_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    dashboard_router = APIRouter()

try:
    from app.routes.logistics import router as logistics_router
    LOGISTICS_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Logistics router not available: {e}")
    LOGISTICS_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    logistics_router = APIRouter()

try:
    from app.routes.customers import router as customers_router
    CUSTOMERS_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Customers router not available: {e}")
    CUSTOMERS_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    customers_router = APIRouter()

try:
    from app.routes.conversations import router as conversations_router
    CONVERSATIONS_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Conversations router not available: {e}")
    CONVERSATIONS_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    conversations_router = APIRouter()

try:
    from app.routes.analytics import router as analytics_router
    ANALYTICS_ROUTER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"⚠️ Analytics router not available: {e}")
    ANALYTICS_ROUTER_AVAILABLE = False
    from fastapi import APIRouter
    analytics_router = APIRouter()

# ==========================================================
# CRITICAL FIX #4: Safe Dashboard Service Imports
# ==========================================================

DASHBOARD_SERVICE_AVAILABLE = False
try:
    from app.services.dashboard_service import (
        get_dashboard_stats,
        get_top_dealers,
        get_top_cities,
        get_warehouse_stats,
        get_upload_statistics,
        get_latest_uploads,
        get_dashboard_conversations
    )
    DASHBOARD_SERVICE_AVAILABLE = True
    logger.info("✅ Dashboard service imported successfully")
except ImportError as e:
    logger.warning(f"⚠️ Dashboard service not available: {e}")
    # Create fallback functions
    def get_dashboard_stats(db):
        return {"total_records": 0, "pending_deliveries": 0, "pending_pod": 0, "pending_pgi": 0, "pending_amount": 0, "completed_deliveries": 0, "total_amount": 0, "cities": 0, "warehouses": 0}
    def get_top_dealers(db, limit=5): return []
    def get_top_cities(db, limit=5): return []
    def get_warehouse_stats(db, limit=5): return []
    def get_upload_statistics(db): return {"total_uploads": 0, "total_imported_rows": 0, "last_upload_date": None}
    def get_latest_uploads(db, limit=5): return []
    def get_dashboard_conversations(db, limit=5): return []

# ==========================================================
# PRIORITY 9: Environment Validation (CRITICAL FIX #5)
# ==========================================================

REQUIRED_ENV_VARS = [
    "DATABASE_URL",
    "GROQ_API_KEY",
    "WHATSAPP_ACCESS_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID"
]

OPTIONAL_ENV_VARS = [
    "WHATSAPP_VERIFY_TOKEN",
    "ENVIRONMENT",
    "GROQ_MODEL"
]

def validate_environment():
    """Validate required environment variables at startup - CRITICAL FIX #5"""
    missing_vars = []
    for var in REQUIRED_ENV_VARS:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
        logger.error(error_msg)
        # CRITICAL FIX #5: Exit on missing required vars in production
        if os.getenv("ENVIRONMENT") == "production":
            logger.error("Production environment - exiting due to missing required variables")
            sys.exit(1)
        else:
            logger.warning("Development mode - continuing anyway")
    else:
        logger.info("✅ All required environment variables are set")
    
    # Log optional vars status
    for var in OPTIONAL_ENV_VARS:
        if os.getenv(var):
            logger.debug(f"Optional var {var} is set")
        else:
            logger.debug(f"Optional var {var} is not set (using default)")

# ==========================================================
# Safe AI Query Service Import
# ==========================================================

AI_QUERY_AVAILABLE = False
AIQueryService = None

try:
    from app.services.ai_query_service import AIQueryService
    AI_QUERY_AVAILABLE = True
    logger.info("✅ AIQueryService imported successfully")
except ImportError as e:
    logger.error(f"❌ AIQueryService Import Failed: {e}")
except Exception as e:
    logger.error(f"❌ AIQueryService initialization error: {e}")

# ==========================================================
# Startup Diagnostics (Using logger)
# ==========================================================

def print_startup_diagnostics():
    """Print comprehensive startup diagnostics using logger"""
    logger.info("=" * 80)
    logger.info("SYSTEM DIAGNOSTICS")
    logger.info("=" * 80)
    
    # Database
    db_connected = check_database_connection()
    logger.info(f"Database: {'✅ CONNECTED' if db_connected else '❌ FAILED'}")
    
    # Groq
    groq_key = os.getenv("GROQ_API_KEY")
    logger.info(f"Groq API Key: {'✅ SET' if groq_key else '❌ NOT SET'}")
    
    # WhatsApp
    whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_verify = os.getenv("WHATSAPP_VERIFY_TOKEN")
    logger.info(f"WhatsApp Token: {'✅ SET' if whatsapp_token else '❌ NOT SET'}")
    logger.info(f"WhatsApp Phone ID: {'✅ SET' if whatsapp_phone_id else '❌ NOT SET'}")
    logger.info(f"WhatsApp Verify Token: {'✅ SET' if whatsapp_verify else '❌ NOT SET'}")
    
    # AI Service
    logger.info(f"AIQueryService: {'✅ AVAILABLE' if AI_QUERY_AVAILABLE else '❌ UNAVAILABLE'}")
    
    # Router Status
    logger.info(f"Routers: Upload={'✅' if UPLOAD_ROUTER_AVAILABLE else '❌'}, "
               f"Webhook={'✅' if WEBHOOK_ROUTER_AVAILABLE else '❌'}, "
               f"Dashboard={'✅' if DASHBOARD_ROUTER_AVAILABLE else '❌'}")
    
    # Environment
    logger.info(f"Environment: {os.getenv('ENVIRONMENT', 'production')}")
    logger.info(f"Railway: {'✅ YES' if os.getenv('RAILWAY_ENVIRONMENT') else '❌ NO'}")
    
    # Python version
    logger.info(f"Python: {sys.version}")
    
    logger.info("=" * 80)


# ==========================================================
# LIFESPAN HANDLER (Safe Startup)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("=" * 80)
    logger.info("🤖 AI WHATSAPP AGENT STARTING v4.0")
    logger.info("=" * 80)
    
    # Railway environment info
    logger.info(f"Railway Environment: {os.getenv('RAILWAY_ENVIRONMENT', 'Not Railway')}")
    logger.info(f"Database URL Exists: {bool(DATABASE_URL)}")
    
    # Priority 9: Validate environment first
    validate_environment()
    
    # Priority 7: Print diagnostics
    print_startup_diagnostics()
    
    # Safe AI Service Test (using health_check only)
    if AI_QUERY_AVAILABLE:
        try:
            test_db = SessionLocal()
            ai_service = AIQueryService(test_db)
            # Use health_check instead of full process_query
            health = ai_service.health_check()
            logger.info(f"✅ AI Query Service health check passed: {health.get('status', 'unknown')}")
            test_db.close()
        except Exception as e:
            logger.error(f"⚠️ AI Query Service initialization warning: {e}")
    else:
        logger.warning("⚠️ AI Query Service not available - skipping initialization")
    
    # Safe Table Creation
    logger.info("\n📊 DATABASE SETUP")
    logger.info("-" * 40)
    
    try:
        # Test database connection first
        if not check_database_connection():
            logger.error("❌ Database Connection Failed - Starting in limited mode")
        else:
            logger.info("✅ Database Connection Successful")
            
            # Safe table creation
            try:
                Base.metadata.create_all(bind=engine)
                logger.info("✅ Tables created/verified successfully")
            except Exception as e:
                logger.error(f"Table creation error: {e}")
            
            # Show actual tables
            try:
                inspector = inspect(engine)
                tables = inspector.get_table_names()
                logger.info(f"📊 Tables in database: {len(tables)}")
                if tables:
                    logger.info(f"   Tables: {', '.join(tables[:10])}")
                    if len(tables) > 10:
                        logger.info(f"   ... and {len(tables) - 10} more")
            except Exception as e:
                logger.warning(f"Could not inspect tables: {e}")
            
            # Safe schema check
            db = SessionLocal()
            try:
                check_schema_version(db)
                logger.info("✅ Schema check completed")
                
                schema_info = get_schema_info(db)
                logger.info(f"📊 Schema: App v{schema_info['app_version']}, DB v{schema_info.get('db_version', 'unknown')}")
                if schema_info.get('needs_migration'):
                    logger.warning(f"⚠️ Schema migration needed")
            except Exception as e:
                logger.exception("Schema check failed")
                logger.warning(f"⚠️ Schema check failed: {e}")
            finally:
                db.close()
    
    except Exception as e:
        logger.error(f"Database setup error: {e}")
    
    # Create upload directory
    logger.info("\n📁 FILE SYSTEM")
    logger.info("-" * 40)
    try:
        os.makedirs("uploads", exist_ok=True)
        logger.info("✅ Upload directory ready")
    except Exception as e:
        logger.warning(f"Could not create upload directory: {e}")
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ APPLICATION STARTUP COMPLETE")
    logger.info("=" * 80 + "\n")
    
    yield
    
    # Shutdown
    logger.info("\n" + "=" * 80)
    logger.info("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
    logger.info("=" * 80)


# ==========================================================
# CRITICAL FIX #1: CREATE APP BEFORE MIDDLEWARE
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent",
    version="4.0.0",
    description="AI WhatsApp Customer Service Agent - Groq Powered",
    lifespan=lifespan
)

# ==========================================================
# CRITICAL FIX #1: MIDDLEWARE AFTER APP CREATION
# ==========================================================

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests with timing information"""
    start_time = time.time()
    
    # Log request
    logger.debug(f"→ {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        
        # Calculate duration
        duration_ms = (time.time() - start_time) * 1000
        
        # Log response
        logger.info(
            f"← {request.method} {request.url.path} | "
            f"Status: {response.status_code} | "
            f"Duration: {duration_ms:.2f}ms"
        )
        
        return response
    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        logger.error(f"✗ {request.method} {request.url.path} | Error: {e} | Duration: {duration_ms:.2f}ms")
        raise

# ==========================================================
# SECURITY: Trusted Host Middleware
# ==========================================================

# Add trusted host middleware for production
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,*.up.railway.app").split(",")
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=ALLOWED_HOSTS
)
logger.info(f"TrustedHostMiddleware configured with hosts: {ALLOWED_HOSTS}")

# ==========================================================
# CORS Configuration
# ==========================================================

# Get allowed origins from environment (comma-separated)
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8000,https://yourdomain.com").split(",")
ENVIRONMENT = os.getenv("ENVIRONMENT", "production")

if ENVIRONMENT == "production":
    # Production: strict origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    logger.info(f"CORS configured for production with origins: {ALLOWED_ORIGINS}")
else:
    # Development: allow all
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    logger.info("CORS configured for development (allow all)")

# ==========================================================
# REGISTER ROUTERS (Safe - only if available)
# ==========================================================

if UPLOAD_ROUTER_AVAILABLE:
    app.include_router(upload_router)
    logger.info("✅ Upload router registered")
else:
    logger.warning("⚠️ Upload router not registered")

if WEBHOOK_ROUTER_AVAILABLE:
    app.include_router(webhook_router)
    logger.info("✅ Webhook router registered")
else:
    logger.warning("⚠️ Webhook router not registered")

if DASHBOARD_ROUTER_AVAILABLE:
    app.include_router(dashboard_router)
    logger.info("✅ Dashboard router registered")
else:
    logger.warning("⚠️ Dashboard router not registered - using fallback endpoints")

if LOGISTICS_ROUTER_AVAILABLE:
    app.include_router(logistics_router)
    logger.info("✅ Logistics router registered")
else:
    logger.warning("⚠️ Logistics router not registered")

if CUSTOMERS_ROUTER_AVAILABLE:
    app.include_router(customers_router)
    logger.info("✅ Customers router registered")
else:
    logger.warning("⚠️ Customers router not registered")

if CONVERSATIONS_ROUTER_AVAILABLE:
    app.include_router(conversations_router)
    logger.info("✅ Conversations router registered")
else:
    logger.warning("⚠️ Conversations router not registered")

if ANALYTICS_ROUTER_AVAILABLE:
    app.include_router(analytics_router)
    logger.info("✅ Analytics router registered")
else:
    logger.warning("⚠️ Analytics router not registered")

# ==========================================================
# TEMPLATES
# ==========================================================

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)

templates = Jinja2Templates(directory=TEMPLATES_DIR)


# ==========================================================
# REQUEST / RESPONSE MODELS
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str
    message: str
    phone_number: Optional[str] = None


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# GROQ HEALTH ENDPOINT
# ==========================================================

@app.get("/groq-health", tags=["Health"])
async def groq_health():
    """Check Groq AI provider health"""
    groq_key = os.getenv("GROQ_API_KEY")
    
    groq_available = False
    groq_model = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
    
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            response = client.chat.completions.create(
                model=groq_model,
                messages=[{"role": "user", "content": "OK"}],
                max_tokens=5
            )
            groq_available = True
            logger.debug("Groq health check passed")
        except Exception as e:
            logger.warning(f"Groq health check failed: {e}")
    
    return {
        "provider": "groq",
        "api_key_set": bool(groq_key),
        "available": groq_available,
        "model": groq_model,
        "ai_query_service_available": AI_QUERY_AVAILABLE,
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# ROOT ENDPOINTS
# ==========================================================

@app.get("/", tags=["Root"])
async def home():
    """Redirect to dashboard"""
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/health", tags=["Health"])
async def health(db: Session = Depends(get_db)):
    """Enhanced health check endpoint"""
    try:
        db.execute(text("SELECT 1")).scalar()
        db_status = "connected"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        db_status = "disconnected"
    
    whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    uploads_folder_exists = os.path.exists("uploads")
    
    db_health = get_database_health()
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "database_details": db_health,
        "whatsapp": "connected" if whatsapp_token else "disconnected",
        "schema_version": APP_SCHEMA_VERSION,
        "uploads_folder": uploads_folder_exists,
        "ai_service": "available" if AI_QUERY_AVAILABLE else "unavailable",
        "ai_provider": "groq",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ai-status", tags=["AI"])
async def ai_status(db: Session = Depends(get_db)):
    """Get AI service status - Groq only"""
    try:
        db.execute(text("SELECT 1")).scalar()
        db_connected = True
    except:
        db_connected = False
    
    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "mixtral-8x7b-32768")
    ai_provider = os.getenv("AI_PROVIDER", "groq")
    
    ai_service_ready = False
    ai_service_error = None
    if AI_QUERY_AVAILABLE:
        try:
            test_db = SessionLocal()
            ai_service = AIQueryService(test_db)
            health = ai_service.health_check()
            ai_service_ready = health.get("status") == "healthy"
            test_db.close()
        except Exception as e:
            ai_service_error = str(e)
            logger.warning(f"AI service health check failed: {e}")
    
    return {
        "ai_provider": ai_provider,
        "groq_api_key_set": bool(groq_key),
        "groq_model": groq_model,
        "database_connected": db_connected,
        "ai_service_available": AI_QUERY_AVAILABLE,
        "ai_service_ready": ai_service_ready,
        "ai_service_error": ai_service_error,
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    """Simple ping endpoint for Railway health checks"""
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


@app.get("/db-health", tags=["Health"])
async def db_health(db: Session = Depends(get_db)):
    """Check database connectivity"""
    try:
        result = db.execute(text("SELECT 1")).scalar()
        return {
            "status": "healthy",
            "database": "connected",
            "query_result": result == 1,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {str(e)}"
        )


# ==========================================================
# UPLOAD ENDPOINTS
# ==========================================================

@app.get("/upload-center", tags=["Upload"])
async def upload_center(request: Request, db: Session = Depends(get_db)):
    """Render upload center page"""
    try:
        latest_uploads = get_latest_uploads(db, limit=20)
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
        logger.error(f"Upload center error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/download-template", tags=["Upload"])
async def download_template():
    """Download Excel template for logistics reports"""
    import pandas as pd
    import io
    from fastapi.responses import StreamingResponse
    
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
    """Check if upload directory is ready for Excel files"""
    return {
        "upload_folder_exists": os.path.exists("uploads"),
        "upload_folder_path": "uploads",
        "status": "ready" if os.path.exists("uploads") else "not_ready"
    }


# ==========================================================
# STATUS ENDPOINTS
# ==========================================================

@app.get("/status", tags=["Status"])
async def status(db: Session = Depends(get_db)):
    try:
        total_customers = db.query(Customer).count()
        total_conversations = db.query(Conversation).count()
        total_delivery_records = db.query(DeliveryReport).count()
        
        schema_info = get_schema_info(db)
        
        last_upload = db.query(DeliveryReport.imported_at).order_by(
            DeliveryReport.imported_at.desc()
        ).first()
        
        return {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai_provider": "groq",
            "ai_available": AI_QUERY_AVAILABLE,
            "whatsapp": "active",
            "railway": "connected",
            "statistics": {
                "total_customers": total_customers,
                "total_conversations": total_conversations,
                "total_delivery_records": total_delivery_records
            },
            "schema": {
                "app_version": schema_info["app_version"],
                "db_version": schema_info.get("db_version", "unknown"),
                "needs_migration": schema_info.get("needs_migration", False)
            },
            "last_upload_date": last_upload[0].isoformat() if last_upload else None,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Status endpoint error: {e}")
        return {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai_provider": "groq",
            "ai_available": AI_QUERY_AVAILABLE,
            "whatsapp": "active",
            "railway": "connected",
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat()
        }


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
        logger.error(f"DB test error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# DASHBOARD ENDPOINT
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the dashboard HTML page"""
    try:
        # Get all dashboard data from service
        stats = get_dashboard_stats(db)
        top_dealers = get_top_dealers(db, limit=5)
        top_cities = get_top_cities(db, limit=5)
        top_warehouses = get_warehouse_stats(db, limit=5)
        upload_stats = get_upload_statistics(db)
        latest_uploads = get_latest_uploads(db, limit=5)
        
        # Conversation stats
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        dashboard_conversations = get_dashboard_conversations(db, limit=5)
        
        # Daily message stats
        daily_stats = db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(
            func.date(Message.created_at).desc()
        ).limit(7).all()
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        schema_info = get_schema_info(db)
        last_refresh = datetime.utcnow()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "total_records": stats.get("total_records", 0),
                "pending_deliveries": stats.get("pending_deliveries", 0),
                "pending_pod": stats.get("pending_pod", 0),
                "pending_pgi": stats.get("pending_pgi", 0),
                "pending_amount": stats.get("pending_amount", 0),
                "completed_deliveries": stats.get("completed_deliveries", 0),
                "total_amount": stats.get("total_amount", 0),
                "cities": stats.get("cities", 0),
                "warehouses": stats.get("warehouses", 0),
                "top_dealers": top_dealers or [],
                "top_cities": top_cities or [],
                "top_warehouses": top_warehouses or [],
                "latest_uploads": latest_uploads or [],
                "total_uploads": upload_stats.get("total_uploads", 0),
                "total_imported_rows": upload_stats.get("total_imported_rows", 0),
                "total_conversations": total_conversations or 0,
                "total_customers": total_customers or 0,
                "total_messages": total_messages or 0,
                "total_ai_responses": total_ai_responses or 0,
                "conversations": dashboard_conversations or [],
                "stats": [{"date": str(s.date), "count": s.count} for s in daily_stats],
                "status": "running",
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "groq_status": "Online" if groq_key else "Offline",
                "ai_available": AI_QUERY_AVAILABLE,
                "ai_provider": "groq",
                "schema_version": schema_info.get("app_version", "4.0"),
                "last_upload_date": upload_stats.get("last_upload_date").strftime('%Y-%m-%d %H:%M') if upload_stats.get("last_upload_date") else "Never",
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        logger.error(f"Dashboard error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# CHAT ENDPOINTS
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        start_time = time.time()
        
        # Safe AI initialization
        if not AI_QUERY_AVAILABLE:
            ai_reply = "⚠️ AI service temporarily unavailable. Please try again later."
        else:
            try:
                ai_service = AIQueryService(db)
                result = ai_service.process_query(
                    question=request.message,
                    user_phone=request.phone_number or "web_chat"
                )
                ai_reply = result.get("response", "Thank you for contacting support.")
                
                elapsed = time.time() - start_time
                logger.info(f"📊 CHAT AI USAGE:")
                logger.info(f"   Question: {request.message[:100]}...")
                logger.info(f"   Intent: {result.get('intent', 'unknown')}")
                logger.info(f"   Response Time: {elapsed:.2f}s")
            except Exception as e:
                logger.error(f"Chat AI error: {e}")
                ai_reply = "⚠️ I'm having trouble processing your request. Please try again in a moment."
        
        # Get or create customer
        customer = None
        if request.phone_number:
            customer = db.query(Customer).filter(
                Customer.phone_number == request.phone_number
            ).first()
        
        if not customer and request.phone_number:
            safe_name = sanitize_email_name(request.customer_name)
            customer = Customer(
                name=request.customer_name,
                phone_number=request.phone_number,
                email=f"{safe_name}@temp.com"
            )
            db.add(customer)
            db.commit()
            db.refresh(customer)
        elif not request.phone_number:
            unique_phone = f"temp_{uuid.uuid4().hex[:12]}"
            safe_name = sanitize_email_name(request.customer_name)
            customer = Customer(
                name=request.customer_name,
                phone_number=unique_phone,
                email=f"{safe_name}@temp.com"
            )
            db.add(customer)
            db.commit()
            db.refresh(customer)
        
        # Create conversation
        conversation = Conversation(
            customer_id=customer.id,
            status="active"
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        
        # Save messages
        user_msg = Message(
            conversation_id=conversation.id,
            sender="user",
            content=request.message,
            message_type="text"
        )
        db.add(user_msg)
        
        ai_msg = Message(
            conversation_id=conversation.id,
            sender="assistant",
            content=ai_reply,
            message_type="text"
        )
        db.add(ai_msg)
        
        ai_log = AIResponseLog(
            conversation_id=conversation.id,
            prompt=request.message,
            ai_response=ai_reply,
            model_name="groq",
            success=True
        )
        db.add(ai_log)
        
        db.commit()
        
        return {
            "success": True,
            "reply": ai_reply
        }
    except Exception as e:
        db.rollback()
        logger.exception(f"Chat endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# INFO ENDPOINTS
# ==========================================================

@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "4.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION,
        "logistics_integration": True,
        "ai_provider": "groq",
        "ai_available": AI_QUERY_AVAILABLE
    }


@app.get("/schema-info", tags=["Info"])
async def schema_info(db: Session = Depends(get_db)):
    """Get detailed schema information"""
    return get_schema_info(db)


# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def sanitize_email_name(name: str) -> str:
    """Convert customer name to safe email prefix"""
    safe_name = name.lower()
    safe_name = re.sub(r'[^a-z0-9]', '_', safe_name)
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = safe_name.strip('_')
    return safe_name


# ==========================================================
# FALLBACK TEMPLATES
# ==========================================================

def create_fallback_templates():
    """Create fallback HTML templates if they don't exist"""
    
    dashboard_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
    if not os.path.exists(dashboard_path):
        with open(dashboard_path, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Logistics Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f2f5; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        .header { background: linear-gradient(135deg, #128C7E, #25D366); color: white; padding: 30px; border-radius: 12px; margin-bottom: 25px; }
        .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 25px; }
        .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.08); }
        .metric { font-size: 32px; font-weight: bold; color: #128C7E; }
        .section { background: white; padding: 25px; border-radius: 12px; margin-bottom: 25px; }
        button { background: #25D366; color: white; border: none; padding: 10px 24px; border-radius: 8px; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        th { background: #2c3e50; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1>📦 Logistics Control Center</h1><p>Groq-Powered AI Assistant</p></div>
        <div class="cards">
            <div class="card"><h3>Total DNs</h3><div class="metric">{{ total_records or 0 }}</div></div>
            <div class="card"><h3>Pending Deliveries</h3><div class="metric">{{ pending_deliveries or 0 }}</div></div>
            <div class="card"><h3>Pending POD</h3><div class="metric">{{ pending_pod or 0 }}</div></div>
            <div class="card"><h3>Pending PGI</h3><div class="metric">{{ pending_pgi or 0 }}</div></div>
            <div class="card"><h3>Pending Amount</h3><div class="metric">₹{{ pending_amount or 0 }}</div></div>
        </div>
        <div class="section">
            <h2>Upload Excel Report</h2>
            <form action="/upload/excel" method="post" enctype="multipart/form-data">
                <input type="file" name="file" accept=".xlsx,.xls" required>
                <button type="submit">Upload</button>
            </form>
        </div>
        <div class="footer"><p>Last Updated: {{ last_refresh }}</p></div>
    </div>
</body>
</html>""")
        logger.info(f"Created fallback dashboard template at {dashboard_path}")
    
    upload_center_path = os.path.join(TEMPLATES_DIR, "upload_center.html")
    if not os.path.exists(upload_center_path):
        with open(upload_center_path, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head><title>Upload Center</title></head>
<body>
    <h1>Upload Center</h1>
    <p>Upload your Excel delivery reports here.</p>
    <a href="/dashboard">Back to Dashboard</a>
</body>
</html>""")
        logger.info(f"Created fallback upload_center template at {upload_center_path}")


create_fallback_templates()
