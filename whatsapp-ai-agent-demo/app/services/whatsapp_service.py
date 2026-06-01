# ==========================================================
# FILE: app/services/whatsapp_service.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

import requests

from app.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_PHONE_NUMBER_ID
)

# ==========================================================
# WHATSAPP API URL
# ==========================================================

def get_whatsapp_url():

    return (
        f"https://graph.facebook.com/v23.0/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )

# ==========================================================
# SEND TEXT MESSAGE
# ==========================================================

def send_text_message(
    phone_number: str,
    message: str
):

    # Demo Mode
    if (
        not WHATSAPP_ACCESS_TOKEN
        or not WHATSAPP_PHONE_NUMBER_ID
    ):

        return {
            "success": True,
            "mode": "demo",
            "phone_number": phone_number,
            "message": message
        }

    try:

        headers = {
            "Authorization":
                f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type":
                "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message
            }
        }

        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=30
        )

        return {
            "success": True,
            "status_code": response.status_code,
            "response": response.json()
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }

# ==========================================================
# SEND TEMPLATE MESSAGE
# ==========================================================

def send_template_message(
    phone_number: str,
    template_name: str
):

    if (
        not WHATSAPP_ACCESS_TOKEN
        or not WHATSAPP_PHONE_NUMBER_ID
    ):

        return {
            "success": True,
            "mode": "demo",
            "template": template_name
        }

    try:

        headers = {
            "Authorization":
                f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type":
                "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {
                    "code": "en_US"
                }
            }
        }

        response = requests.post(
            get_whatsapp_url(),
            headers=headers,
            json=payload,
            timeout=30
        )

        return {
            "success": True,
            "response": response.json()
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }

# ==========================================================
# PARSE INCOMING MESSAGE
# ==========================================================

def parse_whatsapp_message(payload: dict):

    try:

        entry = payload["entry"][0]

        changes = entry["changes"][0]

        value = changes["value"]

        messages = value.get(
            "messages",
            []
        )

        if not messages:

            return None

        message = messages[0]

        return {
            "message_id":
                message.get("id"),

            "from":
                message.get("from"),

            "type":
                message.get("type"),

            "text":
                (
                    message.get(
                        "text",
                        {}
                    ).get(
                        "body",
                        ""
                    )
                )
        }

    except Exception:

        return None

# ==========================================================
# VERIFY WEBHOOK
# ==========================================================

def verify_webhook(
    verify_token: str,
    challenge: str,
    configured_token: str
):

    if verify_token == configured_token:

        return {
            "success": True,
            "challenge": challenge
        }

    return {
        "success": False
    }

# ==========================================================
# DEMO MESSAGE
# ==========================================================

def create_demo_message():

    return {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "id": "demo123",
                                    "from": "923001234567",
                                    "type": "text",
                                    "text": {
                                        "body":
                                            "Where is my order?"
                                    }
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

# ==========================================================
# SERVICE STATUS
# ==========================================================

def whatsapp_status():

    return {
        "service": "WhatsApp Cloud API",
        "configured":
            bool(
                WHATSAPP_ACCESS_TOKEN
            ),
        "phone_number_id":
            bool(
                WHATSAPP_PHONE_NUMBER_ID
            )
    }
