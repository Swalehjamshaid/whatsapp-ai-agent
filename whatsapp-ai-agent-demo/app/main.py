# ==========================================================
# FILE: app/main.py
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
import re
import uuid
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import inspect, func, text

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

# Import schema service functions - verify these exist in schema_service.py
from app.services.schema_service import (
    check_schema_version,
    get_schema_info,
    APP_SCHEMA_VERSION
)

# WhatsApp service import
from app.services.whatsapp_service import send_text_message

# Import upload router
from app.routes.upload import router as upload_router

# ==========================================================
# LIFESPAN HANDLER (Modern FastAPI)
# ==========================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    print("========================================")
    print("AI WHATSAPP AGENT STARTING")
    print("========================================")

    # Check environment variables
    print("ENVIRONMENT VARIABLES CHECK:")
    print("DATABASE_URL EXISTS:", bool(DATABASE_URL))
    print("WHATSAPP TOKEN:", bool(os.getenv("WHATSAPP_ACCESS_TOKEN")))
    print("WHATSAPP PHONE ID:", bool(os.getenv("WHATSAPP_PHONE_NUMBER_ID")))
    print("WHATSAPP VERIFY TOKEN:", bool(os.getenv("WHATSAPP_VERIFY_TOKEN")))
    print("OPENAI_API_KEY:", bool(os.getenv("OPENAI_API_KEY")))
    print("ANTHROPIC_API_KEY:", bool(os.getenv("ANTHROPIC_API_KEY")))
    
    # Check Railway variables
    print("SCHEMA_VERSION:", os.getenv("SCHEMA_VERSION", "Not Set"))
    print("ALLOW_DB_RESET:", os.getenv("ALLOW_DB_RESET", "false"))
    print("========================================")

    # Test database connection
    print("TESTING DATABASE CONNECTION...")
    if not test_connection():
        raise Exception("Database Connection Failed - Check PostgreSQL on Railway")
    
    print("========================================")
    print("REGISTERED TABLES (SQLAlchemy Models):")
    print(list(Base.metadata.tables.keys()))
    print("========================================")

    # Check schema version
    print("CHECKING SCHEMA VERSION...")
    db = SessionLocal()
    try:
        # Call schema check (function doesn't return value)
        check_schema_version(db)
        print("✅ Schema check completed")
        
        # Get schema info for logging
        schema_info = get_schema_info(db)
        print(f"📊 Schema Info: App Version={schema_info['app_version']}, "
              f"DB Version={schema_info['db_version']}, "
              f"Needs Migration={schema_info['needs_migration']}")
        print(f"📊 Tables in database: {schema_info['table_count']}")
        
    except Exception as e:
        print(f"❌ Error during schema check: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()
    
    print("========================================")
    print("✅ Schema Check Complete")
    print("========================================")

    # Create tables if they don't exist (after schema check)
    print("CREATING TABLES (IF NOT EXISTS)...")
    Base.metadata.create_all(bind=engine)
    print("TABLE CREATION COMPLETE")
    
    # Show actual tables in database
    print("========================================")
    print("ACTUAL TABLES IN POSTGRESQL:")
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(tables)
    
    # Log table count
    print(f"📊 Total Tables: {len(tables)}")
    print("========================================")
    
    # Update expected tables with new models
    expected_tables = [
        'customers', 
        'conversations', 
        'messages', 
        'uploaded_images', 
        'ai_response_logs',
        'delivery_reports',
        'system_settings'
    ]
    print("EXPECTED TABLES:", expected_tables)
    print("ALL TABLES CREATED:", set(expected_tables).issubset(set(tables)))
    print("========================================")

    # Create upload directory for Excel files
    print("CREATING UPLOAD DIRECTORY...")
    os.makedirs("uploads", exist_ok=True)
    print("✅ Upload directory ready")
    
    # Check Excel service availability
    print("CHECKING EXCEL SERVICE...")
    try:
        from app.services.excel_import_service import ExcelImportService
        print("✅ Excel Service Available")
    except ImportError:
        print("⚠️ Excel Service Not Installed - Will be added later")
    
    print("========================================")

    print("✅ PostgreSQL Connected Successfully")
    print("✅ Database Tables Verified")
    print("✅ Upload Directory Created")
    print("========================================")

    print("✅ Application Startup Complete")
    print("========================================")
    
    yield
    
    # Shutdown
    print("========================================")
    print("AI WHATSAPP AGENT SHUTTING DOWN")
    print("========================================")


# ==========================================================
# APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent",
    version="1.0.0",
    description="AI WhatsApp Customer Service Agent",
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

# Register upload router
app.include_router(upload_router)

# ==========================================================
# TEMPLATES
# ==========================================================

# Ensure templates directory exists
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
# HELPER FUNCTIONS
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
        # Get unique batches with their metadata
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


# Create fallback templates if they don't exist
def create_fallback_templates():
    """Create fallback HTML templates if they don't exist"""
    
    # Dashboard template
    dashboard_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
    if not os.path.exists(dashboard_path):
        with open(dashboard_path, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>AI WhatsApp Agent - Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fb; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
        h1 { color: #1a1a2e; margin-bottom: 10px; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-bottom: 30px; }
        .stat-card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stat-value { font-size: 32px; font-weight: bold; color: #2563eb; margin: 10px 0; }
        .stat-label { color: #666; font-size: 14px; }
        .upload-section { background: white; border-radius: 12px; padding: 20px; margin-bottom: 30px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .upload-form { display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap; }
        .form-group { flex: 1; }
        label { display: block; margin-bottom: 5px; color: #666; font-size: 14px; }
        input[type="file"] { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; }
        button { background: #2563eb; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 16px; }
        button:hover { background: #1d4ed8; }
        .message { margin-top: 10px; padding: 10px; border-radius: 6px; display: none; }
        .message.success { background: #dcfce7; color: #166534; display: block; }
        .message.error { background: #fee2e2; color: #991b1b; display: block; }
        .uploads-table { width: 100%; border-collapse: collapse; background: white; border-radius: 12px; overflow: hidden; }
        .uploads-table th, .uploads-table td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        .uploads-table th { background: #f9fafb; font-weight: 600; color: #374151; }
        .status-badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 12px; }
        .status-online { background: #dcfce7; color: #166534; }
        .status-offline { background: #fee2e2; color: #991b1b; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 AI WhatsApp Agent Dashboard</h1>
        <p>Logistics Management System</p>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">Total Delivery Records</div>
                <div class="stat-value">{{ total_records }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pending Deliveries</div>
                <div class="stat-value">{{ pending_deliveries }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pending POD</div>
                <div class="stat-value">{{ pending_pod }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pending PGI</div>
                <div class="stat-value">{{ pending_pgi }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Pending Amount</div>
                <div class="stat-value">₹{{ pending_amount }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Cities Covered</div>
                <div class="stat-value">{{ cities }}</div>
            </div>
        </div>
        
        <div class="upload-section">
            <h3>📤 Upload Delivery Report</h3>
            <form class="upload-form" action="/upload/excel" method="post" enctype="multipart/form-data">
                <div class="form-group">
                    <label>Excel File (.xlsx, .xls)</label>
                    <input type="file" name="file" accept=".xlsx,.xls" required>
                </div>
                <div class="form-group">
                    <label>Skip Duplicates</label>
                    <select name="skip_duplicates">
                        <option value="true">Yes</option>
                        <option value="false">No</option>
                    </select>
                </div>
                <button type="submit">Upload</button>
            </form>
        </div>
        
        <div class="upload-section">
            <h3>📋 Recent Uploads</h3>
            <table class="uploads-table">
                <thead>
                    <tr><th>Filename</th><th>Upload Date</th><th>Records</th><th>Action</th></tr>
                </thead>
                <tbody>
                    {% for upload in latest_uploads %}
                    <tr>
                        <td>{{ upload.filename }}</td>
                        <td>{{ upload.upload_date.strftime('%Y-%m-%d %H:%M') if upload.upload_date else 'N/A' }}</td>
                        <td>{{ upload.record_count }}</td>
                        <td><a href="/upload/batch/{{ upload.batch_id }}/summary">View</a></td>
                    </tr>
                    {% else %}
                    <tr><td colspan="4">No uploads yet</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        
        <div class="stats-grid">
            <div class="stat-card">
                <div class="stat-label">WhatsApp Status</div>
                <div class="stat-value"><span class="status-badge status-{{ 'online' if whatsapp_status == 'Online' else 'offline' }}">{{ whatsapp_status }}</span></div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Schema Version</div>
                <div class="stat-value">{{ schema_version }}</div>
            </div>
            <div class="stat-card">
                <div class="stat-label">Last Upload</div>
                <div class="stat-value">{{ last_upload_date }}</div>
            </div>
        </div>
    </div>
    <script>
        const urlParams = new URLSearchParams(window.location.search);
        const message = urlParams.get('message');
        const error = urlParams.get('error');
        if (message) alert(message);
        if (error) alert('Error: ' + error);
    </script>
</body>
</html>""")
        print(f"Created fallback dashboard template at {dashboard_path}")
    
    # Upload center template
    upload_center_path = os.path.join(TEMPLATES_DIR, "upload_center.html")
    if not os.path.exists(upload_center_path):
        with open(upload_center_path, "w") as f:
            f.write("""<!DOCTYPE html>
<html>
<head>
    <title>Upload Center - AI WhatsApp Agent</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f7fb; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; }
        h1 { margin-bottom: 20px; }
        .card { background: white; border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .stats { display: flex; gap: 20px; margin-bottom: 20px; }
        .stat { flex: 1; text-align: center; padding: 15px; background: #f8fafc; border-radius: 8px; }
        .stat-value { font-size: 28px; font-weight: bold; color: #2563eb; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        th { background: #f9fafb; }
        .btn { display: inline-block; padding: 8px 16px; background: #2563eb; color: white; text-decoration: none; border-radius: 6px; }
        .btn-danger { background: #dc2626; }
        .btn-small { padding: 4px 12px; font-size: 14px; }
        .nav { margin-bottom: 20px; }
        .nav a { margin-right: 15px; color: #2563eb; text-decoration: none; }
    </style>
</head>
<body>
    <div class="container">
        <div class="nav">
            <a href="/dashboard">← Back to Dashboard</a>
            <a href="/upload/template">Download Template</a>
        </div>
        <h1>📦 Upload Center</h1>
        <div class="stats">
            <div class="stat"><div class="stat-value">{{ total_batches }}</div><div>Total Batches</div></div>
            <div class="stat"><div class="stat-value">{{ total_records }}</div><div>Total Records</div></div>
        </div>
        <div class="card">
            <h3>Upload History</h3>
            <table>
                <thead><tr><th>Batch ID</th><th>File</th><th>Upload Date</th><th>Records</th><th>Actions</th></tr></thead>
                <tbody>
                    {% for upload in latest_uploads %}
                    <tr>
                        <td>{{ upload.batch_id }}</td>
                        <td>{{ upload.filename }}</td>
                        <td>{{ upload.upload_date.strftime('%Y-%m-%d %H:%M:%S') if upload.upload_date else 'N/A' }}</td>
                        <td>{{ upload.record_count }}</td>
                        <td><a href="/upload/batch/{{ upload.batch_id }}/summary" class="btn btn-small">View</a></td>
                    </tr>
                    {% else %}
                    <tr><td colspan="5">No uploads found</td></tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
</body>
</html>""")
        print(f"Created fallback upload_center template at {upload_center_path}")


# Create fallback templates on startup
create_fallback_templates()


# ==========================================================
# ROOT ENDPOINT - Redirect to Dashboard
# ==========================================================

@app.get("/", tags=["Root"])
async def home():
    """Redirect to dashboard"""
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/ping", tags=["Health"])
async def ping():
    """Simple ping endpoint for Railway health checks"""
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


@app.get("/db-health", tags=["Health"])
async def db_health(db: Session = Depends(get_db)):
    """Check database connectivity with SELECT 1"""
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
# UPLOAD STATUS ENDPOINT
# ==========================================================

@app.get("/upload-status", tags=["Upload"])
async def upload_status():
    """Check if upload directory is ready for Excel files"""
    return {
        "upload_folder_exists": os.path.exists("uploads"),
        "upload_folder_path": "uploads",
        "status": "ready" if os.path.exists("uploads") else "not_ready"
    }


# Upload Center Page
@app.get("/upload-center", tags=["Upload"])
async def upload_center(request: Request, db: Session = Depends(get_db)):
    """Render upload center page"""
    try:
        # Get recent uploads
        latest_uploads = get_latest_uploads(db, limit=20)
        
        # Get upload statistics
        total_batches = db.query(DeliveryReport.upload_batch_id).distinct().count()
        total_records = db.query(DeliveryReport).count()
        
        return templates.TemplateResponse(
            "upload_center.html",
            {
                "request": request,
                "latest_uploads": latest_uploads,
                "total_batches": total_batches,
                "total_records": total_records,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        print(f"Upload center error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# STATUS ENDPOINTS
# ==========================================================

@app.get("/status", tags=["Status"])
async def status(db: Session = Depends(get_db)):
    try:
        total_customers = db.query(Customer).count()
        total_conversations = db.query(Conversation).count()
        total_delivery_records = db.query(DeliveryReport).count()
        
        # Get schema info using the function
        schema_info = get_schema_info(db)
        
        # Get last upload date
        last_upload = db.query(DeliveryReport.imported_at).order_by(
            DeliveryReport.imported_at.desc()
        ).first()
        
        return {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai": "active",
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
            "last_upload_date": last_upload[0].isoformat() if last_upload else None
        }
    except Exception as e:
        return {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai": "active",
            "whatsapp": "active",
            "railway": "connected",
            "error": str(e)
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
# DASHBOARD ENDPOINTS
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the dashboard HTML page"""
    try:
        # Get DeliveryReport KPIs
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
        
        # Get unique cities and warehouses
        cities = db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = db.query(DeliveryReport.warehouse).distinct().count()
        
        # Get total amount
        total_amount = db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
        
        # Get conversation stats
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        # Get dashboard conversations
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=5)
        
        # Get message stats by day (last 7 days)
        stats = db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(
            func.date(Message.created_at).desc()
        ).limit(7).all()
        
        # Get latest uploads
        latest_uploads = get_latest_uploads(db, limit=5)
        
        # Check service statuses
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        # Get schema info for status card
        schema_info = get_schema_info(db)
        
        # Get last upload date
        last_upload = db.query(DeliveryReport.imported_at).order_by(
            DeliveryReport.imported_at.desc()
        ).first()
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                # Delivery KPIs
                "total_records": total_records,
                "pending_deliveries": pending_deliveries,
                "pending_pod": pending_pod,
                "pending_pgi": pending_pgi,
                "pending_amount": round(pending_amount, 2),
                "completed_deliveries": completed_deliveries,
                "total_amount": round(total_amount, 2),
                "cities": cities,
                "warehouses": warehouses,
                # Conversation stats
                "total_conversations": total_conversations,
                "total_customers": total_customers,
                "total_messages": total_messages,
                "total_ai_responses": total_ai_responses,
                "conversations": dashboard_conversations,
                "stats": stats,
                # Upload stats
                "latest_uploads": latest_uploads,
                # System status
                "status": "running",
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "claude_status": "Online" if anthropic_key or openai_key else "Offline",
                "vision_status": "Online" if anthropic_key or openai_key else "Offline",
                # Schema info
                "schema_version": schema_info["app_version"],
                "last_upload_date": last_upload[0].strftime('%Y-%m-%d %H:%M') if last_upload else "Never",
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
        # Delivery KPIs
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
        
        # Conversation stats
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        # Dashboard conversations
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=10)
        
        # Latest uploads
        latest_uploads = get_latest_uploads(db, limit=5)
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        return {
            "delivery_stats": {
                "total_records": total_records,
                "pending_deliveries": pending_deliveries,
                "pending_pod": pending_pod,
                "pending_pgi": pending_pgi,
                "pending_amount": float(pending_amount)
            },
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
            "claude_status": "Online" if anthropic_key or openai_key else "Offline",
            "vision_status": "Online" if anthropic_key or openai_key else "Offline"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Enhanced logistics-status endpoint
@app.get("/logistics-status", tags=["Logistics"])
async def logistics_status(db: Session = Depends(get_db)):
    """Get logistics dashboard statistics"""
    try:
        # Basic counts
        total_dns = db.query(DeliveryReport).count()
        pending_pod = db.query(DeliveryReport).filter(
            DeliveryReport.pod_status == "Pending"
        ).count()
        pending_pgi = db.query(DeliveryReport).filter(
            DeliveryReport.pgi_status == "Pending"
        ).count()
        
        # Add delivered and pending counts - using .is_() for boolean
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
        
        # Get unique cities and warehouses
        cities = db.query(DeliveryReport.ship_to_city).distinct().count()
        warehouses = db.query(DeliveryReport.warehouse).distinct().count()
        
        # Add top cities and warehouses
        top_cities = db.query(
            DeliveryReport.ship_to_city,
            func.count(DeliveryReport.id).label('count')
        ).group_by(
            DeliveryReport.ship_to_city
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(5).all()
        
        top_warehouses = db.query(
            DeliveryReport.warehouse,
            func.count(DeliveryReport.id).label('count')
        ).group_by(
            DeliveryReport.warehouse
        ).order_by(
            func.count(DeliveryReport.id).desc()
        ).limit(5).all()
        
        # Get pending by division
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
            "top_cities": [
                {"city": city[0], "count": city[1]} for city in top_cities if city[0]
            ],
            "top_warehouses": [
                {"warehouse": wh[0], "count": wh[1]} for wh in top_warehouses if wh[0]
            ],
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


# DN search endpoint with improved regex
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


# ==========================================================
# CHAT ENDPOINTS
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        user_message = request.message.lower()
        
        # Enhanced AI response logic with delivery queries
        if "pending delivery" in user_message or "pending dn" in user_message:
            pending_count = db.query(DeliveryReport).filter(
                DeliveryReport.pending_flag.is_(True)
            ).count()
            ai_reply = f"You have {pending_count} pending deliveries."
        elif "pgi status" in user_message:
            pgi_pending = db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status == "Pending"
            ).count()
            ai_reply = f"{pgi_pending} deliveries are pending PGI."
        elif "pod status" in user_message:
            pod_pending = db.query(DeliveryReport).filter(
                DeliveryReport.pod_status == "Pending"
            ).count()
            ai_reply = f"{pod_pending} deliveries are pending POD confirmation."
        elif "order" in user_message:
            ai_reply = "Your order is currently in transit and expected tomorrow."
        elif "delivery" in user_message:
            ai_reply = "Your shipment is scheduled for delivery within 24 hours."
        elif "refund" in user_message:
            ai_reply = "Your refund request has been received and is under review."
        elif "hello" in user_message or "hi" in user_message:
            ai_reply = f"Hello {request.customer_name}, how may I assist you today?"
        else:
            ai_reply = "Thank you for contacting support. You can ask about pending deliveries, PGI status, or POD status."
        
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
        
        # Store messages
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
            model_name="rule-based",
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
# WEBHOOK - WhatsApp Integration
# ==========================================================

@app.get("/webhook", tags=["Webhook"])
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """WhatsApp webhook verification endpoint"""
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "demo_verify_token")
    
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print("✅ Webhook verified successfully")
        return PlainTextResponse(content=hub_challenge)
    
    print("❌ Webhook verification failed")
    raise HTTPException(
        status_code=403,
        detail="Verification failed - Invalid or missing verification token"
    )


@app.post("/webhook", tags=["Webhook"])
async def whatsapp_webhook(payload: dict, db: Session = Depends(get_db)):
    """Receive and process WhatsApp messages"""
    try:
        print("WhatsApp webhook received")
        
        if "entry" in payload:
            for entry in payload["entry"]:
                for change in entry.get("changes", []):
                    if "value" in change:
                        value = change["value"]
                        if "messages" in value:
                            for message in value["messages"]:
                                customer_phone = message.get("from")
                                message_text = message.get("text", {}).get("body", "")
                                
                                print(f"WhatsApp message received from {customer_phone}")
                                
                                # Find or create customer
                                customer = db.query(Customer).filter(
                                    Customer.phone_number == customer_phone
                                ).first()
                                
                                if not customer:
                                    customer = Customer(
                                        name=f"Customer_{customer_phone[-6:]}",
                                        phone_number=customer_phone,
                                        email=f"{customer_phone}@whatsapp.temp"
                                    )
                                    db.add(customer)
                                    db.commit()
                                    db.refresh(customer)
                                
                                # Reuse active conversation
                                active_conversation = db.query(Conversation).filter(
                                    Conversation.customer_id == customer.id,
                                    Conversation.status == "active"
                                ).first()
                                
                                if active_conversation:
                                    conversation = active_conversation
                                    print(f"Reusing active conversation {conversation.id}")
                                else:
                                    conversation = Conversation(
                                        customer_id=customer.id,
                                        status="active"
                                    )
                                    db.add(conversation)
                                    db.commit()
                                    db.refresh(conversation)
                                    print(f"Created new conversation {conversation.id}")
                                
                                # Store incoming message
                                user_msg = Message(
                                    conversation_id=conversation.id,
                                    sender="user",
                                    content=message_text,
                                    message_type="text"
                                )
                                db.add(user_msg)
                                
                                # Enhanced delivery query logic
                                user_message_lower = message_text.lower()
                                
                                # Check for DN number query
                                dn_match = re.search(r'(?:dn|delivery note|delivery|note)[:\s#-]*([A-Za-z0-9]+)', user_message_lower)
                                if dn_match:
                                    dn_number = dn_match.group(1).upper()
                                    delivery = db.query(DeliveryReport).filter(
                                        DeliveryReport.dn_no == dn_number
                                    ).first()
                                    if delivery:
                                        ai_reply = f"DN {dn_number}: Status={delivery.delivery_status}, PGI={delivery.pgi_status}, POD={delivery.pod_status}"
                                    else:
                                        ai_reply = f"DN {dn_number} not found in system."
                                
                                # Check for pending deliveries query
                                elif "pending delivery" in user_message_lower or "pending dn" in user_message_lower:
                                    pending_count = db.query(DeliveryReport).filter(
                                        DeliveryReport.pending_flag.is_(True)
                                    ).count()
                                    ai_reply = f"You have {pending_count} pending deliveries."
                                
                                elif "pgi status" in user_message_lower:
                                    pgi_pending = db.query(DeliveryReport).filter(
                                        DeliveryReport.pgi_status == "Pending"
                                    ).count()
                                    ai_reply = f"{pgi_pending} deliveries are pending PGI."
                                
                                elif "pod status" in user_message_lower:
                                    pod_pending = db.query(DeliveryReport).filter(
                                        DeliveryReport.pod_status == "Pending"
                                    ).count()
                                    ai_reply = f"{pod_pending} deliveries are pending POD confirmation."
                                
                                elif "order" in user_message_lower:
                                    ai_reply = "Your order is currently in transit and expected tomorrow."
                                elif "delivery" in user_message_lower:
                                    ai_reply = "Your shipment is scheduled for delivery within 24 hours."
                                elif "refund" in user_message_lower:
                                    ai_reply = "Your refund request has been received and is under review."
                                elif "hello" in user_message_lower or "hi" in user_message_lower:
                                    ai_reply = f"Hello {customer.name}, how may I assist you today? You can ask about pending deliveries, PGI status, or specific DN numbers."
                                else:
                                    ai_reply = "Thank you for contacting support. You can ask about pending deliveries, PGI status, or specific DN numbers."
                                
                                # Store AI response
                                ai_msg = Message(
                                    conversation_id=conversation.id,
                                    sender="assistant",
                                    content=ai_reply,
                                    message_type="text"
                                )
                                db.add(ai_msg)
                                
                                ai_log = AIResponseLog(
                                    conversation_id=conversation.id,
                                    prompt=message_text,
                                    ai_response=ai_reply,
                                    model_name="rule-based",
                                    success=True
                                )
                                db.add(ai_log)
                                
                                db.commit()
                                
                                # Send reply
                                print(f"Sending WhatsApp reply to {customer_phone}")
                                send_result = send_text_message(
                                    phone_number=customer_phone,
                                    message=ai_reply
                                )
                                print(f"WhatsApp Send Result: {send_result}")
        
        return {"status": "received"}
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        db.rollback()
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
# INFO ENDPOINTS
# ==========================================================

@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "1.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL",
        "schema_version": APP_SCHEMA_VERSION
    }


@app.get("/schema-info", tags=["Info"])
async def schema_info(db: Session = Depends(get_db)):
    """Get detailed schema information"""
    return get_schema_info(db)


# ==========================================================
# SEARCH ENDPOINTS
# ==========================================================

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
        
        # Remove duplicates by DN
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
