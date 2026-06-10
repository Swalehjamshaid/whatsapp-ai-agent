# ==========================================================
# FILE: app/services/schema_service.py
# VERSION: 2.0.0
# PURPOSE: Schema Management & Version Control
# ARCHITECTURE: Main Application → Schema Service (Version & Health Only)
# ==========================================================

from typing import Dict, Any, Optional
from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import text

# ==========================================================
# CONSTANTS
# ==========================================================

# Application Schema Version
APP_SCHEMA_VERSION = "2.0.0"

# Schema Metadata
SCHEMA_NAME = "WhatsApp Logistics AI"
SCHEMA_STATUS = "ACTIVE"
SCHEMA_AUTHOR = "HNR Logistics"

# Database Version Management
DATABASE_VERSION = "1.0"
MIN_SUPPORTED_VERSION = "1.0"
MAX_SUPPORTED_VERSION = "2.0"

# Service Metadata
SERVICE_NAME = "Schema Service"
SERVICE_VERSION = "2.0.0"

# Timestamps
SCHEMA_LAST_UPDATED = "2026-06-10"

# ==========================================================
# HELPER FUNCTIONS
# ==========================================================

def check_database_connection(db: Session) -> bool:
    """
    Check if database connection is working
    
    Args:
        db: SQLAlchemy database session
    
    Returns:
        True if connected, False otherwise
    """
    try:
        # Execute a simple query to test connection
        db.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database connection check failed: {e}")
        return False


def check_table_exists(db: Session, table_name: str) -> bool:
    """
    Check if a specific table exists in the database
    
    Args:
        db: SQLAlchemy database session
        table_name: Name of the table to check
    
    Returns:
        True if table exists, False otherwise
    """
    try:
        result = db.execute(
            text("""
                SELECT EXISTS (
                    SELECT 1 
                    FROM information_schema.tables 
                    WHERE table_name = :table_name
                )
            """),
            {"table_name": table_name}
        ).scalar()
        return bool(result)
    except Exception as e:
        logger.error(f"Table check failed for {table_name}: {e}")
        return False


def get_current_database_version(db: Session) -> Optional[str]:
    """
    Get current database schema version
    
    Args:
        db: SQLAlchemy database session
    
    Returns:
        Database version string or None if not found
    """
    try:
        # Check if schema_version table exists
        if not check_table_exists(db, "schema_version"):
            return None
        
        result = db.execute(
            text("SELECT version FROM schema_version ORDER BY id DESC LIMIT 1")
        ).scalar()
        
        return result if result else None
    except Exception as e:
        logger.error(f"Failed to get database version: {e}")
        return None


def needs_migration(db: Session) -> bool:
    """
    Check if database schema needs migration
    
    Args:
        db: SQLAlchemy database session
    
    Returns:
        True if migration needed, False otherwise
    """
    try:
        db_version = get_current_database_version(db)
        
        if db_version is None:
            # No version table exists, likely new database
            return False
        
        # Check if current version is supported
        if db_version < MIN_SUPPORTED_VERSION:
            return True
        
        if db_version > MAX_SUPPORTED_VERSION:
            return True
        
        return db_version != APP_SCHEMA_VERSION
        
    except Exception as e:
        logger.error(f"Migration check failed: {e}")
        return False


# ==========================================================
# PUBLIC FUNCTIONS (Required by main.py)
# ==========================================================

def check_schema_version(db: Session) -> bool:
    """
    Verify schema version and validate database schema
    
    Purpose:
        - Verify schema version matches application version
        - Validate database schema integrity
        - Prevent version mismatch errors
    
    Args:
        db: SQLAlchemy database session
    
    Returns:
        True if schema is valid and compatible, False otherwise
    """
    logger.info("Checking schema version...")
    
    try:
        # Check database connection
        if not check_database_connection(db):
            logger.error("Database connection failed")
            return False
        
        # Check if migration is needed
        if needs_migration(db):
            logger.warning(f"Schema migration needed. App version: {APP_SCHEMA_VERSION}")
            return False
        
        # Log success
        logger.success(f"Schema version verified: {APP_SCHEMA_VERSION}")
        return True
        
    except Exception as e:
        logger.error(f"Schema version check failed: {e}")
        return False


