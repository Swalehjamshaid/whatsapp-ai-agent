# ==========================================================
# FILE: app/main.py
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import inspect

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

# ==========================================================
# APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent",
    version="1.0.0",
    description="AI WhatsApp Customer Service Agent"
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
# REQUEST / RESPONSE MODELS
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str
    message: str
    phone_number: str = None  # Optional phone number


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# STARTUP
# ==========================================================

@app.on_event("startup")
async def startup_event():
    try:
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

    except Exception as e:
        print("========================================")
        print("❌ DATABASE ERROR")
        print(str(e))
        print("========================================")
        raise e


# ==========================================================
# ROOT
# ==========================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "application": "AI WhatsApp Agent",
        "version": "1.0.0",
        "database": "postgresql",
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# HEALTH
# ==========================================================

@app.get("/health")
async def health():
    return {
        "status": "healthy"
    }


# ==========================================================
# STATUS
# ==========================================================

@app.get("/status")
async def status():
    return {
        "application": "AI WhatsApp Agent",
        "database": "postgresql",
        "ai": "active",
        "whatsapp": "active",
        "railway": "connected"
    }


# ==========================================================
# DATABASE TEST ENDPOINT
# ==========================================================

@app.get("/db-test")
async def db_test():
    """Debug endpoint to test database connectivity"""
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


# ==========================================================
# DASHBOARD
# ==========================================================

@app.get("/dashboard")
async def dashboard(db: Session = Depends(get_db)):
    # Query real data from PostgreSQL
    total_conversations = db.query(Conversation).count()
    active_customers = db.query(Customer).count()
    
    return {
        "total_conversations": total_conversations,
        "active_customers": active_customers,
        "status": "running"
    }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, db: Session = Depends(get_db)):
    user_message = request.message.lower()
    
    # Simple AI response logic
    if "order" in user_message:
        ai_reply = "Your order is currently in transit and expected tomorrow."
    elif "delivery" in user_message:
        ai_reply = "Your shipment is scheduled for delivery within 24 hours."
    elif "refund" in user_message:
        ai_reply = "Your refund request has been received and is under review."
    elif "hello" in user_message:
        ai_reply = f"Hello {request.customer_name}, how may I assist you today?"
    else:
        ai_reply = "Thank you for contacting support. Our AI assistant has received your message."
    
    # Get or create customer - FIXED: using correct field name 'phone_number'
    customer = db.query(Customer).filter(Customer.name == request.customer_name).first()
    if not customer:
        # Generate unique phone number using timestamp to avoid duplicates
        unique_phone = f"temp_{int(datetime.utcnow().timestamp())}"
        customer = Customer(
            name=request.customer_name,
            phone_number=request.phone_number if request.phone_number else unique_phone,
            email=f"{request.customer_name}@temp.com"
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
    
    # Store user message - FIXED: using 'sender' instead of 'role'
    user_msg = Message(
        conversation_id=conversation.id,
        sender="user",
        content=request.message,
        message_type="text"
    )
    db.add(user_msg)
    
    # Store AI response - FIXED: using 'sender' instead of 'role'
    ai_msg = Message(
        conversation_id=conversation.id,
        sender="assistant",
        content=ai_reply,
        message_type="text"
    )
    db.add(ai_msg)
    db.commit()
    
    return {
        "success": True,
        "reply": ai_reply
    }


# ==========================================================
# CONVERSATIONS
# ==========================================================

@app.get("/conversations")
async def get_conversations(db: Session = Depends(get_db)):
    conversations = db.query(Conversation).all()
    
    conversation_data = []
    for conv in conversations:
        messages = db.query(Message).filter(Message.conversation_id == conv.id).all()
        conversation_data.append({
            "id": conv.id,
            "customer_id": conv.customer_id,
            "status": conv.status,
            "created_at": conv.created_at.isoformat() if conv.created_at else None,
            "messages": [
                {
                    "sender": msg.sender,  # FIXED: using 'sender' instead of 'role'
                    "content": msg.content,
                    "message_type": msg.message_type,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None  # FIXED: using 'created_at' instead of 'timestamp'
                }
                for msg in messages
            ]
        })
    
    return {
        "count": len(conversation_data),
        "data": conversation_data
    }


# ==========================================================
# CUSTOMERS
# ==========================================================

@app.get("/customers")
async def get_customers(db: Session = Depends(get_db)):
    customers = db.query(Customer).all()
    
    customer_data = [
        {
            "id": c.id,
            "name": c.name,
            "phone_number": c.phone_number,  # FIXED: using 'phone_number' instead of 'phone'
            "email": c.email,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in customers
    ]
    
    return {
        "count": len(customer_data),
        "customers": customer_data
    }


# ==========================================================
# WEBHOOK
# ==========================================================

@app.get("/webhook")
async def verify_webhook():
    return {
        "message": "Webhook Verification Successful"
    }


@app.post("/webhook")
async def whatsapp_webhook(payload: dict, db: Session = Depends(get_db)):
    # TODO: Implement full WhatsApp Cloud API integration
    # 1. Receive WhatsApp messages
    # 2. Store them in PostgreSQL
    # 3. Generate AI response
    # 4. Send response back through Meta API
    
    print("WhatsApp webhook received:", payload)
    
    return {
        "received": True,
        "payload": payload,
        "status": "processing"
    }


# ==========================================================
# ANALYTICS
# ==========================================================

@app.get("/analytics")
async def get_analytics(db: Session = Depends(get_db)):
    total_messages = db.query(Message).count()
    total_conversations = db.query(Conversation).count()
    total_customers = db.query(Customer).count()
    # FIXED: using 'sender' instead of 'role'
    total_ai_responses = db.query(Message).filter(Message.sender == "assistant").count()
    
    return {
        "total_messages": total_messages,
        "total_ai_responses": total_ai_responses,
        "total_conversations": total_conversations,
        "total_customers": total_customers
    }


# ==========================================================
# VERSION
# ==========================================================

@app.get("/version")
async def version():
    return {
        "name": "AI WhatsApp Agent",
        "version": "1.0.0"
    }
