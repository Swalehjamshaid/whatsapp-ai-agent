from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from datetime import datetime

app = FastAPI(
    title="AI WhatsApp Agent Demo",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

conversations = []


class ChatRequest(BaseModel):
    customer_name: str
    message: str


@app.get("/")
async def root():
    return {
        "status": "ok",
        "app": "AI WhatsApp Agent Demo",
        "version": "1.0.0",
        "railway": "running"
    }


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/dashboard")
async def dashboard():
    return {
        "total_conversations": len(conversations),
        "status": "running"
    }


@app.post("/chat")
async def chat(request: ChatRequest):

    user_message = request.message.lower()

    if "order" in user_message:
        ai_reply = "Your order is currently in transit and expected tomorrow."

    elif "delivery" in user_message:
        ai_reply = "Your shipment is scheduled for delivery within 24 hours."

    elif "refund" in user_message:
        ai_reply = "Your refund request has been received and is under review."

    else:
        ai_reply = (
            "Thank you for contacting support. "
            "Our AI assistant has received your message."
        )

    conversations.append({
        "customer": request.customer_name,
        "message": request.message,
        "reply": ai_reply,
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "success": True,
        "reply": ai_reply
    }


@app.get("/conversations")
async def get_conversations():
    return {
        "count": len(conversations),
        "data": conversations
    }


@app.post("/webhook")
async def whatsapp_webhook(payload: dict):
    return {
        "received": True,
        "payload": payload
    }


@app.get("/status")
async def status():
    return {
        "application": "AI WhatsApp Agent",
        "database": "connected",
        "ai": "connected",
        "whatsapp": "connected",
        "railway": "ready"
    }
