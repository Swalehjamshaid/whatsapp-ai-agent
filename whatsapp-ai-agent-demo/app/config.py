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
APP_VERSION = "2.0.0"

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
# AI PROVIDER SELECTION
# ==========================================================

# Primary AI Provider (groq, deepseek, openai, claude, gemini, ollama)
AI_PROVIDER = os.getenv(
    "AI_PROVIDER",
    "groq"
)

# Single fallback provider if primary fails
AI_FALLBACK_PROVIDER = os.getenv(
    "AI_FALLBACK_PROVIDER",
    "deepseek"
)

# Multiple fallback providers in order (comma-separated)
AI_FALLBACK_PROVIDERS = os.getenv(
    "AI_FALLBACK_PROVIDERS",
    "deepseek,openai"
)

# ==========================================================
# GROQ API (NEW - PRIMARY FOR WHATSAPP)
# ==========================================================

GROQ_API_KEY = os.getenv(
    "GROQ_API_KEY",
    ""
)

# Available GROQ models:
# - llama-3.3-70b-versatile (Most capable - RECOMMENDED)
# - llama3-70b-8192 (Alternative)
# - llama3-8b-8192 (Fastest)
# - mixtral-8x7b-32768 (Good balance)
GROQ_MODEL = os.getenv(
    "GROQ_MODEL",
    "llama-3.3-70b-versatile"
)

GROQ_MAX_TOKENS = int(os.getenv(
    "GROQ_MAX_TOKENS",
    "500"
))

GROQ_TEMPERATURE = float(os.getenv(
    "GROQ_TEMPERATURE",
    "0.7"
))

# ==========================================================
# OPENAI
# ==========================================================

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    ""
)

OPENAI_MODEL = os.getenv(
    "OPENAI_MODEL",
    "gpt-4o-mini"
)

OPENAI_MAX_TOKENS = int(os.getenv(
    "OPENAI_MAX_TOKENS",
    "4096"
))

OPENAI_TEMPERATURE = float(os.getenv(
    "OPENAI_TEMPERATURE",
    "0.3"
))

# ==========================================================
# ANTHROPIC (Claude)
# ==========================================================

ANTHROPIC_API_KEY = os.getenv(
    "ANTHROPIC_API_KEY",
    ""
)

CLAUDE_MODEL = os.getenv(
    "CLAUDE_MODEL",
    "claude-3-sonnet-20240229"
)

CLAUDE_MAX_TOKENS = int(os.getenv(
    "CLAUDE_MAX_TOKENS",
    "4096"
))

CLAUDE_TEMPERATURE = float(os.getenv(
    "CLAUDE_TEMPERATURE",
    "0.3"
))

# ==========================================================
# DEEPSEEK (AI Query Engine for Logistics)
# ==========================================================

DEEPSEEK_API_KEY = os.getenv(
    "DEEPSEEK_API_KEY",
    ""
)

DEEPSEEK_MODEL = os.getenv(
    "DEEPSEEK_MODEL",
    "deepseek-chat"
)

DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL",
    "https://api.deepseek.com"
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
# GEMINI (Google - Optional)
# ==========================================================

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
)

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-pro"
)

# ==========================================================
# OLLAMA (Local LLM - Optional)
# ==========================================================

OLLAMA_HOST = os.getenv(
    "OLLAMA_HOST",
    "http://localhost:11434"
)

OLLAMA_MODEL = os.getenv(
    "OLLAMA_MODEL",
    "llama2"
)

# ==========================================================
# REDIS CACHE SETTINGS
# ==========================================================

REDIS_URL = os.getenv(
    "REDIS_URL",
    ""
)

REDIS_HOST = os.getenv(
    "REDIS_HOST",
    "localhost"
)

REDIS_PORT = int(
    os.getenv(
        "REDIS_PORT",
        "6379"
    )
)

REDIS_DB = int(
    os.getenv(
        "REDIS_DB",
        "0"
    )
)

REDIS_PASSWORD = os.getenv(
    "REDIS_PASSWORD",
    ""
)

REDIS_SSL = os.getenv(
    "REDIS_SSL",
    "False"
).lower() == "true"

# ==========================================================
# AI TIMEOUT & PERFORMANCE
# ==========================================================

