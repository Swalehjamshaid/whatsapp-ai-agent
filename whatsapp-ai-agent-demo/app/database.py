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

# ==========================================================

# ENGINE

# ==========================================================

engine = create_engine(
DATABASE_URL,
pool_pre_ping=True,
pool_recycle=300,
echo=True,
future=True
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

```
db = SessionLocal()

try:
    yield db

finally:
    db.close()
```

# ==========================================================

# DATABASE TEST

# ==========================================================

def test_connection():

```
try:

    with engine.connect() as conn:

        conn.execute(
            text("SELECT 1")
        )

        print(
            "✅ PostgreSQL Connected Successfully"
        )

    return True

except Exception as e:

    print(
        f"❌ Database Connection Failed: {e}"
    )

    return False
```

# ==========================================================

# END OF FILE

# ==========================================================
