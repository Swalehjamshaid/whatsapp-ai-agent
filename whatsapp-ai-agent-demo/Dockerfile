# ==========================================================
# WhatsApp AI Agent - Enterprise Dockerfile
# Optimized for Railway
# Python 3.12
# ==========================================================

FROM python:3.12-slim

# ==========================================================
# Environment
# ==========================================================

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120

# ==========================================================
# Working Directory
# ==========================================================

WORKDIR /app

# ==========================================================
# Install Linux Packages
# ==========================================================

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    g++ \
    libpq-dev \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ==========================================================
# Upgrade pip
# ==========================================================

RUN python -m pip install --upgrade pip setuptools wheel

# ==========================================================
# Copy only dependency files
# ==========================================================

COPY requirements/ ./requirements/

# ==========================================================
# Install Python dependencies
# ==========================================================

RUN pip install \
    --prefer-binary \
    --no-cache-dir \
    -r requirements/requirements-all.txt

# ==========================================================
# Copy application
# ==========================================================

COPY . .

# ==========================================================
# Expose Port
# ==========================================================

EXPOSE 8000

# ==========================================================
# Health Check
# ==========================================================

HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --start-period=30s \
    --retries=3 \
CMD curl -fs http://localhost:${PORT:-8000}/health || exit 1

# ==========================================================
# Start FastAPI
# ==========================================================

CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
