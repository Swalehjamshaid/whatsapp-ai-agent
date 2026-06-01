# ==========================================================
# FILE: app/routes/admin.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from fastapi import APIRouter

from app.services.conversation_service import (
    get_all_conversations,
    dashboard_stats,
    clear_all_conversations,
    total_customers,
    total_messages
)

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
    prefix="/admin",
    tags=["Admin Dashboard"]
)

# ==========================================================
# DASHBOARD
# ==========================================================

@router.get("/")
async def admin_dashboard():

    return {
        "application": "AI WhatsApp Customer Service Agent",
        "status": "running",
        "analytics": dashboard_stats(),
        "services": {
            "claude": claude_status(),
            "whatsapp": whatsapp_status(),
            "vision": vision_status()
        }
    }

# ==========================================================
# ANALYTICS
# ==========================================================

@router.get("/analytics")
async def analytics():

    return dashboard_stats()

# ==========================================================
# CONVERSATIONS
# ==========================================================

@router.get("/conversations")
async def conversations():

    return {
        "success": True,
        "data": get_all_conversations()
    }

# ==========================================================
# CUSTOMERS
# ==========================================================

@router.get("/customers")
async def customers():

    return {
        "total_customers": total_customers()
    }

# ==========================================================
# MESSAGES
# ==========================================================

@router.get("/messages")
async def messages():

    return {
        "total_messages": total_messages()
    }

# ==========================================================
# SYSTEM STATUS
# ==========================================================

@router.get("/status")
async def system_status():

    return {
        "application": "AI WhatsApp Agent",
        "database": "connected",
        "claude": claude_status(),
        "whatsapp": whatsapp_status(),
        "vision": vision_status()
    }

# ==========================================================
# RESET DEMO DATA
# ==========================================================

@router.delete("/reset")
async def reset_demo_data():

    clear_all_conversations()

    return {
        "success": True,
        "message": "All demo conversations deleted."
    }

# ==========================================================
# HEALTH CHECK
# ==========================================================

@router.get("/health")
async def health():

    return {
        "status": "healthy",
        "service": "admin"
    }
