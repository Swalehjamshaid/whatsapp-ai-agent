#!/usr/bin/env python
"""
======================================================================================================
MIGRATION SCRIPT: Fix upload_batch_id column type
======================================================================================================
PURPOSE: Migrate upload_batch_id from INTEGER to VARCHAR(100)
VERSION: 1.0
DATE: 2026-06-27
======================================================================================================
This script:
1. Checks the current column type
2. Safely migrates from INTEGER to VARCHAR using a temporary column
3. Preserves all existing data
4. Can be run multiple times safely (idempotent)
======================================================================================================
"""

import os
import sys
import logging
import argparse
from datetime import datetime
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.engine import Engine

# ==================================================================================================
# LOGGING CONFIGURATION
# ==================================================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ==================================================================================================
# COLOR CODES FOR TERMINAL OUTPUT
# ==================================================================================================

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    END = '\033[0m'

def print_header(text: str):
    """Print formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.CYAN}{text}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.END}\n")

def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}✅ {text}{Colors.END}")

def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}❌ {text}{Colors.END}")

def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}⚠️ {text}{Colors.END}")

def print_info(text: str):
    """Print info message"""
    print(f"{Colors.CYAN}ℹ️ {text}{Colors.END}")

def print_step(step: int, total: int, text: str):
    """Print step progress"""
    print(f"{Colors.BOLD}Step {step}/{total}:{Colors.END} {text}")

# ==================================================================================================
# DATABASE CONNECTION
# ==================================================================================================

def get_database_url() -> str:
    """
    Get database URL from environment variables.
    Supports multiple environment variable names.
    
    Returns:
        str: Database URL
    
    Raises:
        SystemExit: If DATABASE_URL is not found
    """
    # Try multiple possible environment variable names
    env_vars = [
        'DATABASE_URL',
        'DATABASE_URL_RAILWAY',
        'DB_URL',
        'POSTGRES_URL'
    ]
    
    database_url = None
    for var in env_vars:
        url = os.getenv(var)
        if url:
            database_url = url
            logger.info(f"Using database URL from {var}")
            break
    
    # Try Railway-specific URL
    if not database_url:
        # Railway often uses POSTGRES_URL
        database_url = os.getenv('POSTGRES_URL')
        if database_url:
            logger.info("Using database URL from POSTGRES_URL")
    
    if not database_url:
        print_error("DATABASE_URL not found in environment variables")
        print_info("Please set DATABASE_URL in your environment")
        print_info("Example: export DATABASE_URL='postgresql://user:password@localhost:5432/dbname'")
        sys.exit(1)
    
    # Handle Railway's postgres:// vs postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
        logger.info("Converted postgres:// to postgresql://")
    
    # Handle Render.com format
    if database_url.startswith("postgresql://") and "@" not in database_url:
        # Might be missing credentials
        logger.warning("Database URL may be missing credentials")
    
    return database_url

def create_db_engine(database_url: str) -> Engine:
    """
    Create SQLAlchemy engine with proper configuration.
    
    Args:
        database_url: Database connection string
    
    Returns:
        Engine: SQLAlchemy engine
    """
    try:
        engine = create_engine(
            database_url,
            echo=False,
            pool_pre_ping=True,
            pool_recycle=3600,
            connect_args={
                "connect_timeout": 10,
                "application_name": "migration_upload_batch_id"
            }
        )
        
        # Test connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        
        print_success("Database connection established")
        return engine
        
    except Exception as e:
        print_error(f"Failed to create database engine: {e}")
        sys.exit(1)

# ==================================================================================================
# DATABASE FUNCTIONS
# ==================================================================================================

def table_exists(engine: Engine, table_name: str) -> bool:
    """
    Check if a table exists in the database.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
    
    Returns:
        bool: True if table exists
    """
    try:
        inspector = inspect(engine)
        return table_name in inspector.get_table_names()
    except Exception as e:
        logger.error(f"Failed to check table existence: {e}")
        return False

def column_exists(engine: Engine, table_name: str, column_name: str) -> bool:
    """
    Check if a column exists in a table.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
        column_name: Name of the column
    
    Returns:
        bool: True if column exists
    """
    try:
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception as e:
        logger.error(f"Failed to check column existence: {e}")
        return False

def get_column_type(engine: Engine, table_name: str, column_name: str) -> str:
    """
    Get the data type of a column.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
        column_name: Name of the column
    
    Returns:
        str: Column data type, or None if not found
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("""
                    SELECT data_type 
                    FROM information_schema.columns 
                    WHERE table_name = :table_name 
                      AND column_name = :column_name
                """),
                {"table_name": table_name, "column_name": column_name}
            )
            row = result.fetchone()
            return row[0].upper() if row else None
    except Exception as e:
        logger.error(f"Failed to get column type: {e}")
        return None

