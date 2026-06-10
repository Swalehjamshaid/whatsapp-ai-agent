# app/database.py

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm import Session

from app.config import config

# ==========================================================

# DATABASE URL

# ==========================================================

DATABASE_URL = config.DATABASE_URL

# ==========================================================

# ENGINE

# ==========================================================

engine = create_engine(
DATABASE_URL,
pool_pre_ping=True,
pool_recycle=300,
pool_size=10,
max_overflow=20,
echo=False
)

# ==========================================================

# SESSION FACTORY

# ==========================================================

SessionLocal = sessionmaker(
autocommit=False,
autoflush=False,
bind=engine
)

# ==========================================================

# BASE MODEL

# ==========================================================

Base = declarative_base()

# ==========================================================

# DATABASE DEPENDENCY

# ==========================================================

def get_db():
"""
FastAPI dependency

```
Usage:

db: Session = Depends(get_db)
"""

db = SessionLocal()

try:
    yield db

finally:
    db.close()
```

# ==========================================================

# HEALTH CHECK

# ==========================================================

def check_database_connection() -> bool:

```
try:

    db = SessionLocal()

    db.execute("SELECT 1")

    db.close()

    return True

except Exception:

    return False
```

# ==========================================================

# TABLE CREATION

# ==========================================================

def create_tables():

```
from app import models

Base.metadata.create_all(bind=engine)
```