AI_TIMEOUT_SECONDS = int(
    os.getenv(
        "AI_TIMEOUT_SECONDS",
        "15"
    )
)

AI_MAX_RETRIES = int(
    os.getenv(
        "AI_MAX_RETRIES",
        "2"
    )
)

AI_RETRY_DELAY_SECONDS = int(
    os.getenv(
        "AI_RETRY_DELAY_SECONDS",
        "1"
    )
)

# ==========================================================
# AI ANALYSIS TOGGLE (Emergency Kill Switch)
# ==========================================================

AI_ANALYSIS_ENABLED = os.getenv(
    "AI_ANALYSIS_ENABLED",
    "True"
).lower() == "true"

# Enable DeepSeek specifically for logistics
ENABLE_DEEPSEEK_LOGISTICS = os.getenv(
    "ENABLE_DEEPSEEK_LOGISTICS",
    "False"
).lower() == "true" and AI_ANALYSIS_ENABLED

# Enable GROQ for WhatsApp responses (NEW)
ENABLE_GROQ = os.getenv(
    "ENABLE_GROQ",
    "True"
).lower() == "true" and AI_ANALYSIS_ENABLED

# Cache AI responses to reduce API calls
CACHE_AI_RESPONSES = os.getenv(
    "CACHE_AI_RESPONSES",
    "True"
).lower() == "true"

AI_RESPONSE_CACHE_TTL = int(os.getenv(
    "AI_RESPONSE_CACHE_TTL",
    "300"
))

# Fallback to rule-based if all AI providers fail
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

WHATSAPP_MESSAGE_TIMEOUT = int(os.getenv(
    "WHATSAPP_MESSAGE_TIMEOUT",
    "60"
))

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
# SYSTEM PROMPTS
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

# GROQ System Prompt for WhatsApp (NEW)
GROQ_SYSTEM_PROMPT = """
You are a Professional Logistics Operations Manager responding on WhatsApp.

WHATSAPP FORMATTING RULES (CRITICAL):
- Use emojis: 📊 🚨 ✅ ❌ 💡 📦 🏪 🔢 🌆 👑
- Use **bold** for numbers: **15 pending deliveries**
- Use bullet points with • or -
- Keep paragraphs short (2-3 lines)
- NEVER return raw JSON
- ALWAYS return human-readable text

BUSINESS RULES:
- "Completed" = "Delivered" ✅
- "Pending" = "Pending Dispatch" ⏳
- POD "Received" = "Acknowledged" ✅
- POD "Pending" = "Awaiting Acknowledgement" 📋
- Age > 15 days = "Critical" 🚨

Be conversational, helpful, and concise.
"""

