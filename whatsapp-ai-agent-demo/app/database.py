# ==========================================================

# FILE: app/database.py

# PROJECT: AI WhatsApp Customer Service Agent

# ==========================================================

import os

from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

# ==========================================================

# DATABASE URL

# ==========================================================

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
raise RuntimeError(
"DATABASE_URL environment variable is missing"
)

# ==========================================================

# RAILWAY POSTGRES FIX

# ==========================================================

if DATABASE_URL.startswith("postgres://"):
DATABASE_URL = DATABASE_URL.replace(
"postgres://",
"postgresql://",
1
)

print("===================================")
print("DATABASE_URL EXISTS:", bool(DATABASE_URL))
print("===================================")

# =================================================
