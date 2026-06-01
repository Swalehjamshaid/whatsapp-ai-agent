from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime
from typing import List

# ==========================================================
# DATABASE IMPORTS
# ==========================================================

from app.database import engine
from app.models import Base

# ==========================================================
# CREATE TABLES BEFORE APP STARTS
# ==========================================================

Base.metadata.create_all(bind=engine)

# ==========================================================
# APP
# ==========================================================

app = FastAPI(
    title="AI WhatsApp Agent Demo",
    version="1.0.0",
    description="Customer Service AI Demo"
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
# DEMO MEMORY STORE
# ==========================================================

conversations = []

# ==========================================================
# MODELS
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str
    message: str


class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# STARTUP
# ==========================================================

@app.on_event("startup")
async def startup_event():

    try:

        Base.metadata.create_all(bind=engine)

        print("✅ AI WhatsApp Agent Started")
        print("✅ PostgreSQL Connected")
        print("✅ Database Tables Created")

    except Exception as e:

        print(f"❌ Database Error: {e}")


# ==========================================================
# ROOT
# ==========================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "application": "AI WhatsApp Agent Demo",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }


# ==========================================================
# HEALTH CHECK
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
# DASHBOARD
# ==========================================================

@app.get("/dashboard")
async def dashboard():

    return {
        "total_conversations": len(conversations),
        "active_customers": len(
            set(
                item["customer"]
                for item in conversations
            )
        ) if conversations else 0,
        "status": "running"
    }


# ==========================================================
# CHAT
# ==========================================================

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):

    user_message = request.message.lower()

    if "order" in user_message:

        ai_reply = (
            "Your order is currently in transit "
            "and expected tomorrow."
        )

    elif "delivery" in user_message:

        ai_reply = (
            "Your shipment is scheduled "
            "for delivery within 24 hours."
        )

    elif "refund" in user_message:

        ai_reply = (
            "Your refund request has been received "
            "and is under review."
        )

    elif "hello" in user_message:

        ai_reply = (
            f"Hello {request.customer_name}, "
            "how may I assist you today?"
        )

    else:

        ai_reply = (
            "Thank you for contacting support. "
            "Our AI assistant has received your message."
        )

    record = {
        "customer": request.customer_name,
        "message": request.message,
        "reply": ai_reply,
        "timestamp": datetime.utcnow().isoformat()
    }

    conversations.append(record)

    return {
        "success": True,
        "reply": ai_reply
    }


# ==========================================================
# CONVERSATIONS
# ==========================================================

@app.get("/conversations")
async def get_conversations():

    return {
        "count": len(conversations),
        "data": conversations
    }


# ==========================================================
# CUSTOMER DETAILS
# ==========================================================

@app.get("/customers")
async def customers():

    unique_customers = list(
        set(
            item["customer"]
            for item in conversations
        )
    )

    return {
        "count": len(unique_customers),
        "customers": unique_customers
    }


# ==========================================================
# WHATSAPP WEBHOOK DEMO
# ==========================================================

@app.get("/webhook")
async def verify_webhook():

    return {
        "message": "Webhook Verification Successful"
    }


@app.post("/webhook")
async def whatsapp_webhook(payload: dict):

    return {
        "received": True,
        "payload": payload
    }


# ==========================================================
# ANALYTICS
# ==========================================================

@app.get("/analytics")
async def analytics():

    return {
        "total_messages": len(conversations),
        "total_ai_responses": len(conversations),
        "total_customers": len(
            set(
                item["customer"]
                for item in conversations
            )
        ) if conversations else 0
    }


# ==========================================================
# VERSION
# ==========================================================

@app.get("/version")
async def version():

    return {
        "name": "AI WhatsApp Agent Demo",
        "version": "1.0.0"
    }
