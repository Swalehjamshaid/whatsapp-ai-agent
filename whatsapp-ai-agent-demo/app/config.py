# ==========================================================
# FILE: app/config.py
# PROJECT: AI WhatsApp Customer Service Agent
# ==========================================================

import os
from dotenv import load_dotenv

# ==========================================================
# LOAD ENVIRONMENT VARIABLES
# ==========================================================

load_dotenv()

# ==========================================================
# APPLICATION
# ==========================================================

APP_NAME = "AI WhatsApp Customer Service Agent"
APP_VERSION = "1.0.0"

DEBUG = os.getenv(
    "DEBUG",
    "False"
).lower() == "true"

# ==========================================================
# DATABASE
# ==========================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL environment variable is missing"
    )

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )

# ==========================================================
# SECURITY
# ==========================================================

SECRET_KEY = os.getenv(
    "SECRET_KEY",
    "change-this-secret-key"
)

# ==========================================================
# OPENAI
# ==========================================================

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    ""
)

# ==========================================================
# ANTHROPIC
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
# WHATSAPP CLOUD API
# ==========================================================

WHATSAPP_ACCESS_TOKEN = os.getenv(
    "WHATSAPP_ACCESS_TOKEN",
    ""
)

WHATSAPP_PHONE_NUMBER_ID = os.getenv(
    "WHATSAPP_PHONE_NUMBER_ID",
    ""
)

WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv(
    "WHATSAPP_BUSINESS_ACCOUNT_ID",
    ""
)

WHATSAPP_VERIFY_TOKEN = os.getenv(
    "WHATSAPP_VERIFY_TOKEN",
    ""
)

# ==========================================================
# FILES
# ==========================================================

UPLOAD_FOLDER = "uploads"
MAX_FILE_SIZE_MB = 10

# ==========================================================
# DASHBOARD
# ==========================================================

DASHBOARD_TITLE = "AI Customer Service Dashboard"
ENABLE_ANALYTICS = True

# ==========================================================
# SYSTEM PROMPT
# ==========================================================

SYSTEM_PROMPT = """
You are an AI Customer Support Agent.

Responsibilities:

* Customer support
* Order tracking
* Delivery assistance
* Refund handling
* WhatsApp communication
* Image analysis

Always be professional,
helpful and concise.
"""

# ==========================================================
# CONFIG CLASS
# ==========================================================

class Config:
    APP_NAME = APP_NAME
    APP_VERSION = APP_VERSION
    DEBUG = DEBUG
    
    DATABASE_URL = DATABASE_URL
    
    SECRET_KEY = SECRET_KEY
    
    OPENAI_API_KEY = OPENAI_API_KEY
    
    ANTHROPIC_API_KEY = ANTHROPIC_API_KEY
    CLAUDE_MODEL = CLAUDE_MODEL
    
    WHATSAPP_ACCESS_TOKEN = WHATSAPP_ACCESS_TOKEN
    WHATSAPP_PHONE_NUMBER_ID = WHATSAPP_PHONE_NUMBER_ID
    WHATSAPP_BUSINESS_ACCOUNT_ID = WHATSAPP_BUSINESS_ACCOUNT_ID
    WHATSAPP_VERIFY_TOKEN = WHATSAPP_VERIFY_TOKEN
    
    UPLOAD_FOLDER = UPLOAD_FOLDER
    MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
    
    DASHBOARD_TITLE = DASHBOARD_TITLE
    ENABLE_ANALYTICS = ENABLE_ANALYTICS
    
    SYSTEM_PROMPT = SYSTEM_PROMPT

# ==========================================================
# GLOBAL CONFIG
# ==========================================================

config = Config()

print("===================================")
print("CONFIG LOADED")
print("DATABASE:", bool(DATABASE_URL))
print("WHATSAPP:", bool(WHATSAPP_ACCESS_TOKEN))
print("OPENAI:", bool(OPENAI_API_KEY))
print("ANTHROPIC:", bool(ANTHROPIC_API_KEY))
print("===================================")

# ==========================================================
# END FILE
# ==========================================================