# DeepSeek Logistics System Prompt (preserved)
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
    # Application
    APP_NAME = APP_NAME
    APP_VERSION = APP_VERSION
    DEBUG = DEBUG
    
    # Database
    DATABASE_URL = DATABASE_URL
    
    # Security
    SECRET_KEY = SECRET_KEY
    
    # AI Provider Selection
    AI_PROVIDER = AI_PROVIDER
    AI_FALLBACK_PROVIDER = AI_FALLBACK_PROVIDER
    AI_FALLBACK_PROVIDERS = AI_FALLBACK_PROVIDERS
    
    # GROQ (NEW)
    GROQ_API_KEY = GROQ_API_KEY
    GROQ_MODEL = GROQ_MODEL
    GROQ_MAX_TOKENS = GROQ_MAX_TOKENS
    GROQ_TEMPERATURE = GROQ_TEMPERATURE
    
    # OpenAI
    OPENAI_API_KEY = OPENAI_API_KEY
    OPENAI_MODEL = OPENAI_MODEL
    OPENAI_MAX_TOKENS = OPENAI_MAX_TOKENS
    OPENAI_TEMPERATURE = OPENAI_TEMPERATURE
    
    # Anthropic/Claude
    ANTHROPIC_API_KEY = ANTHROPIC_API_KEY
    CLAUDE_MODEL = CLAUDE_MODEL
    CLAUDE_MAX_TOKENS = CLAUDE_MAX_TOKENS
    CLAUDE_TEMPERATURE = CLAUDE_TEMPERATURE
    
    # DeepSeek
    DEEPSEEK_API_KEY = DEEPSEEK_API_KEY
    DEEPSEEK_MODEL = DEEPSEEK_MODEL
    DEEPSEEK_BASE_URL = DEEPSEEK_BASE_URL
    DEEPSEEK_MAX_TOKENS = DEEPSEEK_MAX_TOKENS
    DEEPSEEK_TEMPERATURE = DEEPSEEK_TEMPERATURE
    
    # Gemini
    GEMINI_API_KEY = GEMINI_API_KEY
    GEMINI_MODEL = GEMINI_MODEL
    
    # Ollama
    OLLAMA_HOST = OLLAMA_HOST
    OLLAMA_MODEL = OLLAMA_MODEL
    
    # Redis Cache
    REDIS_URL = REDIS_URL
    REDIS_HOST = REDIS_HOST
    REDIS_PORT = REDIS_PORT
    REDIS_DB = REDIS_DB
    REDIS_PASSWORD = REDIS_PASSWORD
    REDIS_SSL = REDIS_SSL
    
    # AI Timeout & Performance
    AI_TIMEOUT_SECONDS = AI_TIMEOUT_SECONDS
    AI_MAX_RETRIES = AI_MAX_RETRIES
    AI_RETRY_DELAY_SECONDS = AI_RETRY_DELAY_SECONDS
    
    # AI Analysis Toggle
    AI_ANALYSIS_ENABLED = AI_ANALYSIS_ENABLED
    ENABLE_DEEPSEEK_LOGISTICS = ENABLE_DEEPSEEK_LOGISTICS
    ENABLE_GROQ = ENABLE_GROQ
    CACHE_AI_RESPONSES = CACHE_AI_RESPONSES
    AI_RESPONSE_CACHE_TTL = AI_RESPONSE_CACHE_TTL
    AI_FALLBACK_TO_RULE_BASED = AI_FALLBACK_TO_RULE_BASED
    
    # WhatsApp
    WHATSAPP_ACCESS_TOKEN = WHATSAPP_ACCESS_TOKEN
    WHATSAPP_PHONE_NUMBER_ID = WHATSAPP_PHONE_NUMBER_ID
    WHATSAPP_BUSINESS_ACCOUNT_ID = WHATSAPP_BUSINESS_ACCOUNT_ID
    WHATSAPP_VERIFY_TOKEN = WHATSAPP_VERIFY_TOKEN
    WHATSAPP_API_VERSION = WHATSAPP_API_VERSION
    WHATSAPP_API_URL = WHATSAPP_API_URL
    WHATSAPP_MESSAGE_TIMEOUT = WHATSAPP_MESSAGE_TIMEOUT
    
    # Files
    UPLOAD_FOLDER = UPLOAD_FOLDER
    MAX_FILE_SIZE_MB = MAX_FILE_SIZE_MB
    
    # Dashboard
    DASHBOARD_TITLE = DASHBOARD_TITLE
    ENABLE_ANALYTICS = ENABLE_ANALYTICS
    
    # System Prompts (preserved)
    SYSTEM_PROMPT = SYSTEM_PROMPT
    GROQ_SYSTEM_PROMPT = GROQ_SYSTEM_PROMPT
    DEEPSEEK_SYSTEM_PROMPT = DEEPSEEK_SYSTEM_PROMPT
    
    @property
    def REDIS_CONNECTION_URL(self):
        """Generate Redis connection URL from settings or environment"""
        if self.REDIS_URL:
            return self.REDIS_URL
        
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    @property
    def AVAILABLE_PROVIDERS(self):
        """Return list of configured AI providers"""
        providers = []
        if self.GROQ_API_KEY:
            providers.append("groq")
        if self.OPENAI_API_KEY:
            providers.append("openai")
        if self.DEEPSEEK_API_KEY:
            providers.append("deepseek")
        if self.ANTHROPIC_API_KEY:
            providers.append("claude")
        if self.GEMINI_API_KEY:
            providers.append("gemini")
        return providers
    
    @property
    def AI_READY(self):
        """Check if at least one AI provider is configured"""
        return len(self.AVAILABLE_PROVIDERS) > 0 and self.AI_ANALYSIS_ENABLED


# ==========================================================
# GLOBAL CONFIG INSTANCE
# ==========================================================

config = Config()

# ==========================================================
# VALIDATION & WARNINGS
# ==========================================================

