# ==========================================================
# FILE: app/main.py (ENTERPRISE v2.0)
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
from fastapi.responses import RedirectResponse, PlainTextResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import inspect, func, text, case
from loguru import logger

# ==========================================================
# PRIORITY 1: Safe AI Query Service Import
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

# Import SessionLocal from database
from app.database import (
    engine,
    DATABASE_URL,
    Base,
    get_db,
    test_connection,
    SessionLocal
)

import app.models

# Import all models explicitly including new ones
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

# Import routers
from app.routes.upload import router as upload_router
from app.routes.webhook import router as webhook_router


# ==========================================================
# PRIORITY 7: STARTUP DIAGNOSTICS
# ==========================================================

def print_startup_diagnostics():
    """Print comprehensive startup diagnostics"""
    print("=" * 80)
    print("SYSTEM DIAGNOSTICS")
    print("=" * 80)
    
    # Database
    db_connected = test_connection()
    print(f"Database: {'✅ CONNECTED' if db_connected else '❌ FAILED'}")
    
    # Groq (Priority 3 - Groq Only)
    groq_key = os.getenv("GROQ_API_KEY")
    print(f"Groq API Key: {'✅ SET' if groq_key else '❌ NOT SET'}")
    
    # WhatsApp
    whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    whatsapp_phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    whatsapp_verify = os.getenv("WHATSAPP_VERIFY_TOKEN")
    print(f"WhatsApp Token: {'✅ SET' if whatsapp_token else '❌ NOT SET'}")
    print(f"WhatsApp Phone ID: {'✅ SET' if whatsapp_phone_id else '❌ NOT SET'}")
    print(f"WhatsApp Verify Token: {'✅ SET' if whatsapp_verify else '❌ NOT SET'}")
    
    # AI Service
    print(f"AIQueryService: {'✅ AVAILABLE' if AI_QUERY_AVAILABLE else '❌ UNAVAILABLE'}")
    
    # Environment
    print(f"Environment: {os.getenv('ENVIRONMENT', 'production')}")
    print(f"Railway: {'✅ YES' if os.getenv('RAILWAY_ENVIRONMENT') else '❌ NO'}")
    
    print("=" * 80)


# ==========================================================
# LIFESPAN HANDLER (Safe Startup - Priority 10)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("=" * 80)
    print("🤖 AI WHATSAPP AGENT STARTING")
    print("=" * 80)
    
    # Priority 7: Print diagnostics
    print_startup_diagnostics()
    
    # Priority 2 & 10: Safe AI Service Test (won't crash)
    if AI_QUERY_AVAILABLE:
        try:
            test_db = SessionLocal()
            ai_service = AIQueryService(test_db)
            print("✅ AI Query Service initialized successfully")
            test_db.close()
        except Exception as e:
            logger.error(f"⚠️ AI Query Service initialization warning: {e}")
            print(f"⚠️ AI Query Service unavailable: {e}")
    else:
        print("⚠️ AI Query Service not available - skipping initialization")
    
    # Priority 4: Safe Table Creation
    print("\n📊 DATABASE SETUP")
    print("-" * 40)
    
    try:
        # Test database connection first (Priority 10 - No crash)
        if not test_connection():
            print("❌ Database Connection Failed - Starting in limited mode")
        else:
            print("✅ Database Connection Successful")
            
            # Priority 4: Safe table creation
            try:
                Base.metadata.create_all(bind=engine)
                print("✅ Tables created/verified successfully")
            except Exception as e:
                logger.error(f"Table creation error: {e}")
                print(f"❌ Table creation failed: {e}")
            
            # Show actual tables
            try:
                inspector = inspect(engine)
                tables = inspector.get_table_names()
                print(f"📊 Tables in database: {len(tables)}")
                if tables:
                    print(f"   Tables: {', '.join(tables[:10])}")
                    if len(tables) > 10:
                        print(f"   ... and {len(tables) - 10} more")
            except Exception as e:
                print(f"⚠️ Could not inspect tables: {e}")
            
            # Priority 5: Safe schema check
            db = SessionLocal()
            try:
                check_schema_version(db)
                print("✅ Schema check completed")
                
                schema_info = get_schema_info(db)
                print(f"📊 Schema: App v{schema_info['app_version']}, DB v{schema_info['db_version']}")
                if schema_info['needs_migration']:
                    print(f"⚠️ Schema migration needed")
            except Exception as e:
                logger.exception("Schema check failed")
                print(f"⚠️ Schema check failed: {e}")
            finally:
                db.close()
    
    except Exception as e:
        logger.error(f"Database setup error: {e}")
        print(f"❌ Database setup error: {e}")
    
    # Create upload directory
    print("\n📁 FILE SYSTEM")
    print("-" * 40)
    try:
        os.makedirs("uploads", exist_ok=True)
        print("✅ Upload directory ready")
    except Exception as e:
        print(f"⚠️ Could not create upload directory: {e}")
    
    # Check Excel service
    try:
        from app.services.excel_import_service import ExcelImportService
        print("✅ Excel Service Available")
    except ImportError:
        print("⚠️ Excel Service Not Installed")
    
    print("\n" + "=" * 80)
    print("✅ APPLICATION STARTUP COMPLETE")
    print("=" * 80 + "\n")
    
    yield
    
    # Shutdown
    print("\n" + "=" * 80)
    print("🛑 AI WHATSAPP AGENT SHUTTING DOWN")
    print("=" * 80)


