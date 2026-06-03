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
# DEEPSEEK (NEW - AI Query Engine for Logistics)
# ==========================================================

DEEPSEEK_API_KEY = os.getenv(
    "DEEPSEEK_API_KEY",
    ""
)

DEEPSEEK_MODEL = os.getenv(
    "DEEPSEEK_MODEL",
    "deepseek-chat"
)

# FIX: DeepSeek base URL without /v1 - SDK appends the path automatically
# The OpenAI-compatible SDK will add /v1/chat/completions to this base URL
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com"  # Correct: No /v1 suffix
)

DEEPSEEK_MAX_TOKENS = int(os.getenv(
    "DEEPSEEK_MAX_TOKENS",
    "4096"
))

DEEPSEEK_TEMPERATURE = float(os.getenv(
    "DEEPSEEK_TEMPERATURE",
    "0.3"
))

# ==========================================================
# AI ANALYSIS TOGGLE (NEW - Emergency Kill Switch)
# ==========================================================

# Master switch to enable/disable AI analysis
# When False: Falls back to rule-based responses only
# Use this for emergency disable or cost control
AI_ANALYSIS_ENABLED = os.getenv(
    "AI_ANALYSIS_ENABLED",
    "True"
).lower() == "true"

# Enable DeepSeek specifically for logistics (requires AI_ANALYSIS_ENABLED=True)
ENABLE_DEEPSEEK_LOGISTICS = os.getenv(
    "ENABLE_DEEPSEEK_LOGISTICS",
    "True"
).lower() == "true" and AI_ANALYSIS_ENABLED

# Cache AI responses for 5 minutes to reduce API calls
CACHE_AI_RESPONSES = os.getenv(
    "CACHE_AI_RESPONSES",
    "True"
).lower() == "true"

AI_RESPONSE_CACHE_TTL = int(os.getenv(
    "AI_RESPONSE_CACHE_TTL",
    "300"
))

# Fallback to rule-based if DeepSeek fails
AI_FALLBACK_TO_RULE_BASED = os.getenv(
    "AI_FALLBACK_TO_RULE_BASED",
    "True"
).lower() == "true"

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

WHATSAPP_API_VERSION = os.getenv(
    "WHATSAPP_API_VERSION",
    "v25.0"
)