print("===================================")
print("CONFIG LOADED - GROQ EDITION")
print("===================================")
print(f"DATABASE: {'✓' if DATABASE_URL else '✗'}")
print(f"WHATSAPP: {'✓' if WHATSAPP_ACCESS_TOKEN else '✗'}")
print(f"WHATSAPP API VERSION: {WHATSAPP_API_VERSION}")
print("===================================")
print("AI PROVIDERS:")
print(f"  PRIMARY: {AI_PROVIDER}")
print(f"  FALLBACK: {AI_FALLBACK_PROVIDER}")
print(f"  FALLBACK CHAIN: {AI_FALLBACK_PROVIDERS}")
print("===================================")
print("API KEYS:")
print(f"  GROQ: {'✓' if GROQ_API_KEY else '✗'} (Model: {GROQ_MODEL}) ← PRIMARY")
print(f"  DEEPSEEK: {'✓' if DEEPSEEK_API_KEY else '✗'} (Model: {DEEPSEEK_MODEL})")
print(f"  OPENAI: {'✓' if OPENAI_API_KEY else '✗'} (Model: {OPENAI_MODEL})")
print(f"  CLAUDE: {'✓' if ANTHROPIC_API_KEY else '✗'} (Model: {CLAUDE_MODEL})")
print(f"  GEMINI: {'✓' if GEMINI_API_KEY else '✗'} (Model: {GEMINI_MODEL})")
print("===================================")
print("REDIS CACHE:")
if REDIS_URL:
    print(f"  USING REDIS_URL from environment")
    print(f"  URL: {'✓' if REDIS_URL else '✗'}")
else:
    print(f"  HOST: {REDIS_HOST}:{REDIS_PORT}")
    print(f"  DB: {REDIS_DB}")
print(f"  CACHE ENABLED: {CACHE_AI_RESPONSES}")
print(f"  CACHE TTL: {AI_RESPONSE_CACHE_TTL}s")
print("===================================")
print("AI PERFORMANCE:")
print(f"  TIMEOUT: {AI_TIMEOUT_SECONDS}s")
print(f"  MAX RETRIES: {AI_MAX_RETRIES}")
print(f"  RETRY DELAY: {AI_RETRY_DELAY_SECONDS}s")
print("===================================")
print("AI CONFIGURATION:")
print(f"  AI_ANALYSIS_ENABLED: {AI_ANALYSIS_ENABLED}")
print(f"  ENABLE_GROQ: {ENABLE_GROQ}")
print(f"  ENABLE_DEEPSEEK_LOGISTICS: {ENABLE_DEEPSEEK_LOGISTICS}")
print(f"  AI_FALLBACK_TO_RULE_BASED: {AI_FALLBACK_TO_RULE_BASED}")
print("===================================")

# Warnings
if not AI_ANALYSIS_ENABLED:
    print("⚠️  WARNING: AI_ANALYSIS_ENABLED = False")
    print("   AI features are disabled. Using rule-based responses only.")
    print("   Set AI_ANALYSIS_ENABLED=True in .env to enable AI.")
    print("===================================")

if AI_ANALYSIS_ENABLED and len(config.AVAILABLE_PROVIDERS) == 0:
    print("⚠️  WARNING: AI_ANALYSIS_ENABLED = True but no API keys configured")
    print("   Please add at least one API key (GROQ_API_KEY recommended)")
    print("===================================")

if AI_PROVIDER == "groq" and not GROQ_API_KEY:
    print("⚠️  WARNING: Primary provider 'groq' is not configured")
    print("   GROQ_API_KEY is missing!")
    print("   Get your API key from: https://console.groq.com")
    print("===================================")

if AI_PROVIDER not in config.AVAILABLE_PROVIDERS and config.AVAILABLE_PROVIDERS:
    print(f"⚠️  WARNING: Primary provider '{AI_PROVIDER}' is not configured")
    print(f"   Available providers: {config.AVAILABLE_PROVIDERS}")
    print(f"   Will fallback to: {AI_FALLBACK_PROVIDER}")
    print("===================================")

print("✅ GROQ CONFIGURATION LOADED SUCCESSFULLY")
print("   WhatsApp responses will use GROQ for fast AI replies")
print("===================================")

# ==========================================================
# END FILE
# ==========================================================
