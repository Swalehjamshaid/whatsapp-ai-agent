# ==========================================================
# FILE: app/services/schema_service.py
# ==========================================================

import os
import logging
from sqlalchemy.orm import Session
from sqlalchemy import inspect

from app.database import engine
from app.models import Base, SystemSetting


logger = logging.getLogger(__name__)


# ==========================================================
# CONSTANTS
# ==========================================================

APP_SCHEMA_VERSION = "2.0.0"


# ==========================================================
# RESET DATABASE
# ==========================================================

def reset_database():
    """
    Drop all existing tables and recreate them.
    USE WITH CAUTION.
    """

    logger.warning("Starting database reset...")

    Base.metadata.drop_all(bind=engine)

    logger.warning("All tables dropped.")

    Base.metadata.create_all(bind=engine)

    logger.warning("All tables recreated.")


# ==========================================================
# GET SETTING
# ==========================================================

def get_setting(
    db: Session,
    key: str
):
    return (
        db.query(SystemSetting)
        .filter(SystemSetting.key == key)
        .first()
    )


# ==========================================================
# CREATE / UPDATE SETTING
# ==========================================================

def set_setting(
    db: Session,
    key: str,
    value: str,
    description: str = None
):

    setting = get_setting(
        db,
        key
    )

    if setting:

        setting.value = value

        if description:
            setting.description = description

    else:

        setting = SystemSetting(
            key=key,
            value=value,
            description=description
        )

        db.add(setting)

    db.commit()


# ==========================================================
# CHECK SCHEMA VERSION
# ==========================================================

def check_schema_version(
    db: Session
):
    """
    Compare database schema version
    against Railway environment variable.

    If version changed AND
    ALLOW_DB_RESET=true

    Then:
        Drop tables
        Recreate tables
        Update schema version
    """

    app_version = os.getenv(
        "SCHEMA_VERSION",
        "1.0"
    )

    allow_reset = os.getenv(
        "ALLOW_DB_RESET",
        "false"
    ).lower()

    logger.info(
        f"App Schema Version: {app_version}"
    )

    version_record = get_setting(
        db,
        "schema_version"
    )

    # ======================================================
    # FIRST RUN
    # ======================================================

    if not version_record:

        logger.info(
            "No schema version found."
        )

        set_setting(
            db=db,
            key="schema_version",
            value=app_version,
            description="Current database schema version"
        )

        logger.info(
            f"Schema version initialized: {app_version}"
        )

        return

    db_version = version_record.value

    logger.info(
        f"Database Version: {db_version}"
    )

    # ======================================================
    # VERSION MATCH
    # ======================================================

    if db_version == app_version:

        logger.info(
            "Schema version matches."
        )

        return

    # ======================================================
    # VERSION MISMATCH
    # ======================================================

    logger.warning(
        f"Schema mismatch detected "
        f"({db_version} -> {app_version})"
    )

    if allow_reset != "true":

        logger.warning(
            "ALLOW_DB_RESET is FALSE."
        )

        logger.warning(
            "Database reset skipped."
        )

        return

    # ======================================================
    # RESET DATABASE
    # ======================================================

    logger.warning(
        "Database reset approved."
    )

    reset_database()

    # ======================================================
    # SAVE NEW VERSION
    # ======================================================

    new_record = get_setting(
        db,
        "schema_version"
    )

    if new_record:

        new_record.value = app_version

    else:

        db.add(
            SystemSetting(
                key="schema_version",
                value=app_version,
                description="Current database schema version"
            )
        )

    db.commit()

    logger.warning(
        f"Schema updated to {app_version}"
    )


# ==========================================================
# GET SCHEMA INFO
# ==========================================================

def get_schema_info(
    db: Session
) -> dict:
    """
    Get detailed schema information for status endpoints.
    Returns a dictionary with current schema status.
    """

    inspector = inspect(engine)

    # Get current version from database
    version_record = get_setting(
        db,
        "schema_version"
    )

    db_version = version_record.value if version_record else "0.0.0"

    app_version = os.getenv(
        "SCHEMA_VERSION",
        "1.0"
    )

    allow_reset = os.getenv(
        "ALLOW_DB_RESET",
        "false"
    ).lower()

    needs_migration = app_version != db_version

    return {
        "app_version": app_version,
        "db_version": db_version,
        "needs_migration": needs_migration,
        "allow_reset": allow_reset == "true",
        "tables": inspector.get_table_names(),
        "table_count": len(inspector.get_table_names())
    }
