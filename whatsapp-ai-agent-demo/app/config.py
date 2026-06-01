# ==========================================================
# FILE: app/config.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

import os
from dotenv import load_dotenv

# ==========================================================
# LOAD ENVIRONMENT VARIABLES
# ==========================================================

load_dotenv()

# ==========================================================
# APPLICATION SETTINGS
# ==========================================================

APP_NAME = "AI WhatsApp Customer Service Agent"

APP_VERSION = "1.0.0"

DEBUG = True

# ==========================================================
# DATABASE SETTINGS
# ==========================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./whatsapp_agent.db"
)

# ==========================================================
# ANTHROPIC CLAUDE SETTINGS
# ==========================================================

ANTHROPIC_API_KEY = os.getenv(
    "ANTHROPIC_API_KEY",
    ""
)

CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL",
    "claude-sonnet-4-20250514"
)

# ==========================================================
# WHATSAPP CLOUD API SETTINGS
# ==========================================================

WHATSAPP_ACCESS_TOKEN = os.getenv(
    "WHATSAPP_ACCESS_TOKEN",
    ""
)

WHATSAPP_PHONE_NUMBER_ID = os.getenv(
    "WHATSAPP_PHONE_NUMBER_ID",
    ""
)

WHATSAPP_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_VERIFY_TOKEN",
    "demo_verify_token"
)

# ==========================================================
# FILE UPLOAD SETTINGS
# ==========================================================

UPLOAD_FOLDER = "uploads"

MAX_FILE_SIZE_MB = 10

# ==========================================================
# AI SYSTEM PROMPT
# ==========================================================

SYSTEM_PROMPT = """
You are an AI Customer Support Agent.

Your responsibilities:

- Answer customer questions professionally.
- Help customers track orders.
- Assist with delivery questions.
- Assist with refund requests.
- Analyze uploaded images.
- Remain polite and professional.
- Provide concise answers.

Always respond as a customer service representative.
"""

# ==========================================================
# DASHBOARD SETTINGS
# ==========================================================

DASHBOARD_TITLE = "AI Customer Service Dashboard"

# ==========================================================
# ANALYTICS SETTINGS
# ==========================================================

ENABLE_ANALYTICS = True

# ==========================================================
# SECURITY SETTINGS
# ==========================================================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "demo_secret_key_change_in_production"
)
