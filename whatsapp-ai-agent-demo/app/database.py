# ==========================================================
# FILE: app/database.py (IMPROVED v3.1 - SPEED OPTIMIZED)
# ==========================================================
# PURPOSE: Database Connection Management - Pure Database Layer
#
# ARCHITECTURE:
# Webhook → AIQueryService → Services → THIS FILE → Database
#
# RESPONSIBILITIES (ONLY):
# - Database Engine Configuration
# - Session Management
# - Connection Pool Management
# - Health Checks
# - Table Creation
#
# WHAT THIS FILE DOES NOT CONTAIN:
# - No Business Logic
# - No AI Logic
# - No WhatsApp Logic
# - No Analytics Logic
# - No KPI Logic
# ==========================================================

import warnings
from typing import Dict, Any, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from loguru import logger

from app.config import config

# ==========================================================
# SUPPRESS SQLAlchemy WARNINGS (for models.py help_text)
# ==========================================================

warnings.filterwarnings('ignore', message="Can't validate argument 'help_text'")
warnings.filterwarnings('ignore', category=DeprecationWarning, module='sqlalchemy')

# ==========================================================
# DATABASE URL VALIDATION (Critical Fix)
# ==========================================================

DATABASE_URL = config.DATABASE_URL

# Validate database URL at startup
if not DATABASE_URL:
    error_msg = "DATABASE_URL is not configured. Please set DATABASE_URL in environment variables."
    logger.error(error_msg)
    raise ValueError(error_msg)

logger.info(f"Database URL configured (type: {DATABASE_URL.split('://')[0] if '://' in DATABASE_URL else 'unknown'})")

# ==========================================================
# ENGINE CONFIGURATION (SPEED OPTIMIZED)
# ==========================================================

# Determine if using PostgreSQL (Railway default) or SQLite (local development)
is_postgres = DATABASE_URL.startswith(('postgresql://', 'postgres://'))

# SPEED OPTIMIZATION v3.1: Increased pool size for faster response times
# Railway optimized settings - increased for better concurrency
engine_config = {
    "pool_pre_ping": True,           # Verify connections before using (prevents stale connections)
    "pool_recycle": 3600,            # Recycle connections every 60 minutes (increased from 30)
    "pool_timeout": 15,              # Wait 15 seconds for connection from pool (reduced from 30)
    "echo": False,                   # Disable SQL logging in production
    "future": True,                  # SQLAlchemy 2.0 style
}

# Different pool settings for PostgreSQL vs SQLite
if is_postgres:
    # SPEED OPTIMIZATION: Increased pool size for concurrent requests
    engine_config["pool_size"] = 10        # Increased from 5 for better concurrency
    engine_config["max_overflow"] = 20     # Increased from 10 for burst handling
    engine_config["pool_use_lifo"] = True  # LIFO: reuse most recent connections (faster)
    logger.info(f"PostgreSQL detected - Pool size: {engine_config['pool_size']}, Max overflow: {engine_config['max_overflow']}")
else:
    # SQLite doesn't need connection pooling
    engine_config["pool_size"] = 1
    engine_config["max_overflow"] = 0
    engine_config["connect_args"] = {"check_same_thread": False}
    logger.info("SQLite detected - Using single connection")

# SPEED OPTIMIZATION: Add execution timeout for slow queries (PostgreSQL only)
if is_postgres:
    # Set statement timeout to prevent long-running queries
    engine_config["execution_options"] = {
        "statement_timeout": 5000  # 5 second timeout for all queries
    }
    logger.info("Query timeout set to 5 seconds")

# Create engine
try:
    engine = create_engine(DATABASE_URL, **engine_config)
    logger.info("Database engine created successfully")
except Exception as e:
    logger.error(f"Failed to create database engine: {e}")
    raise

# ==========================================================
# SESSION FACTORY (Optimized for FastAPI)
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
# CONNECTION POOL MONITORING
# ==========================================================

