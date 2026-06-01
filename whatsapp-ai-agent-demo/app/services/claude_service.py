# ==========================================================
# FILE: app/services/claude_service.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from anthropic import Anthropic
from app.config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    SYSTEM_PROMPT
)

# ==========================================================
# CLAUDE CLIENT
# ==========================================================

client = None

if ANTHROPIC_API_KEY:
    client = Anthropic(
        api_key=ANTHROPIC_API_KEY
    )

# ==========================================================
# DEMO FALLBACK RESPONSES
# ==========================================================

def get_demo_response(message: str) -> str:

    message = message.lower()

    if "order" in message:
        return (
            "Your order is currently in transit "
            "and expected to arrive tomorrow."
        )

    elif "delivery" in message:
        return (
            "Your shipment is scheduled "
            "for delivery within 24 hours."
        )

    elif "refund" in message:
        return (
            "Your refund request has been received "
            "and is currently under review."
        )

    elif "hello" in message:
        return (
            "Hello! Welcome to customer support. "
            "How may I assist you today?"
        )

    elif "help" in message:
        return (
            "I can help with orders, deliveries, "
            "refunds, and general inquiries."
        )

    return (
        "Thank you for contacting customer support. "
        "How may I assist you today?"
    )

# ==========================================================
# CLAUDE RESPONSE
# ==========================================================

def ask_claude(message: str) -> str:

    try:

        if not ANTHROPIC_API_KEY:
            return get_demo_response(message)

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": message
                }
            ]
        )

        return response.content[0].text

    except Exception as e:

        print(f"Claude Error: {e}")

        return get_demo_response(message)

# ==========================================================
# CONVERSATION MEMORY
# ==========================================================

def ask_claude_with_history(
    current_message: str,
    conversation_history: list
) -> str:

    try:

        if not ANTHROPIC_API_KEY:
            return get_demo_response(current_message)

        messages = []

        for item in conversation_history:

            messages.append(
                {
                    "role": item["role"],
                    "content": item["content"]
                }
            )

        messages.append(
            {
                "role": "user",
                "content": current_message
            }
        )

        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=500,
            system=SYSTEM_PROMPT,
            messages=messages
        )

        return response.content[0].text

    except Exception as e:

        print(f"Claude Memory Error: {e}")

        return get_demo_response(current_message)

# ==========================================================
# HEALTH CHECK
# ==========================================================

def claude_status():

    return {
        "service": "Claude AI",
        "connected": bool(ANTHROPIC_API_KEY),
        "model": CLAUDE_MODEL
    }
