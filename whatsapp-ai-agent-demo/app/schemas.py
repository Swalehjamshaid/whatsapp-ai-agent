# ==========================================================
# FILE: app/schemas.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime


# ==========================================================
# CHAT REQUEST
# ==========================================================

class ChatRequest(BaseModel):
    customer_name: str
    phone_number: str
    message: str


# ==========================================================
# CHAT RESPONSE
# ==========================================================

class ChatResponse(BaseModel):
    success: bool
    reply: str


# ==========================================================
# CUSTOMER RESPONSE
# ==========================================================

class CustomerResponse(BaseModel):
    id: int
    name: str
    phone_number: str
    email: Optional[str] = None

    class Config:
        from_attributes = True


# ==========================================================
# MESSAGE RESPONSE
# ==========================================================

class MessageResponse(BaseModel):
    id: int
    sender: str
    content: str
    message_type: str
    created_at: datetime

    class Config:
        from_attributes = True


# ==========================================================
# CONVERSATION RESPONSE
# ==========================================================

class ConversationResponse(BaseModel):
    id: int
    customer_id: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


# ==========================================================
# IMAGE ANALYSIS REQUEST
# ==========================================================

class ImageAnalysisRequest(BaseModel):
    image_url: str


# ==========================================================
# IMAGE ANALYSIS RESPONSE
# ==========================================================

class ImageAnalysisResponse(BaseModel):
    success: bool
    analysis: str


# ==========================================================
# WEBHOOK PAYLOAD
# ==========================================================

class WebhookPayload(BaseModel):
    customer_name: str
    phone_number: str
    message: str


# ==========================================================
# ANALYTICS RESPONSE
# ==========================================================

class AnalyticsResponse(BaseModel):
    total_customers: int
    total_conversations: int
    total_messages: int
    total_ai_responses: int


# ==========================================================
# DASHBOARD RESPONSE
# ==========================================================

class DashboardResponse(BaseModel):
    total_customers: int
    total_conversations: int
    active_conversations: int
    total_messages: int
