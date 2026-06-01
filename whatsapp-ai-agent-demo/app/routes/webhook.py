# ==========================================================
# FILE: app/routes/webhook.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from fastapi import APIRouter
from fastapi import Request

from app.config import (
    WHATSAPP_VERIFY_TOKEN
)

from app.services.whatsapp_service import (
    parse_whatsapp_message,
    verify_webhook,
    send_text_message
)

from app.services.claude_service import (
    ask_claude
)

from app.services.conversation_service import (
    add_user_message,
    add_ai_message
)

# ==========================================================
# ROUTER
# ==========================================================

router = APIRouter(
    prefix="/webhook",
    tags=["WhatsApp Webhook"]
)

# ==========================================================
# WEBHOOK VERIFICATION
# ==========================================================

@router.get("/")
async def webhook_verification(

    hub_mode: str = "",
    hub_verify_token: str = "",
    hub_challenge: str = ""

):

    result = verify_webhook(
        hub_verify_token,
        hub_challenge,
        WHATSAPP_VERIFY_TOKEN
    )

    if result["success"]:

        return int(
            result["challenge"]
        )

    return {
        "success": False,
        "message": "Verification failed"
    }

# ==========================================================
# RECEIVE WHATSAPP MESSAGE
# ==========================================================

@router.post("/")
async def receive_message(
    request: Request
):

    payload = await request.json()

    parsed_message = parse_whatsapp_message(
        payload
    )

    if not parsed_message:

        return {
            "success": True,
            "message": "No message found"
        }

    phone_number = parsed_message["from"]

    customer_message = parsed_message["text"]

    # ------------------------------------------------------
    # SAVE CUSTOMER MESSAGE
    # ------------------------------------------------------

    add_user_message(
        phone_number,
        customer_message
    )

    # ------------------------------------------------------
    # AI RESPONSE
    # ------------------------------------------------------

    ai_reply = ask_claude(
        customer_message
    )

    # ------------------------------------------------------
    # SAVE AI RESPONSE
    # ------------------------------------------------------

    add_ai_message(
        phone_number,
        ai_reply
    )

    # ------------------------------------------------------
    # SEND WHATSAPP RESPONSE
    # ------------------------------------------------------

    whatsapp_response = send_text_message(
        phone_number,
        ai_reply
    )

    return {
        "success": True,
        "customer_message":
            customer_message,
        "ai_reply":
            ai_reply,
        "whatsapp_response":
            whatsapp_response
    }

# ==========================================================
# TEST WEBHOOK
# ==========================================================

@router.get("/test")
async def test_webhook():

    return {
        "success": True,
        "message":
            "Webhook is active"
    }

# ==========================================================
# DEMO MESSAGE
# ==========================================================

@router.post("/demo")
async def demo_message():

    customer_message = (
        "Where is my order?"
    )

    ai_reply = ask_claude(
        customer_message
    )

    return {
        "success": True,
        "customer_message":
            customer_message,
        "ai_reply":
            ai_reply
    }

# ==========================================================
# WEBHOOK STATUS
# ==========================================================

@router.get("/status")
async def webhook_status():

    return {
        "service":
            "WhatsApp Webhook",
        "status":
            "running",
        "verify_token":
            bool(
                WHATSAPP_VERIFY_TOKEN
            )
    }
