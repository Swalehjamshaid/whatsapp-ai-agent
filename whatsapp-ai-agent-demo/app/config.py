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

# ==========================================================
# CONFIG CLASS AND LOAD FUNCTION (FIX FOR THE ERROR)
# ==========================================================

class Config:
    """Configuration class to hold all settings"""
    def __init__(self):
        self.APP_NAME = APP_NAME
        self.APP_VERSION = APP_VERSION
        self.DEBUG = DEBUG
        self.DATABASE_URL = DATABASE_URL
        self.ANTHROPIC_API_KEY = ANTHROPIC_API_KEY
        self.CLAUDE_MODEL = CLAUDE_MODEL
        self.WHATSAPP_ACCESS_TOKEN = WHATSAPP_ACCESS_TOKEN
        self.WHATSAPP_PHONE_NUMBER_ID = WHATSAPP_PHONE_NUMBER_ID
        self.WHATSAPP_VERIFY_TOKEN = WHATSAPP_VERIFY_TOKEN
        self.UPLOAD_FOLDER = UPLOAD_FOLDER
        self.MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
        self.SYSTEM_PROMPT = SYSTEM_PROMPT
        self.DASHBOARD_TITLE = DASHBOARD_TITLE
        self.ENABLE_ANALYTICS = ENABLE_ANALYTICS
        self.SECRET_KEY = SECRET_KEY
    
    def load(self):
        """Load/reload configuration - useful for dynamic config updates"""
        print("✅ Configuration loaded successfully")
        print(f"   - Database: {self.DATABASE_URL.split('@')[-1] if '@' in self.DATABASE_URL else self.DATABASE_URL}")
        print(f"   - WhatsApp: {'Configured' if self.WHATSAPP_ACCESS_TOKEN else 'Not configured'}")
        print(f"   - Claude AI: {'Configured' if self.ANTHROPIC_API_KEY else 'Not configured'}")
        return self

# Create a global config instance
config = Config()

# Optional: Auto-load on import
config.load()