# ==========================================================
# APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent",
    version="2.0.0",
    description="AI WhatsApp Customer Service Agent - Groq Powered",
    lifespan=lifespan
)

# ==========================================================
# CORS
# ==========================================================

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

app.include_router(upload_router)
app.include_router(webhook_router)

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
# PRIORITY 9: GROQ HEALTH ENDPOINT
# ==========================================================

@app.get("/groq-health", tags=["Health"])
async def groq_health():
    """Check Groq AI provider health"""
    groq_key = os.getenv("GROQ_API_KEY")
    
    # Try to import Groq and test
    groq_available = False
    groq_model = os.getenv("GROQ_MODEL", "qwen-qwq-32b")
    
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            # Simple test call
            response = client.chat.completions.create(
                model=groq_model,
                messages=[{"role": "user", "content": "OK"}],
                max_tokens=5
            )
            groq_available = True
        except Exception as e:
            logger.warning(f"Groq health check failed: {e}")
    
    return {
        "provider": "groq",
        "api_key_set": bool(grook_key),
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
    except:
        db_status = "disconnected"
    
    whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    uploads_folder_exists = os.path.exists("uploads")
    
    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "whatsapp": "connected" if whatsapp_token else "disconnected",
        "schema_version": APP_SCHEMA_VERSION,
        "uploads_folder": uploads_folder_exists,
        "ai_service": "available" if AI_QUERY_AVAILABLE else "unavailable",
        "ai_provider": "groq",
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# PRIORITY 3: AI STATUS (Groq Only)
# ==========================================================

@app.get("/ai-status", tags=["AI"])
async def ai_status(db: Session = Depends(get_db)):
    """Get AI service status - Groq only"""
    try:
        db.execute(text("SELECT 1")).scalar()
        db_connected = True
    except:
        db_connected = False
    
    # Groq configuration (Priority 3)
    groq_key = os.getenv("GROQ_API_KEY")
    groq_model = os.getenv("GROQ_MODEL", "qwen-qwq-32b")
    ai_provider = os.getenv("AI_PROVIDER", "groq")
    
    # Check if AI service is ready
    ai_service_ready = False
    if AI_QUERY_AVAILABLE:
        try:
            test_db = SessionLocal()
            ai_service = AIQueryService(test_db)
            ai_service_ready = True
            test_db.close()
        except Exception as e:
            ai_service_error = str(e)
    
    return {
        "ai_provider": ai_provider,
        "groq_api_key_set": bool(groq_key),
        "groq_model": groq_model,
        "database_connected": db_connected,
        "ai_service_available": AI_QUERY_AVAILABLE,
        "ai_service_ready": ai_service_ready,
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
        print(f"Upload center error: {str(e)}")
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
                "db_version": schema_info["db_version"],
                "needs_migration": schema_info["needs_migration"]
            },
            "last_upload_date": last_upload[0].isoformat() if last_upload else None,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
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
        from app.database import test_connection
        connected = test_connection()
        
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        
        return {
            "connected": connected,
            "database_url_exists": bool(DATABASE_URL),
            "tables": tables,
            "table_count": len(tables)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# DASHBOARD ENDPOINT
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the dashboard HTML page"""
    try:
        total_records = db.query(DeliveryReport).count()
        pending_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        pending_amount = db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        completed_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Received"
        ).count()
        
        cities = db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = db.query(DeliveryReport.warehouse).distinct().count()
        total_amount = db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        
        top_dealers = get_top_dealers(db, limit=5)
        top_cities = get_top_cities(db, limit=5)
        top_warehouses = get_warehouse_stats(db, limit=5)
        upload_stats = get_upload_statistics(db)
        latest_uploads = get_latest_uploads(db, limit=5)
        
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=5)
        
        stats = db.query(
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
                "total_records": total_records or 0,
                "pending_deliveries": pending_deliveries or 0,
                "pending_pod": pending_pod or 0,
                "pending_pgi": pending_pgi or 0,
                "pending_amount": round(pending_amount, 2) if pending_amount else 0,
                "completed_deliveries": completed_deliveries or 0,
                "total_amount": round(total_amount, 2) if total_amount else 0,
                "cities": cities or 0,
                "warehouses": warehouses or 0,
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
                "stats": stats or [],
                "status": "running",
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "groq_status": "Online" if groq_key else "Offline",
                "ai_available": AI_QUERY_AVAILABLE,
                "ai_provider": "groq",
                "schema_version": schema_info.get("app_version", "2.0"),
                "last_upload_date": upload_stats.get("last_upload_date").strftime('%Y-%m-%d %H:%M') if upload_stats.get("last_upload_date") else "Never",
                "last_refresh": last_refresh.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        print(f"Dashboard error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/dashboard", tags=["API"])
async def dashboard_api(db: Session = Depends(get_db)):
    """Return dashboard data as JSON"""
    try:
        total_records = db.query(DeliveryReport).count()
        pending_deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        pending_amount = db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        
        top_dealers = get_top_dealers(db, limit=5)
        top_cities = get_top_cities(db, limit=5)
        top_warehouses = get_warehouse_stats(db, limit=5)
        
        upload_stats = get_upload_statistics(db)
        
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=10)
        latest_uploads = get_latest_uploads(db, limit=5)
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        groq_key = os.getenv("GROQ_API_KEY")
        
        return {
            "delivery_stats": {
                "total_records": total_records,
                "pending_deliveries": pending_deliveries,
                "pending_pod": pending_pod,
                "pending_pgi": pending_pgi,
                "pending_amount": float(pending_amount)
            },
            "top_dealers": top_dealers,
            "top_cities": top_cities,
            "top_warehouses": top_warehouses,
            "upload_stats": upload_stats,
            "conversation_stats": {
                "total_conversations": total_conversations,
                "total_customers": total_customers,
                "total_messages": total_messages,
                "total_ai_responses": total_ai_responses
            },
            "conversations": dashboard_conversations,
            "latest_uploads": latest_uploads,
            "status": "running",
            "whatsapp_status": "Online" if whatsapp_token else "Offline",
            "groq_status": "Online" if groq_key else "Offline",
            "ai_available": AI_QUERY_AVAILABLE,
            "ai_provider": "groq"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# LOGISTICS ENDPOINTS
# ==========================================================

@app.get("/logistics-status", tags=["Logistics"])
async def logistics_status(db: Session = Depends(get_db)):
    """Get logistics dashboard statistics"""
    try:
        total_dns = db.query(DeliveryReport).count()
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        total_delivered = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Received"
        ).count()
        total_pending = db.query(DeliveryReport).filter(
            DeliveryReport.pending_flag.is_(True)
        ).count()
        
        total_amount = db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        pending_amount = db.query(func.sum(DeliveryReport.dn_amount)).filter(
            DeliveryReport.pending_flag.is_(True)
        ).scalar() or 0
        
        cities = db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = db.query(DeliveryReport.warehouse).distinct().count()
        
        top_cities = get_top_cities(db, limit=5)
        top_warehouses = get_warehouse_stats(db, limit=5)
        
        pending_by_division = db.query(
            DeliveryReport.division,
            func.count(DeliveryReport.id).label('count'),
            func.sum(DeliveryReport.dn_amount).label('amount')
        ).filter(
            DeliveryReport.pending_flag.is_(True)
        ).group_by(
            DeliveryReport.division
        ).all()
        
        return {
            "summary": {
                "total_delivery_notes": total_dns,
                "pending_pod": pending_pod,
                "pending_pgi": pending_pgi,
                "total_delivered": total_delivered,
                "total_pending": total_pending,
                "total_delivery_amount": float(total_amount),
                "pending_amount": float(pending_amount),
                "unique_cities": cities,
                "unique_warehouses": warehouses
            },
            "top_cities": top_cities,
            "top_warehouses": top_warehouses,
            "pending_by_division": [
                {
                    "division": div.division,
                    "count": div.count,
                    "amount": float(div.amount) if div.amount else 0.0
                }
                for div in pending_by_division if div.division
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/dn/{dn_no}", tags=["Logistics"])
async def get_delivery_note(dn_no: str, db: Session = Depends(get_db)):
    """Search for delivery notes by DN number"""
    try:
        deliveries = db.query(DeliveryReport).filter(
            DeliveryReport.dn_no == dn_no
        ).all()
        
        if not deliveries:
            raise HTTPException(status_code=404, detail=f"DN {dn_no} not found")
        
        return {
            "dn_no": dn_no,
            "total_lines": len(deliveries),
            "deliveries": [
                {
                    "line_id": d.id,
                    "dealer_code": d.dealer_code,
                    "customer_name": d.customer_name,
                    "material_no": d.material_no,
                    "quantity": d.dn_qty,
                    "amount": float(d.dn_amount) if d.dn_amount else 0,
                    "city": d.ship_to_city,
                    "warehouse": d.warehouse,
                    "pod_status": d.pod_status,
                    "pgi_status": d.pgi_status,
                    "pending_flag": d.pending_flag,
                    "pod_date": d.pod_date.isoformat() if d.pod_date else None,
                    "good_issue_date": d.good_issue_date.isoformat() if d.good_issue_date else None
                }
                for d in deliveries
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search", tags=["Search"])
async def search_deliveries(
    q: str,
    search_type: str = "all",
    limit: int = 20,
    db: Session = Depends(get_db)
):
    """Search deliveries by DN, dealer, city, warehouse, or division"""
    try:
        results = []
        
        if search_type in ["all", "dn"]:
            dn_results = db.query(DeliveryReport).filter(
                DeliveryReport.dn_no.ilike(f"%{q}%")
            ).limit(limit).all()
            for r in dn_results:
                results.append({
                    "type": "dn",
                    "dn_no": r.dn_no,
                    "customer_name": r.customer_name,
                    "city": r.ship_to_city,
                    "dealer_code": r.dealer_code,
                    "warehouse": r.warehouse,
                    "division": r.division,
                    "status": r.delivery_status,
                    "pending": r.pending_flag
                })
        
        if search_type in ["all", "dealer"]:
            dealer_results = db.query(DeliveryReport).filter(
                DeliveryReport.dealer_code.ilike(f"%{q}%")
            ).limit(limit).all()
            for r in dealer_results:
                results.append({
                    "type": "dealer",
                    "dealer_code": r.dealer_code,
                    "customer_name": r.customer_name,
                    "dn_no": r.dn_no,
                    "city": r.ship_to_city,
                    "status": r.delivery_status
                })
        
        if search_type in ["all", "city"]:
            city_results = db.query(DeliveryReport).filter(
                DeliveryReport.ship_to_city.ilike(f"%{q}%")
            ).limit(limit).all()
            for r in city_results:
                results.append({
                    "type": "city",
                    "city": r.ship_to_city,
                    "dn_no": r.dn_no,
                    "customer_name": r.customer_name,
                    "status": r.delivery_status
                })
        
        if search_type in ["all", "warehouse"]:
            warehouse_results = db.query(DeliveryReport).filter(
                DeliveryReport.warehouse.ilike(f"%{q}%")
            ).limit(limit).all()
            for r in warehouse_results:
                results.append({
                    "type": "warehouse",
                    "warehouse": r.warehouse,
                    "dn_no": r.dn_no,
                    "customer_name": r.customer_name,
                    "city": r.ship_to_city,
                    "status": r.delivery_status
                })
        
        if search_type in ["all", "division"]:
            division_results = db.query(DeliveryReport).filter(
                DeliveryReport.division.ilike(f"%{q}%")
            ).limit(limit).all()
            for r in division_results:
                results.append({
                    "type": "division",
                    "division": r.division,
                    "dn_no": r.dn_no,
                    "customer_name": r.customer_name,
                    "status": r.delivery_status
                })
        
        seen = set()
        unique_results = []
        for r in results:
            dn_key = r.get('dn_no')
            if dn_key and dn_key not in seen:
                seen.add(dn_key)
                unique_results.append(r)
        
        return {
            "query": q,
            "search_type": search_type,
            "total_results": len(unique_results),
            "limit": limit,
            "results": unique_results[:limit]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# CHAT ENDPOINTS (Safe AI Initialization - Priority 6)
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        start_time = time.time()
        
        # Priority 6: Safe AI initialization
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
                print(f"📊 CHAT AI USAGE:")
                print(f"   Question: {request.message[:100]}...")
                print(f"   Intent: {result.get('question_type', 'unknown')}")
                print(f"   AI Used: {result.get('ai_used', False)}")
                print(f"   Response Time: {elapsed:.2f}s")
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
# CONVERSATION ENDPOINTS
# ==========================================================

@app.get("/conversations", tags=["Conversations"])
async def get_conversations(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """Get all conversations with pagination"""
    try:
        conversations = db.query(Conversation).options(
            joinedload(Conversation.customer)
        ).order_by(
            Conversation.created_at.desc()
        ).offset(skip).limit(limit).all()
        
        conversation_data = []
        for conv in conversations:
            messages = db.query(Message).filter(
                Message.conversation_id == conv.id
            ).order_by(Message.created_at).all()
            
            conversation_data.append({
                "id": conv.id,
                "customer_id": conv.customer_id,
                "customer_name": conv.customer.name if conv.customer else "Unknown",
                "status": conv.status,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
                "messages": [
                    {
                        "sender": msg.sender,
                        "content": msg.content,
                        "message_type": msg.message_type,
                        "created_at": msg.created_at.isoformat() if msg.created_at else None
                    }
                    for msg in messages
                ]
            })
        
        return {
            "count": len(conversation_data),
            "skip": skip,
            "limit": limit,
            "data": conversation_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/conversations/{conversation_id}", tags=["Conversations"])
async def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    """Get a specific conversation by ID"""
    try:
        conversation = db.query(Conversation).options(
            joinedload(Conversation.customer)
        ).filter(
            Conversation.id == conversation_id
        ).first()
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        messages = db.query(Message).filter(
            Message.conversation_id == conversation.id
        ).order_by(Message.created_at).all()
        
        return {
            "id": conversation.id,
            "customer_id": conversation.customer_id,
            "customer_name": conversation.customer.name if conversation.customer else "Unknown",
            "status": conversation.status,
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            "messages": [
                {
                    "sender": msg.sender,
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None
                }
                for msg in messages
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# CUSTOMER ENDPOINTS
# ==========================================================

@app.get("/customers", tags=["Customers"])
async def get_customers(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """Get all customers with pagination"""
    try:
        customers = db.query(Customer).order_by(
            Customer.created_at.desc()
        ).offset(skip).limit(limit).all()
        
        customer_data = [
            {
                "id": c.id,
                "name": c.name,
                "phone_number": c.phone_number,
                "email": c.email,
                "created_at": c.created_at.isoformat() if c.created_at else None
            }
            for c in customers
        ]
        
        return {
            "count": len(customer_data),
            "skip": skip,
            "limit": limit,
            "customers": customer_data
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/customers/{customer_id}", tags=["Customers"])
async def get_customer(customer_id: int, db: Session = Depends(get_db)):
    """Get a specific customer by ID"""
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        
        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")
        
        conversations = db.query(Conversation).filter(
            Conversation.customer_id == customer.id
        ).all()
        
        return {
            "id": customer.id,
            "name": customer.name,
            "phone_number": customer.phone_number,
            "email": customer.email,
            "created_at": customer.created_at.isoformat() if customer.created_at else None,
            "conversation_count": len(conversations),
            "conversations": [
                {
                    "id": conv.id,
                    "status": conv.status,
                    "created_at": conv.created_at.isoformat() if conv.created_at else None
                }
                for conv in conversations
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# ANALYTICS ENDPOINTS
# ==========================================================

@app.get("/analytics", tags=["Analytics"])
async def get_analytics(db: Session = Depends(get_db)):
    """Get analytics data"""
    try:
        total_messages = db.query(Message).count()
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        total_delivery_records = db.query(DeliveryReport).count()
        
        daily_stats = db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(
            func.date(Message.created_at).desc()
        ).limit(7).all()
        
        return {
            "total_messages": total_messages,
            "total_ai_responses": total_ai_responses,
            "total_conversations": total_conversations,
            "total_customers": total_customers,
            "total_delivery_records": total_delivery_records,
            "daily_stats": [
                {"date": str(stat.date), "count": stat.count}
                for stat in daily_stats
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# INFO ENDPOINTS (Priority 8 - Clean, Groq-only)
# ==========================================================

@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "2.0.0",
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
# HELPER FUNCTIONS (Preserved)
# ==========================================================

def sanitize_email_name(name: str) -> str:
    """Convert customer name to safe email prefix"""
    safe_name = name.lower()
    safe_name = re.sub(r'[^a-z0-9]', '_', safe_name)
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = safe_name.strip('_')
    return safe_name


def get_dashboard_conversations_optimized(db: Session, limit: int = 10):
    """Get formatted conversations for dashboard template - OPTIMIZED version"""
    conversations = db.query(
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
        user_messages = db.query(
            Message.conversation_id,
            Message.content,
            Message.created_at
        ).filter(
            Message.conversation_id.in_(conv_ids),
            Message.sender == "user"
        ).distinct(Message.conversation_id).order_by(
            Message.conversation_id, Message.created_at.desc()
        ).all()
        
        ai_responses = db.query(
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
    
    dashboard_conversations = []
    for conv in conversations:
        user_msg = user_msg_dict.get(conv.id)
        ai_response = ai_response_dict.get(conv.id)
        
        dashboard_conversations.append({
            "id": conv.id,
            "customer": conv.customer_name,
            "customer_phone": conv.customer_phone,
            "message": user_msg.content if user_msg else "No messages",
            "reply": ai_response.content if ai_response else "No response",
            "timestamp": conv.created_at.isoformat() if conv.created_at else "",
            "status": conv.status
        })
    
    return dashboard_conversations


def get_latest_uploads(db: Session, limit: int = 5):
    """Get latest upload batches for dashboard"""
    try:
        batches = db.query(
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
    except Exception as e:
        print(f"Error getting latest uploads: {e}")
        return []


def get_top_dealers(db: Session, limit: int = 5):
    """Get top dealers by delivery count - FIXED case() function"""
    try:
        dealers = db.query(
            DeliveryReport.dealer_code,
            DeliveryReport.customer_name,
            func.count(DeliveryReport.id).label('delivery_count'),
            func.sum(DeliveryReport.dn_amount).label('total_amount'),
            func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending_count')
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
                "delivery_count": d.delivery_count,
                "total_amount": float(d.total_amount or 0),
                "pending_count": d.pending_count or 0
            }
            for d in dealers if d.dealer_code
        ]
    except Exception as e:
        print(f"Error getting top dealers: {e}")
        return []


def get_top_cities(db: Session, limit: int = 5):
    """Get top cities by delivery count - FIXED case() function"""
    try:
        cities = db.query(
            DeliveryReport.ship_to_city.label('city'),
            func.count(DeliveryReport.id).label('count'),
            func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending_count')
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(limit).all()
        
        return [
            {
                "city": c.city or "N/A",
                "count": c.count,
                "pending_count": c.pending_count or 0
            }
            for c in cities if c.city
        ]
    except Exception as e:
        print(f"Error getting top cities: {e}")
        return []


def get_warehouse_stats(db: Session, limit: int = 5):
    """Get warehouse statistics - FIXED case() function"""
    try:
        warehouses = db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label('total_count'),
            func.sum(case((DeliveryReport.pending_flag.is_(True), 1), else_=0)).label('pending_count'),
            func.sum(case((DeliveryReport.pending_flag.is_(True), DeliveryReport.dn_amount), else_=0)).label('pending_amount')
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(limit).all()
        
        return [
            {
                "warehouse": w.warehouse or "N/A",
                "total_count": w.total_count,
                "pending_count": w.pending_count or 0,
                "pending_amount": float(w.pending_amount or 0)
            }
            for w in warehouses if w.warehouse
        ]
    except Exception as e:
        print(f"Error getting warehouse stats: {e}")
        return []


def get_upload_statistics(db: Session):
    """Get upload statistics for dashboard"""
    try:
        total_uploads = db.query(DeliveryReport.upload_batch_id).distinct().count()
        total_imported_rows = db.query(DeliveryReport).count()
        last_upload = db.query(DeliveryReport.imported_at).order_by(
            DeliveryReport.imported_at.desc()
        ).first()
        
        return {
            "total_uploads": total_uploads,
            "total_imported_rows": total_imported_rows,
            "last_upload_date": last_upload[0] if last_upload else None
        }
    except Exception as e:
        print(f"Error getting upload statistics: {e}")
        return {
            "total_uploads": 0,
            "total_imported_rows": 0,
            "last_upload_date": None
        }


# Create fallback templates if they don't exist
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
        print(f"Created fallback dashboard template at {dashboard_path}")
    
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
        print(f"Created fallback upload_center template at {upload_center_path}")


create_fallback_templates()
