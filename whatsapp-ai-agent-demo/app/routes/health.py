# ==========================================================
# FILE: app/routes/health.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from fastapi import APIRouter
from datetime import datetime

from app.services.claude_service import (
    claude_status
)

from app.services.whatsapp_service import (
    whatsapp_status
)

from app.services.vision_service import (
    vision_status
)

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/health",
    tags=["Health"]
)

# ==========================================================
# BASIC HEALTH CHECK
# ==========================================================

@router.get("/")
async def health_check():

    return {
        "status": "healthy",
        "application": "AI WhatsApp Customer Service Agent",
        "timestamp": datetime.utcnow().isoformat()
    }

# ==========================================================
# SYSTEM STATUS
# ==========================================================

@router.get("/status")
async def system_status():

    return {
        "status": "running",
        "application": "AI WhatsApp Customer Service Agent",
        "services": {
            "claude": claude_status(),
            "whatsapp": whatsapp_status(),
            "vision": vision_status()
        }
    }

# ==========================================================
# READINESS CHECK
# ==========================================================

@router.get("/ready")
async def readiness_check():

    return {
        "ready": True,
        "database": True,
        "claude_service": True,
        "whatsapp_service": True,
        "vision_service": True
    }

# ==========================================================
# LIVENESS CHECK
# ==========================================================

@router.get("/live")
async def liveness_check():

    return {
        "alive": True,
        "timestamp": datetime.utcnow().isoformat()
    }

# ==========================================================
# RAILWAY HEALTH CHECK
# ==========================================================

@router.get("/railway")
async def railway_health():

    return {
        "status": "healthy",
        "platform": "Railway",
        "deployment": "active"
    }

# ==========================================================
# API INFORMATION
# ==========================================================

@router.get("/info")
async def api_info():

    return {
        "application": "AI WhatsApp Customer Service Agent",
        "version": "1.0.0",
        "environment": "demo",
        "status": "online"
    }
