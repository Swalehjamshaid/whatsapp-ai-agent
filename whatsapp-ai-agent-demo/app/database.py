# ==========================================================
# FILE: app/database.py
# PROJECT: AI WhatsApp Customer Service Agent Demo
# ==========================================================

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import declarative_base

# ==========================================================
# DATABASE CONFIGURATION
# ==========================================================

# For Demo Version
# Later replace with PostgreSQL Railway URL

import os

DATABASE_URL = os.getenv("DATABASE_URL")

# ==========================================================
# ENGINE
# ==========================================================

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# ==========================================================
# SESSION
# ==========================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# ==========================================================
# BASE
# ==========================================================

Base = declarative_base()

# ==========================================================
# DATABASE DEPENDENCY
# ==========================================================

def get_db():

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()
