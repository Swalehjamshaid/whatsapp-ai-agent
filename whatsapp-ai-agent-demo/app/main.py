from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(
    title="AI WhatsApp Agent Demo",
    version="1.0.0"
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
# DEMO DATABASE
# ==========================================================

conversations = []

# ==========================================================
# MODELS
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str
    message: str

# ==========================================================
# HEALTH CHECK
# ==========================================================

@app.get("/")
def root():
    return {
        "status": "ok",
        "app": "AI WhatsApp Agent Demo",
        "version": "1.0.0"
    }

# ==========================================================
# DASHBOARD
# ==========================================================

@app.get("/dashboard")
def dashboard():

    return {
        "total_conversations": len(conversations),
        "status": "running"
    }

# ==========================================================
# AI CHAT DEMO
# ==========================================================

@app.post("/chat")
def chat(request: ChatRequest):

    user_message = request.message.lower()

    if "order" in user_message:
        ai_reply = (
            "Your order is currently in transit "
            "and expected to arrive tomorrow."
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
# CONVERSATION LOGS
# ==========================================================

@app.get("/conversations")
def get_conversations():

    return {
        "count": len(conversations),
        "data": conversations
    }

# ==========================================================
# WHATSAPP WEBHOOK DEMO
# ==========================================================

@app.post("/webhook")
def whatsapp_webhook(payload: dict):

    return {
        "received": True,
        "payload": payload
    }

# ==========================================================
# SYSTEM STATUS
# ==========================================================

@app.get("/status")
def status():

    return {
        "application": "AI WhatsApp Agent",
        "database": "connected",
        "ai": "connected",
        "whatsapp": "connected",
        "railway": "ready"
    }