def get_pool_status() -> Dict[str, Any]:
    """
    Get current connection pool status for monitoring.
    
    Returns:
        Dictionary with pool statistics
    """
    pool = engine.pool
    return {
        "size": pool.size(),
        "checked_in": pool.checkedin(),
        "overflow": pool.overflow(),
        "total": pool.total(),
        "max_connections": engine_config.get("pool_size", 0) + engine_config.get("max_overflow", 0)
    }

# ==========================================================
# DATABASE DEPENDENCY (FastAPI)
# ==========================================================

def get_db() -> Session:
    """
    FastAPI dependency for database session.
    
    Usage:
        @app.get("/")
        def endpoint(db: Session = Depends(get_db)):
            ...
    
    Yields:
        Database session for request
    """
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.exception(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()

# ==========================================================
# HEALTH CHECK (Improved with SQLAlchemy 2.x compatibility)
# ==========================================================

def check_database_connection() -> bool:
    """
    Simple database connection health check.
    
    Returns:
        True if connected, False otherwise
    """
    try:
        db = SessionLocal()
        # Use text() for SQLAlchemy 2.x compatibility (Critical Fix)
        db.execute(text("SELECT 1"))
        db.close()
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False


def get_database_health() -> Dict[str, Any]:
    """
    Detailed database health check with metadata (Priority 4).
    
    Returns:
        Dictionary with health status and metadata
    """
    health_status = {
        "connected": False,
        "database_type": "postgresql" if is_postgres else "sqlite",
        "pool_size": engine_config.get("pool_size", 0),
        "max_overflow": engine_config.get("max_overflow", 0),
        "url_configured": bool(DATABASE_URL),
        "error": None,
        "current_pool_status": None
    }
    
    try:
        db = SessionLocal()
        # Use text() for SQLAlchemy 2.x compatibility
        result = db.execute(text("SELECT 1 as connected, version() as version")).first()
        db.close()
        
        health_status["connected"] = True
        if result:
            health_status["version"] = str(result[1]) if len(result) > 1 else "unknown"
        
        # Get current pool status
        health_status["current_pool_status"] = get_pool_status()
        
        logger.debug("Database health check passed")
        
    except Exception as e:
        health_status["error"] = str(e)
        logger.error(f"Database health check failed: {e}")
    
    return health_status


def check_database_connection_detailed() -> Dict[str, Any]:
    """
    Alias for get_database_health (backward compatibility).
    """
    return get_database_health()

# ==========================================================
# QUERY OPTIMIZATION HELPERS
# ==========================================================

def set_query_timeout(seconds: int = 5):
    """
    Set statement timeout for the current session.
    
    Args:
        seconds: Timeout in seconds (default 5)
    """
    if is_postgres:
        try:
            db = SessionLocal()
            db.execute(text(f"SET statement_timeout = '{seconds}s'"))
            db.commit()
            db.close()
            logger.debug(f"Query timeout set to {seconds} seconds")
        except Exception as e:
            logger.warning(f"Failed to set query timeout: {e}")


def get_query_performance_stats() -> Dict[str, Any]:
    """
    Get query performance statistics from PostgreSQL.
    
    Returns:
        Dictionary with performance metrics
    """
    if not is_postgres:
        return {"message": "Query performance stats only available for PostgreSQL"}
    
    try:
        db = SessionLocal()
        # Get slow queries
        slow_queries = db.execute(text("""
            SELECT query, calls, total_time, mean_time 
            FROM pg_stat_statements 
            ORDER BY mean_time DESC 
            LIMIT 10
        """)).fetchall()
        db.close()
        
        return {
            "slow_queries": [{"query": q[0][:100], "calls": q[1], "total_time_ms": q[2], "avg_time_ms": q[3]} for q in slow_queries]
        }
    except Exception as e:
        return {"error": str(e)}


# ==========================================================
# STARTUP DIAGNOSTICS (Priority 5)
# ==========================================================

def database_info() -> Dict[str, Any]:
    """
    Database startup diagnostics for monitoring.
    
    Returns:
        Dictionary with database configuration information
    """
    return {
        "database_url_exists": bool(DATABASE_URL),
        "database_type": "postgresql" if is_postgres else "sqlite",
        "engine_ready": engine is not None,
        "pool_size": engine_config.get("pool_size", 0),
        "max_overflow": engine_config.get("max_overflow", 0),
        "pool_recycle": engine_config.get("pool_recycle", 0),
        "pool_timeout": engine_config.get("pool_timeout", 0),
        "pool_pre_ping": engine_config.get("pool_pre_ping", False),
        "pool_use_lifo": engine_config.get("pool_use_lifo", False),
        "future_mode": engine_config.get("future", False),
        "database_connected": check_database_connection(),
        "query_timeout_ms": engine_config.get("execution_options", {}).get("statement_timeout", "Not set")
    }


def validate_database_setup() -> bool:
    """
    Validate complete database setup at startup.
    
    Returns:
        True if setup is valid, False otherwise
    """
    logger.info("Validating database setup...")
    
    info = database_info()
    
    if not info["database_url_exists"]:
        logger.error("❌ DATABASE_URL not configured")
        return False
    
    if not info["engine_ready"]:
        logger.error("❌ Database engine not ready")
        return False
    
    if not info["database_connected"]:
        logger.error("❌ Cannot connect to database")
        return False
    
    logger.info(f"✅ Database setup validated (Type: {info['database_type']}, Pool: {info['pool_size']}/{info['max_overflow']}, Query Timeout: {info['query_timeout_ms']}ms)")
    return True

# ==========================================================
# TABLE CREATION (Pure - No Business Logic)
# ==========================================================

def create_tables() -> None:
    """
    Create all database tables based on models.
    Called during application startup.
    """
    try:
        # Import models inside function to avoid circular imports
        import app.models
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created/verified successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise


def drop_tables() -> None:
    """
    Drop all database tables (development only!).
    WARNING: This will delete all data!
    """
    if config.ENVIRONMENT == "production":
        logger.warning("drop_tables() called in production - operation blocked")
        return
    
    try:
        import app.models
        Base.metadata.drop_all(bind=engine)
        logger.warning("Database tables dropped successfully")
    except Exception as e:
        logger.error(f"Failed to drop database tables: {e}")
        raise

# ==========================================================
# CONNECTION POOL RESET (For maintenance)
# ==========================================================

def reset_connection_pool() -> Dict[str, Any]:
    """
    Reset the connection pool (useful for maintenance).
    
    Returns:
        Status of the reset operation
    """
    try:
        engine.dispose()
        logger.info("Connection pool disposed and reset")
        return {"success": True, "message": "Connection pool reset successfully"}
    except Exception as e:
        logger.error(f"Failed to reset connection pool: {e}")
        return {"success": False, "error": str(e)}

# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 60)
logger.info("🗄️ DATABASE SERVICE v3.1 - SPEED OPTIMIZED")
logger.info(f"   Type: {'PostgreSQL' if is_postgres else 'SQLite'}")
logger.info(f"   Pool Size: {engine_config.get('pool_size', 'N/A')}")
logger.info(f"   Max Overflow: {engine_config.get('max_overflow', 'N/A')}")
logger.info(f"   Pool Recycle: {engine_config.get('pool_recycle', 'N/A')}s")
logger.info(f"   Pool LIFO: {engine_config.get('pool_use_lifo', False)}")
logger.info(f"   Query Timeout: {engine_config.get('execution_options', {}).get('statement_timeout', 'Not set')}ms")
logger.info(f"   SQLAlchemy Future Mode: {engine_config.get('future', False)}")
logger.info("=" * 60)

# Auto-validate on import (optional - can be disabled in production)
if config.ENVIRONMENT != "production":
    validate_database_setup()