def get_table_row_count(engine: Engine, table_name: str) -> int:
    """
    Get the number of rows in a table.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
    
    Returns:
        int: Number of rows
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            )
            return result.scalar()
    except Exception as e:
        logger.error(f"Failed to get row count: {e}")
        return -1

def get_column_null_count(engine: Engine, table_name: str, column_name: str) -> int:
    """
    Get the number of NULL values in a column.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
        column_name: Name of the column
    
    Returns:
        int: Number of NULL values
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT COUNT(*) 
                    FROM {table_name} 
                    WHERE {column_name} IS NULL
                """)
            )
            return result.scalar()
    except Exception as e:
        logger.error(f"Failed to get NULL count: {e}")
        return -1

def get_column_sample_values(engine: Engine, table_name: str, column_name: str, limit: int = 5):
    """
    Get sample values from a column.
    
    Args:
        engine: SQLAlchemy engine
        table_name: Name of the table
        column_name: Name of the column
        limit: Number of samples to fetch
    
    Returns:
        list: Sample values
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT {column_name} 
                    FROM {table_name} 
                    WHERE {column_name} IS NOT NULL 
                    LIMIT {limit}
                """)
            )
            return [row[0] for row in result.fetchall()]
    except Exception as e:
        logger.error(f"Failed to get sample values: {e}")
        return []

# ==================================================================================================
# MIGRATION FUNCTION
# ==================================================================================================

def migrate_upload_batch_id(engine: Engine, dry_run: bool = False) -> bool:
    """
    Migrate upload_batch_id column from INTEGER to VARCHAR(100).
    
    Args:
        engine: SQLAlchemy engine
        dry_run: If True, only check without making changes
    
    Returns:
        bool: True if migration successful
    """
    table_name = "delivery_reports"
    column_name = "upload_batch_id"
    
    print_header("📊 UPLOAD_BATCH_ID MIGRATION")
    
    if dry_run:
        print_info("DRY RUN MODE - No changes will be made")
    
    # ==============================================================================================
    # Step 1: Check if table exists
    # ==============================================================================================
    print_step(1, 7, "Checking if table exists")
    
    if not table_exists(engine, table_name):
        print_error(f"Table '{table_name}' does not exist")
        return False
    
    print_success(f"Table '{table_name}' exists")
    
    # ==============================================================================================
    # Step 2: Check if column exists
    # ==============================================================================================
    print_step(2, 7, "Checking if column exists")
    
    if not column_exists(engine, table_name, column_name):
        print_warning(f"Column '{column_name}' does not exist")
        print_info(f"Creating {column_name} column...")
        
        if not dry_run:
            try:
                with engine.connect() as conn:
                    with conn.begin():
                        conn.execute(
                            text(f"""
                                ALTER TABLE {table_name} 
                                ADD COLUMN {column_name} VARCHAR(100)
                            """)
                        )
                print_success(f"Created column '{column_name}'")
            except Exception as e:
                print_error(f"Failed to create column: {e}")
                return False
        else:
            print_info("[DRY RUN] Would create column")
            return True
    
    print_success(f"Column '{column_name}' exists")
    
    # ==============================================================================================
    # Step 3: Check current column type
    # ==============================================================================================
    print_step(3, 7, "Checking current column type")
    
    current_type = get_column_type(engine, table_name, column_name)
    
    if current_type is None:
        print_error(f"Could not determine type of '{column_name}'")
        return False
    
    print_info(f"Current type: {current_type}")
    
    if current_type.upper() == 'VARCHAR':
        print_success("Column is already VARCHAR - No migration needed")
        
        # Check if length is sufficient
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    text(f"""
                        SELECT character_maximum_length 
                        FROM information_schema.columns 
                        WHERE table_name = :table_name 
                          AND column_name = :column_name
                    """),
                    {"table_name": table_name, "column_name": column_name}
                )
                max_length = result.scalar()
                if max_length and max_length < 100:
                    print_warning(f"Column length is {max_length}, increasing to 100")
                    if not dry_run:
                        with engine.connect() as conn:
                            with conn.begin():
                                conn.execute(
                                    text(f"""
                                        ALTER TABLE {table_name} 
                                        ALTER COLUMN {column_name} TYPE VARCHAR(100)
                                    """)
                                )
                        print_success("Column length increased to 100")
        except Exception as e:
            logger.warning(f"Could not check column length: {e}")
        
        return True
    
    # ==============================================================================================
    # Step 4: Get row counts
    # ==============================================================================================
    print_step(4, 7, "Analyzing data")
    
    total_rows = get_table_row_count(engine, table_name)
    null_count = get_column_null_count(engine, table_name, column_name)
    
    if total_rows < 0:
        print_error("Could not get row count")
        return False
    
    print_info(f"Total rows: {total_rows}")
    print_info(f"Rows with NULL: {null_count}")
    
    if total_rows > 0:
        sample_values = get_column_sample_values(engine, table_name, column_name)
        if sample_values:
            print_info(f"Sample values: {sample_values[:3]}")
    
    if dry_run:
        print_info("[DRY RUN] Would migrate column")
        return True
    
    # ==============================================================================================
    # Step 5: Begin migration
    # ==============================================================================================
    print_step(5, 7, "Starting migration")
    
    temp_column = f"{column_name}_temp"
    
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Check if temp column exists
                if column_exists(engine, table_name, temp_column):
                    print_warning(f"Temporary column '{temp_column}' already exists")
                    
                    # Check if it has data
                    temp_count = get_table_row_count(engine, table_name)
                    if temp_count > 0:
                        print_info("Temporary column has data, dropping it...")
                        conn.execute(
                            text(f"ALTER TABLE {table_name} DROP COLUMN {temp_column}")
                        )
                        print_success("Dropped temporary column")
                
                # Step 5a: Add temporary column
                print_info(f"Adding temporary column '{temp_column}'...")
                conn.execute(
                    text(f"""
                        ALTER TABLE {table_name} 
                        ADD COLUMN {temp_column} VARCHAR(100)
                    """)
                )
                print_success(f"Added temporary column '{temp_column}'")
                
                # Step 5b: Copy data from old to new
                print_info("Copying data to temporary column...")
                conn.execute(
                    text(f"""
                        UPDATE {table_name} 
                        SET {temp_column} = {column_name}::VARCHAR
                    """)
                )
                
                # Verify copy
                copy_result = conn.execute(
                    text(f"""
                        SELECT COUNT(*) 
                        FROM {table_name} 
                        WHERE {temp_column} IS NULL AND {column_name} IS NOT NULL
                    """)
                )
                missing_count = copy_result.scalar()
                
                if missing_count > 0:
                    print_error(f"Data copy failed: {missing_count} rows missing")
                    return False
                
                print_success("Data copied successfully")
                
                # Step 5c: Drop old column
                print_info(f"Dropping old column '{column_name}'...")
                conn.execute(
                    text(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
                )
                print_success(f"Dropped old column '{column_name}'")
                
                # Step 5d: Rename temp to original
                print_info(f"Renaming '{temp_column}' to '{column_name}'...")
                conn.execute(
                    text(f"""
                        ALTER TABLE {table_name} 
                        RENAME COLUMN {temp_column} TO {column_name}
                    """)
                )
                print_success(f"Renamed column to '{column_name}'")
                
        print_success("Migration completed successfully")
        
    except Exception as e:
        print_error(f"Migration failed: {e}")
        logger.error(f"Full error: {e}", exc_info=True)
        return False
    
    # ==============================================================================================
    # Step 6: Verify migration
    # ==============================================================================================
    print_step(6, 7, "Verifying migration")
    
    try:
        # Check new type
        new_type = get_column_type(engine, table_name, column_name)
        
        if new_type is None:
            print_warning("Could not verify new column type")
            return False
        
        print_info(f"New type: {new_type}")
        
        if new_type.upper() != 'VARCHAR':
            print_error(f"Migration failed: Column is {new_type}, expected VARCHAR")
            return False
        
        # Get sample values
        sample = get_column_sample_values(engine, table_name, column_name)
        if sample:
            print_info(f"Sample values after migration: {sample[:3]}")
        
        print_success("Verification passed")
        
    except Exception as e:
        print_error(f"Verification failed: {e}")
        return False
    
    # ==============================================================================================
    # Step 7: Final report
    # ==============================================================================================
    print_step(7, 7, "Final report")
    
    print_info(f"Table: {table_name}")
    print_info(f"Column: {column_name}")
    print_info(f"New Type: VARCHAR(100)")
    
    # Get final row count
    final_count = get_table_row_count(engine, table_name)
    print_info(f"Total rows: {final_count}")
    
    # Create index if it doesn't exist
    try:
        with engine.connect() as conn:
            # Check if index exists
            index_result = conn.execute(
                text("""
                    SELECT indexname 
                    FROM pg_indexes 
                    WHERE tablename = :table_name 
                      AND indexname = :index_name
                """),
                {"table_name": table_name, "index_name": f"idx_{table_name}_{column_name}"}
            )
            index_exists = index_result.fetchone() is not None
            
            if not index_exists and not dry_run:
                print_info("Creating index on upload_batch_id...")
                conn.execute(
                    text(f"""
                        CREATE INDEX idx_{table_name}_{column_name} 
                        ON {table_name} ({column_name})
                    """)
                )
                print_success("Index created")
    except Exception as e:
        logger.warning(f"Could not create index: {e}")
    
    print_success("✅ Migration completed successfully!")
    return True

# ==================================================================================================
# ROLLBACK FUNCTION
# ==================================================================================================

def rollback_migration(engine: Engine, dry_run: bool = False) -> bool:
    """
    Rollback the migration (if needed).
    
    Args:
        engine: SQLAlchemy engine
        dry_run: If True, only check without making changes
    
    Returns:
        bool: True if rollback successful
    """
    print_header("↩️ ROLLBACK MIGRATION")
    
    if dry_run:
        print_info("DRY RUN MODE - No changes will be made")
    
    table_name = "delivery_reports"
    column_name = "upload_batch_id"
    
    print_info(f"Table: {table_name}")
    print_info(f"Column: {column_name}")
    print_warning("Rollback will convert VARCHAR back to INTEGER")
    print_warning("This may cause data loss if values are not numeric")
    
    # Ask for confirmation
    if not dry_run:
        response = input("Continue with rollback? (yes/no): ")
        if response.lower() not in ['yes', 'y']:
            print_info("Rollback cancelled")
            return False
    
    try:
        with engine.connect() as conn:
            with conn.begin():
                # Add temp column as integer
                temp_column = f"{column_name}_int"
                
                print_info(f"Adding temporary integer column...")
                conn.execute(
                    text(f"""
                        ALTER TABLE {table_name} 
                        ADD COLUMN {temp_column} INTEGER
                    """)
                )
                
                # Try to convert data
                print_info("Converting data to integer...")
                conn.execute(
                    text(f"""
                        UPDATE {table_name} 
                        SET {temp_column} = {column_name}::INTEGER 
                        WHERE {column_name} ~ '^[0-9]+$'
                    """)
                )
                
                # Drop old column
                print_info(f"Dropping VARCHAR column...")
                conn.execute(
                    text(f"ALTER TABLE {table_name} DROP COLUMN {column_name}")
                )
                
                # Rename temp to original
                print_info(f"Renaming column...")
                conn.execute(
                    text(f"""
                        ALTER TABLE {table_name} 
                        RENAME COLUMN {temp_column} TO {column_name}
                    """)
                )
        
        print_success("Rollback completed")
        return True
        
    except Exception as e:
        print_error(f"Rollback failed: {e}")
        return False

# ==================================================================================================
# MAIN FUNCTION
# ==================================================================================================

def main():
    """Main entry point for the migration script."""
    
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Migrate upload_batch_id column from INTEGER to VARCHAR(100)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python migrate_upload_batch_id.py              # Run migration
  python migrate_upload_batch_id.py --dry-run    # Check without making changes
  python migrate_upload_batch_id.py --rollback   # Rollback to INTEGER
  python migrate_upload_batch_id.py --verbose    # Show detailed logs
        """
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Check migration without making changes'
    )
    parser.add_argument(
        '--rollback',
        action='store_true',
        help='Rollback migration (convert back to INTEGER)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Show detailed debug logs'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompts'
    )
    
    args = parser.parse_args()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Print banner
    print_header("🔧 UPLOAD_BATCH_ID MIGRATION TOOL v1.0")
    print_info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    if args.dry_run:
        print_info("Mode: DRY RUN (no changes)")
    elif args.rollback:
        print_info("Mode: ROLLBACK")
    else:
        print_info("Mode: MIGRATION")
    
    # Get database URL
    print_info("Loading database configuration...")
    database_url = get_database_url()
    
    # Create engine
    engine = create_db_engine(database_url)
    
    # Check if migration should be rolled back
    if args.rollback:
        print_warning("⚠️  ROLLBACK WILL CONVERT VARCHAR BACK TO INTEGER")
        print_warning("⚠️  This may cause data loss for non-numeric values")
        
        if not args.force and not args.dry_run:
            response = input("Continue with rollback? (yes/no): ")
            if response.lower() not in ['yes', 'y']:
                print_info("Rollback cancelled")
                sys.exit(0)
        
        success = rollback_migration(engine, args.dry_run)
    else:
        # Run migration
        success = migrate_upload_batch_id(engine, args.dry_run)
    
    # Print final status
    print_header("📊 MIGRATION STATUS")
    
    if success:
        if args.dry_run:
            print_success("Dry run completed - Migration would be successful")
        else:
            print_success("Migration completed successfully")
            print_info("You can now restart your application")
    else:
        print_error("Migration failed")
        print_info("Check the logs above for error details")
        sys.exit(1)

# ==================================================================================================
# ENTRY POINT
# ==================================================================================================

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Migration interrupted by user")
        sys.exit(130)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        logger.error(f"Unexpected error: {e}", exc_info=True)
        sys.exit(1)