WHATSAPP_API_URL = os.getenv(
    "WHATSAPP_API_URL",
    "https://graph.facebook.com"
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
# SYSTEM PROMPTS (Enhanced for DeepSeek)
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

# DeepSeek Logistics System Prompt
DEEPSEEK_SYSTEM_PROMPT = """
You are a Professional Logistics Operations Manager and AI Customer Support Agent.

YOUR CAPABILITIES:
- Answer logistics queries about deliveries, DNs, warehouses, cities, and dealers
- Analyze pending dispatches, POD delays, and aging reports
- Provide executive summaries and action plans
- Compare dealers, warehouses, cities, and products
- Generate data-driven recommendations

BUSINESS RULES (NEVER SHOW RAW CODES):
- PGI Status "Completed" = "Delivered"
- PGI Status "Pending" = "Pending Dispatch"  
- POD Status "Received" = "Acknowledged"
- POD Status "Pending" = "Awaiting Acknowledgement"
- Dispatch Age > 15 days = "Critical"
- POD Age > 15 days = "Urgent"

RESPONSE STYLE:
- Use WhatsApp-friendly formatting (emojis, bold, line breaks)
- Keep responses concise but informative
- Show empathy for delayed deliveries
- Provide actionable recommendations
- Always include relevant metrics (quantities, values, days)

EXAMPLE RESPONSES:
1. For pending query: "⏳ There are *15 pending deliveries* totaling *2,500 units* worth *Rs 12.5M*."
2. For dealer dashboard: "📊 *DEALER DASHBOARD: ABC TRADERS*\n• Total DNs: 45\n• Pending: 12\n• POD Pending: 8"

Always prioritize critical issues and provide clear next steps.
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
    
    # DeepSeek Configuration (Fixed)
    DEEPSEEK_API_KEY = DEEPSEEK_API_KEY
    DEEPSEEK_MODEL = DEEPSEEK_MODEL
    DEEPSEEK_BASE_URL = DEEPSEEK_BASE_URL  # Correct: No /v1 suffix
    DEEPSEEK_MAX_TOKENS = DEEPSEEK_MAX_TOKENS
    DEEPSEEK_TEMPERATURE = DEEPSEEK_TEMPERATURE
    
    # AI Analysis Toggle (Emergency Kill Switch)
    AI_ANALYSIS_ENABLED = AI_ANALYSIS_ENABLED
    ENABLE_DEEPSEEK_LOGISTICS = ENABLE_DEEPSEEK_LOGISTICS
    CACHE_AI_RESPONSES = CACHE_AI_RESPONSES
    AI_RESPONSE_CACHE_TTL = AI_RESPONSE_CACHE_TTL
    AI_FALLBACK_TO_RULE_BASED = AI_FALLBACK_TO_RULE_BASED
    
    WHATSAPP_ACCESS_TOKEN = WHATSAPP_ACCESS_TOKEN
    WHATSAPP_PHONE_NUMBER_ID = WHATSAPP_PHONE_NUMBER_ID
    WHATSAPP_BUSINESS_ACCOUNT_ID = WHATSAPP_BUSINESS_ACCOUNT_ID
    WHATSAPP_VERIFY_TOKEN = WHATSAPP_VERIFY_TOKEN
    
    WHATSAPP_API_VERSION = WHATSAPP_API_VERSION
    WHATSAPP_API_URL = WHATSAPP_API_URL
    
    UPLOAD_FOLDER = UPLOAD_FOLDER
    MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
    
    DASHBOARD_TITLE = DASHBOARD_TITLE
    ENABLE_ANALYTICS = ENABLE_ANALYTICS
    
    SYSTEM_PROMPT = SYSTEM_PROMPT
    DEEPSEEK_SYSTEM_PROMPT = DEEPSEEK_SYSTEM_PROMPT

# ==========================================================
# VALIDATION & WARNINGS
# ==========================================================

print("===================================")
print("CONFIG LOADED")
print("===================================")
print(f"DATABASE: {'✓' if DATABASE_URL else '✗'}")
print(f"WHATSAPP: {'✓' if WHATSAPP_ACCESS_TOKEN else '✗'}")
print(f"WHATSAPP API VERSION: {WHATSAPP_API_VERSION}")
print(f"OPENAI: {'✓' if OPENAI_API_KEY else '✗'}")
print(f"ANTHROPIC: {'✓' if ANTHROPIC_API_KEY else '✗'}")
print(f"DEEPSEEK: {'✓' if DEEPSEEK_API_KEY else '✗'}")
print(f"DEEPSEEK BASE URL: {DEEPSEEK_BASE_URL}")
print(f"DEEPSEEK MODEL: {DEEPSEEK_MODEL if DEEPSEEK_API_KEY else 'Not configured'}")
print("===================================")
print("AI CONFIGURATION:")
print(f"  AI_ANALYSIS_ENABLED: {AI_ANALYSIS_ENABLED}")
print(f"  ENABLE_DEEPSEEK_LOGISTICS: {ENABLE_DEEPSEEK_LOGISTICS}")
print(f"  CACHE_AI_RESPONSES: {CACHE_AI_RESPONSES}")
print(f"  AI_FALLBACK_TO_RULE_BASED: {AI_FALLBACK_TO_RULE_BASED}")
print("===================================")

# Warning if AI is disabled
if not AI_ANALYSIS_ENABLED:
    print("⚠️  WARNING: AI_ANALYSIS_ENABLED = False")
    print("   AI features are disabled. Using rule-based responses only.")
    print("   Set AI_ANALYSIS_ENABLED=True in .env to enable AI.")
    print("===================================")

# Warning if DeepSeek is not configured but enabled
if ENABLE_DEEPSEEK_LOGISTICS and not DEEPSEEK_API_KEY:
    print("⚠️  WARNING: ENABLE_DEEPSEEK_LOGISTICS = True but DEEPSEEK_API_KEY is missing")
    print("   Falling back to rule-based responses for logistics queries.")
    print("   Add DEEPSEEK_API_KEY to .env to enable DeepSeek AI.")
    print("===================================")

# ==========================================================
# GLOBAL CONFIG INSTANCE
# ==========================================================

config = Config()

# ==========================================================
# END FILE
# ==========================================================
