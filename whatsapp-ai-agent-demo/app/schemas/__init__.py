# ==========================================================
# FILE: app/schemas/__init__.py
# PURPOSE: Mark schemas as Python package
# ==========================================================

from .schema_service import (
    SchemaService,
    get_schema_service,
    generate_metadata_report,
)

__all__ = [
    "SchemaService",
    "get_schema_service",
    "generate_metadata_report",
]