def get_schema_info(db: Optional[Session] = None) -> Dict[str, Any]:
    """
    Return schema metadata and system information
    
    Purpose:
        - Return schema metadata for monitoring
        - Provide version information
        - Report system health status
    
    Args:
        db: Optional SQLAlchemy database session
    
    Returns:
        Dictionary with schema metadata and status
    """
    
    # Check database connection status
    database_connected = False
    if db is not None:
        database_connected = check_database_connection(db)
    
    # Check if migration is needed
    migration_needed = False
    if db is not None:
        migration_needed = needs_migration(db)
    
    return {
        "app_version": APP_SCHEMA_VERSION,
        "schema_version": APP_SCHEMA_VERSION,
        "schema_name": SCHEMA_NAME,
        "status": SCHEMA_STATUS,
        "needs_migration": migration_needed,
        "database_connected": database_connected,
        "last_updated": SCHEMA_LAST_UPDATED,
        "author": SCHEMA_AUTHOR,
        "database_version": get_current_database_version(db) if db else None,
        "min_supported_version": MIN_SUPPORTED_VERSION,
        "max_supported_version": MAX_SUPPORTED_VERSION
    }


def health_check() -> Dict[str, Any]:
    """
    Health monitoring endpoint for schema service
    
    Purpose:
        - Provide health status for monitoring systems
        - Used by Railway health endpoints
        - Quick service availability check
    
    Returns:
        Dictionary with service health status
    """
    return {
        "service": SERVICE_NAME,
        "status": "healthy",
        "version": SERVICE_VERSION,
        "schema_version": APP_SCHEMA_VERSION,
        "schema_status": SCHEMA_STATUS,
        "timestamp": datetime.utcnow().isoformat(),
        "service_version": SERVICE_VERSION,
        "min_supported": MIN_SUPPORTED_VERSION,
        "max_supported": MAX_SUPPORTED_VERSION
    }


# ==========================================================
# OPTIONAL UTILITY FUNCTIONS
# ==========================================================

def get_version_info() -> Dict[str, str]:
    """
    Get version information without database dependency
    
    Returns:
        Dictionary with version information
    """
    return {
        "app_schema_version": APP_SCHEMA_VERSION,
        "schema_name": SCHEMA_NAME,
        "schema_status": SCHEMA_STATUS,
        "database_version": DATABASE_VERSION,
        "min_supported": MIN_SUPPORTED_VERSION,
        "max_supported": MAX_SUPPORTED_VERSION
    }


def log_schema_status(db: Optional[Session] = None) -> None:
    """
    Log current schema status for debugging
    
    Args:
        db: Optional SQLAlchemy database session
    """
    logger.info("=" * 50)
    logger.info(f"Schema: {SCHEMA_NAME}")
    logger.info(f"Version: {APP_SCHEMA_VERSION}")
    logger.info(f"Status: {SCHEMA_STATUS}")
    logger.info(f"Author: {SCHEMA_AUTHOR}")
    
    if db:
        connected = check_database_connection(db)
        logger.info(f"Database Connected: {'✓' if connected else '✗'}")
        
        if connected:
            db_version = get_current_database_version(db)
            logger.info(f"Database Version: {db_version or 'Not set'}")
            logger.info(f"Migration Needed: {'✓' if needs_migration(db) else '✗'}")
    
    logger.info("=" * 50)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info(f"✅ {SERVICE_NAME} v{SERVICE_VERSION} loaded")
logger.info(f"   Schema: {SCHEMA_NAME} v{APP_SCHEMA_VERSION}")
logger.info(f"   Status: {SCHEMA_STATUS}")
logger.info(f"   Supports DB versions: {MIN_SUPPORTED_VERSION} - {MAX_SUPPORTED_VERSION}")
