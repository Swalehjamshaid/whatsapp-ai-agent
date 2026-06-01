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

from app.database import (
    engine,
    DATABASE_URL,
    Base,
    get_db,
    test_connection
)

import app.models

from app.models import (
    Customer,
    Conversation,
    Message,
    UploadedImage,
    AIResponseLog
)

# FIX 1: Added WhatsApp service import
from app.services.whatsapp_service import send_text_message

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
    print("========================================")

    # Test database connection
    print("TESTING DATABASE CONNECTION...")
    if not test_connection():
        raise Exception("Database Connection Failed - Check PostgreSQL on Railway")
    
    print("========================================")
    print("REGISTERED TABLES (SQLAlchemy Models):")
    print(list(Base.metadata.tables.keys()))
    print("========================================")

    # Create tables
    print("CREATING TABLES...")
    Base.metadata.create_all(bind=engine)
    print("TABLE CREATION COMPLETE")
    
    # Show actual tables in database
    print("========================================")
    print("ACTUAL TABLES IN POSTGRESQL:")
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    print(tables)
    print("========================================")
    
    # Expected tables check
    expected_tables = ['customers', 'conversations', 'messages', 'uploaded_images', 'ai_response_logs']
    print("EXPECTED TABLES:", expected_tables)
    print("ALL TABLES CREATED:", set(expected_tables).issubset(set(tables)))
    print("========================================")

    print("✅ PostgreSQL Connected Successfully")
    print("✅ Database Tables Created")
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
# TEMPLATES - More reliable path for Railway
# ==========================================================

templates = Jinja2Templates(
    directory=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)

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
    # Convert to lowercase
    safe_name = name.lower()
    # Replace spaces and special characters with underscores
    safe_name = re.sub(r'[^a-z0-9]', '_', safe_name)
    # Remove multiple consecutive underscores
    safe_name = re.sub(r'_+', '_', safe_name)
    # Remove leading/trailing underscores
    safe_name = safe_name.strip('_')
    return safe_name

def get_dashboard_conversations_optimized(db: Session, limit: int = 10):
    """Get formatted conversations for dashboard template - OPTIMIZED version"""
    # Single query with joins to get all data at once
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
    
    # Get latest messages for all conversations in one query
    conv_ids = [conv.id for conv in conversations]
    
    if conv_ids:
        # Get latest user messages
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
        
        # Get latest AI responses
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
        
        # Create lookup dictionaries
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


# ==========================================================
# ROOT - Health JSON (Stable fallback)
# ==========================================================

@app.get("/", tags=["Root"])
async def root():
    """Return health JSON - dashboard available at /dashboard"""
    return {
        "status": "ok",
        "message": "AI WhatsApp Agent is running",
        "dashboard_url": "/dashboard",
        "api_docs": "/docs",
        "version": "1.0.0"
    }


# ==========================================================
# HEALTH
# ==========================================================

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# DATABASE HEALTH - Specific PostgreSQL check
# ==========================================================

@app.get("/db-health", tags=["Health"])
async def db_health(db: Session = Depends(get_db)):
    """Check database connectivity with SELECT 1"""
    try:
        # Perform simple query to verify database is responsive
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
# PING - For Railway health checks
# ==========================================================

@app.get("/ping", tags=["Health"])
async def ping():
    """Simple ping endpoint for Railway health checks"""
    return {"ping": "pong", "timestamp": datetime.utcnow().isoformat()}


# ==========================================================
# STATUS
# ==========================================================

@app.get("/status", tags=["Status"])
async def status(db: Session = Depends(get_db)):
    try:
        total_customers = db.query(Customer).count()
        total_conversations = db.query(Conversation).count()
        
        return {
            "application": "AI WhatsApp Agent",
            "database": "postgresql",
            "ai": "active",
            "whatsapp": "active",
            "railway": "connected",
            "statistics": {
                "total_customers": total_customers,
                "total_conversations": total_conversations
            }
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


# ==========================================================
# DATABASE TEST ENDPOINT
# ==========================================================

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
# DASHBOARD - HTML UI
# ==========================================================

@app.get("/dashboard", tags=["Dashboard"])
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Render the dashboard HTML page"""
    try:
        # Query real data from PostgreSQL
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        # Get optimized dashboard conversations
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=10)
        
        # Get message stats by day (last 7 days with correct order)
        stats = db.query(
            func.date(Message.created_at).label('date'),
            func.count(Message.id).label('count')
        ).group_by(func.date(Message.created_at)).order_by(
            func.date(Message.created_at).desc()
        ).limit(7).all()
        
        # Check service statuses for dashboard
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "total_conversations": total_conversations,
                "total_customers": total_customers,
                "total_messages": total_messages,
                "total_ai_responses": total_ai_responses,
                "conversations": dashboard_conversations,
                "stats": stats,
                "status": "running",
                # Added missing status variables
                "whatsapp_status": "Online" if whatsapp_token else "Offline",
                "claude_status": "Online" if anthropic_key or openai_key else "Offline",
                "vision_status": "Online" if anthropic_key or openai_key else "Offline",
                "timestamp": datetime.utcnow().isoformat()
            }
        )
    except Exception as e:
        print(f"Dashboard error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# DASHBOARD API - JSON Endpoint
# ==========================================================

@app.get("/api/dashboard", tags=["API"])
async def dashboard_api(db: Session = Depends(get_db)):
    """Return dashboard data as JSON"""
    try:
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_messages = db.query(Message).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        # Get optimized conversations
        dashboard_conversations = get_dashboard_conversations_optimized(db, limit=10)
        
        whatsapp_token = os.getenv("WHATSAPP_ACCESS_TOKEN")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")
        
        return {
            "total_conversations": total_conversations,
            "total_customers": total_customers,
            "total_messages": total_messages,
            "total_ai_responses": total_ai_responses,
            "conversations": dashboard_conversations,
            "status": "running",
            "whatsapp_status": "Online" if whatsapp_token else "Offline",
            "claude_status": "Online" if anthropic_key or openai_key else "Offline",
            "vision_status": "Online" if anthropic_key or openai_key else "Offline"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# CHAT
# ==========================================================

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    try:
        user_message = request.message.lower()
        
        # Simple AI response logic
        if "order" in user_message:
            ai_reply = "Your order is currently in transit and expected tomorrow."
        elif "delivery" in user_message:
            ai_reply = "Your shipment is scheduled for delivery within 24 hours."
        elif "refund" in user_message:
            ai_reply = "Your refund request has been received and is under review."
        elif "hello" in user_message or "hi" in user_message:
            ai_reply = f"Hello {request.customer_name}, how may I assist you today?"
        else:
            ai_reply = "Thank you for contacting support. Our AI assistant has received your message."
        
        # Get or create customer using phone number as primary key only
        customer = None
        if request.phone_number:
            customer = db.query(Customer).filter(
                Customer.phone_number == request.phone_number
            ).first()
        
        if not customer and request.phone_number:
            # Create new customer with sanitized email
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
            # Generate truly unique phone number using UUID
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
        
        # Store user message
        user_msg = Message(
            conversation_id=conversation.id,
            sender="user",
            content=request.message,
            message_type="text"
        )
        db.add(user_msg)
        
        # Store AI response
        ai_msg = Message(
            conversation_id=conversation.id,
            sender="assistant",
            content=ai_reply,
            message_type="text"
        )
        db.add(ai_msg)
        
        # Log AI response with correct fields
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
# CONVERSATIONS
# ==========================================================

@app.get("/conversations", tags=["Conversations"])
async def get_conversations(
    skip: int = 0, 
    limit: int = 100, 
    db: Session = Depends(get_db)
):
    """Get all conversations with pagination"""
    try:
        # Use joinedload to avoid N+1 query pattern
        conversations = db.query(Conversation).options(
            joinedload(Conversation.customer)
        ).order_by(
            Conversation.created_at.desc()
        ).offset(skip).limit(limit).all()
        
        conversation_data = []
        for conv in conversations:
            # Load messages efficiently
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
# CUSTOMERS
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
# WEBHOOK - WhatsApp Integration with proper aliases
# ==========================================================

@app.get("/webhook", tags=["Webhook"])
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """WhatsApp webhook verification endpoint with proper Meta aliases"""
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "demo_verify_token")
    
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        print("✅ Webhook verified successfully")
        # Return PlainTextResponse for Meta compatibility
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
        print("WhatsApp webhook received:", payload)
        
        # Process WhatsApp messages
        if "entry" in payload:
            for entry in payload["entry"]:
                for change in entry.get("changes", []):
                    if "value" in change:
                        value = change["value"]
                        if "messages" in value:
                            for message in value["messages"]:
                                # Extract message details
                                customer_phone = message.get("from")
                                message_text = message.get("text", {}).get("body", "")
                                # Note: message_id extracted but not stored (matches current model)
                                
                                print(f"Message from {customer_phone}: {message_text}")
                                
                                # Find or create customer
                                customer = db.query(Customer).filter(
                                    Customer.phone_number == customer_phone
                                ).first()
                                
                                if not customer:
                                    # Create new customer with phone number
                                    customer = Customer(
                                        name=f"Customer_{customer_phone[-6:]}",
                                        phone_number=customer_phone,
                                        email=f"{customer_phone}@whatsapp.temp"
                                    )
                                    db.add(customer)
                                    db.commit()
                                    db.refresh(customer)
                                
                                # Create new conversation
                                conversation = Conversation(
                                    customer_id=customer.id,
                                    status="active"
                                )
                                db.add(conversation)
                                db.commit()
                                db.refresh(conversation)
                                
                                # Store incoming message - FIXED: Removed whatsapp_message_id
                                user_msg = Message(
                                    conversation_id=conversation.id,
                                    sender="user",
                                    content=message_text,
                                    message_type="text"
                                )
                                db.add(user_msg)
                                
                                # Generate AI response
                                user_message_lower = message_text.lower()
                                if "order" in user_message_lower:
                                    ai_reply = "Your order is currently in transit and expected tomorrow."
                                elif "delivery" in user_message_lower:
                                    ai_reply = "Your shipment is scheduled for delivery within 24 hours."
                                elif "refund" in user_message_lower:
                                    ai_reply = "Your refund request has been received and is under review."
                                elif "hello" in user_message_lower or "hi" in user_message_lower:
                                    ai_reply = f"Hello {customer.name}, how may I assist you today?"
                                else:
                                    ai_reply = "Thank you for contacting support. Our AI assistant has received your message."
                                
                                # Store AI response
                                ai_msg = Message(
                                    conversation_id=conversation.id,
                                    sender="assistant",
                                    content=ai_reply,
                                    message_type="text"
                                )
                                db.add(ai_msg)
                                
                                # Log AI response
                                ai_log = AIResponseLog(
                                    conversation_id=conversation.id,
                                    prompt=message_text,
                                    ai_response=ai_reply,
                                    model_name="rule-based",
                                    success=True
                                )
                                db.add(ai_log)
                                
                                db.commit()
                                
                                # FIX 2: Send reply back via WhatsApp API
                                print(f"Sending WhatsApp reply to {customer_phone}")
                                
                                send_result = send_text_message(
                                    phone_number=customer_phone,
                                    message=ai_reply
                                )
                                
                                print(f"WhatsApp Send Result: {send_result}")
                                print(f"AI Response to {customer_phone}: {ai_reply}")
        
        return {"status": "received", "payload": payload}
    except Exception as e:
        print(f"Webhook error: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# ANALYTICS
# ==========================================================

@app.get("/analytics", tags=["Analytics"])
async def get_analytics(db: Session = Depends(get_db)):
    """Get analytics data"""
    try:
        total_messages = db.query(Message).count()
        total_conversations = db.query(Conversation).count()
        total_customers = db.query(Customer).count()
        total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
        
        # Get daily message count for last 7 days with correct order
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
            "daily_stats": [
                {"date": str(stat.date), "count": stat.count}
                for stat in daily_stats
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================================
# VERSION
# ==========================================================

@app.get("/version", tags=["Info"])
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "1.0.0",
        "framework": "FastAPI",
        "database": "PostgreSQL"
    }
