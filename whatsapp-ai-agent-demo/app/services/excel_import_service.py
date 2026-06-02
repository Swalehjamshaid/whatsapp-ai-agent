# ==========================================================
# FILE: app/services/excel_import_service.py
# ==========================================================

import os
import pandas as pd
from datetime import datetime, date
from sqlalchemy.orm import Session
from typing import Dict, List, Any, Tuple, Optional
import logging

from app.models import DeliveryReport

logger = logging.getLogger(__name__)


# ==========================================================
# EXCEL IMPORT SERVICE
# ==========================================================

class ExcelImportService:
    """
    Service for importing Excel delivery reports into the database.
    Handles file validation, data cleaning, and bulk import.
    """

    # Column name aliases (Excel variations -> Standard name)
    COLUMN_ALIASES = {
        "DN NO": "DN No",
        "DN NO.": "DN No",
        "DN Number": "DN No",
        "DN No": "DN No",
        "DN Work": "DN Work",
        "Material NO": "Material No",
        "Material Number": "Material No",
        "sales office": "Sales Office",
        "Sales Office": "Sales Office",
        "Ship-to City": "Ship To City",
        "Ship To City": "Ship To City",
        "Ship-to-City": "Ship To City",
        "storage": "Storage Location",
        "Storage Location": "Storage Location",
        "Storage": "Storage Location",
    }

    # Expected columns mapping (Standard name -> Database field)
    COLUMN_MAPPING = {
        "DN No": "dn_no",
        "DN Work": "dn_work",
        "Order Type": "order_type",
        "Division": "division",
        "Customer Code": "customer_code",
        "Dealer Code": "dealer_code",
        "Customer Name": "customer_name",
        "Customer Model": "customer_model",
        "Material No": "material_no",
        "Storage Location": "storage_location",
        "Sales Office": "sales_office",
        "Sales Manager": "sales_manager",
        "Ship To City": "ship_to_city",
        "Warehouse": "warehouse",
        "Warehouse Code": "warehouse_code",
        "DN Qty": "dn_qty",
        "DN Amount": "dn_amount",
        "DN Create Date": "dn_create_date",
        "Good Issue Date": "good_issue_date",
        "POD Date": "pod_date",
    }

    # Required columns (after normalization)
    REQUIRED_COLUMNS = [
        "DN No",
        "Customer Name",
        "DN Amount",
        "Ship To City"
    ]

    def __init__(self, db: Session):
        self.db = db

    # ==========================================================
    # MAIN IMPORT METHODS
    # ==========================================================

    def import_excel(
        self,
        file_path: str,
        source_filename: str,
        batch_id: int = None,
        skip_duplicates: bool = True,
        update_existing: bool = False
    ) -> Dict[str, Any]:
        """
        Import Excel file into delivery_reports table.
        
        Args:
            file_path: Path to the Excel file
            source_filename: Original filename for tracking
            batch_id: Optional batch ID for grouping multiple files
            skip_duplicates: Skip duplicate DN records (default: True)
            update_existing: Update existing records instead of skipping
        
        Returns:
            Dictionary with import statistics
        """
        try:
            # Read Excel file
            logger.info(f"Reading Excel file: {file_path}")
            df = pd.read_excel(file_path)
            
            # Normalize column names (apply aliases)
            df = self._normalize_columns(df)
            
            # Validate columns
            validation_result = self._validate_columns(df)
            if not validation_result["is_valid"]:
                return {
                    "success": False,
                    "error": validation_result["error"],
                    "missing_columns": validation_result.get("missing_columns", [])
                }
            
            # Clean and transform data
            records = self._transform_data(df, source_filename, batch_id)
            
            # Handle duplicates
            if skip_duplicates or update_existing:
                records = self._handle_duplicates(records, update_existing)
            
            # Bulk insert records
            inserted_count, updated_count, skipped_count = self._bulk_insert(
                records, 
                skip_duplicates=skip_duplicates,
                update_existing=update_existing
            )
            
            # Update derived fields for new/updated records
            if batch_id:
                self._update_derived_fields(batch_id)
            
            logger.info(f"Import complete: {inserted_count} inserted, {updated_count} updated, {skipped_count} skipped")
            
            return {
                "success": True,
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "total_rows": len(df),
                "batch_id": batch_id,
                "source_file": source_filename
            }
            
        except Exception as e:
            logger.error(f"Error importing Excel: {str(e)}")
            import traceback
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e)
            }

    # ==========================================================
    # COLUMN NORMALIZATION
    # ==========================================================

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize column names using aliases.
        """
        # Create a mapping for actual columns in the dataframe
        column_mapping = {}
        
        for col in df.columns:
            # Try to find the column in aliases
            if col in self.COLUMN_ALIASES:
                column_mapping[col] = self.COLUMN_ALIASES[col]
            # Also try case-insensitive match
            else:
                for alias, standard in self.COLUMN_ALIASES.items():
                    if col.lower() == alias.lower():
                        column_mapping[col] = standard
                        break
        
        # Rename columns
        if column_mapping:
            df = df.rename(columns=column_mapping)
            logger.info(f"Normalized {len(column_mapping)} columns: {list(column_mapping.values())}")
        
        return df

    # ==========================================================
    # COLUMN VALIDATION
    # ==========================================================

    def _validate_columns(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Validate that required columns exist in the Excel file.
        """
        missing_columns = []
        
        for col in self.REQUIRED_COLUMNS:
            if col not in df.columns:
                missing_columns.append(col)
        
        if missing_columns:
            # Log available columns for debugging
            available_cols = list(df.columns)
            logger.error(f"Available columns: {available_cols}")
            logger.error(f"Missing required columns: {missing_columns}")
            
            return {
                "is_valid": False,
                "error": f"Missing required columns: {', '.join(missing_columns)}",
                "missing_columns": missing_columns,
                "available_columns": available_cols
            }
        
        # Warn about optional missing columns
        optional_missing = []
        for col in self.COLUMN_MAPPING.keys():
            if col not in df.columns and col not in self.REQUIRED_COLUMNS:
                optional_missing.append(col)
        
        if optional_missing:
            logger.warning(f"Optional columns missing: {', '.join(optional_missing[:10])}")
        
        return {
            "is_valid": True,
            "optional_missing": optional_missing
        }

    # ==========================================================
    # DUPLICATE HANDLING
    # ==========================================================

    def _handle_duplicates(
        self, 
        records: List[Dict[str, Any]], 
        update_existing: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Check for existing DN records and handle accordingly.
        """
        if not records:
            return records
        
        # Extract all DN numbers from records
        dn_numbers = [r.get("dn_no") for r in records if r.get("dn_no")]
        
        if not dn_numbers:
            return records
        
        # Query existing records
        existing_records = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no.in_(dn_numbers)
        ).all()
        
        existing_dn_map = {r.dn_no: r for r in existing_records}
        
        processed_records = []
        
        for record in records:
            dn_no = record.get("dn_no")
            
            if dn_no and dn_no in existing_dn_map:
                if update_existing:
                    # Add existing ID for update
                    record["_existing_id"] = existing_dn_map[dn_no].id
                    processed_records.append(record)
                    logger.debug(f"Will update existing DN: {dn_no}")
                else:
                    # Skip duplicate
                    logger.debug(f"Skipping duplicate DN: {dn_no}")
                    continue
            else:
                # New record
                processed_records.append(record)
        
        return processed_records

    # ==========================================================
    # DATA TRANSFORMATION
    # ==========================================================

    def _transform_data(
        self, 
        df: pd.DataFrame, 
        source_filename: str, 
        batch_id: int = None
    ) -> List[Dict[str, Any]]:
        """
        Transform Excel data to match DeliveryReport model.
        """
        records = []
        current_time = datetime.utcnow()
        
        for index, row in df.iterrows():
            try:
                record = {}
                
                # Map columns based on mapping
                for standard_col, db_col in self.COLUMN_MAPPING.items():
                    if standard_col in df.columns:
                        value = row[standard_col]
                        
                        # Handle NaN values
                        if pd.isna(value):
                            value = None
                        
                        # Convert dates
                        if db_col in ["dn_create_date", "good_issue_date", "pod_date"]:
                            value = self._parse_date(value)
                        
                        # Convert numeric fields
                        if db_col in ["dn_qty", "dn_amount"]:
                            value = self._parse_numeric(value)
                        
                        record[db_col] = value
                
                # Skip if DN No is missing (critical field)
                if not record.get("dn_no"):
                    logger.warning(f"Skipping row {index + 2}: Missing DN No")
                    continue
                
                # Add tracking fields
                record["source_file"] = source_filename
                record["upload_batch_id"] = batch_id
                record["imported_at"] = current_time
                record["created_at"] = current_time
                record["updated_at"] = current_time
                
                # Generate delivery_location from warehouse + ship_to_city
                record["delivery_location"] = self._generate_delivery_location(
                    record.get("warehouse"),
                    record.get("ship_to_city")
                )
                
                # Set initial statuses
                record["delivery_status"] = self._determine_delivery_status(record)
                record["pgi_status"] = self._determine_pgi_status(record)
                record["pod_status"] = self._determine_pod_status(record)
                record["pending_flag"] = self._determine_pending_flag(record)
                
                records.append(record)
                
            except Exception as e:
                logger.error(f"Error transforming row {index + 2}: {str(e)}")
                continue
        
        return records

    # ==========================================================
    # DATA PARSING HELPERS
    # ==========================================================

    def _parse_date(self, value: Any) -> Optional[date]:
        """
        Parse date from various formats.
        """
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        
        try:
            # If it's already a datetime/date object
            if isinstance(value, (datetime, date)):
                return value if isinstance(value, date) else value.date()
            
            # If it's a string
            if isinstance(value, str):
                value = value.strip()
                # Try common date formats (including new format)
                for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d.%m.%Y"]:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
            
            # If it's a number (Excel serial date)
            if isinstance(value, (int, float)):
                # Excel serial date (1900 system)
                try:
                    return datetime.fromordinal(datetime(1900, 1, 1).toordinal() + int(value) - 2).date()
                except:
                    return None
            
            return None
            
        except Exception as e:
            logger.warning(f"Failed to parse date: {value} - {str(e)}")
            return None

    def _parse_numeric(self, value: Any) -> Optional[float]:
        """
        Parse numeric values safely.
        """
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        
        try:
            # If it's a string, remove currency symbols and commas
            if isinstance(value, str):
                value = value.replace('$', '').replace(',', '').strip()
                return float(value) if value else None
            return float(value)
        except:
            return None

    def _generate_delivery_location(self, warehouse: str, city: str) -> str:
        """
        Generate delivery location string from warehouse and city.
        """
        warehouse_str = warehouse or ""
        city_str = city or ""
        
        if warehouse_str and city_str:
            return f"{warehouse_str} → {city_str}"
        elif warehouse_str:
            return warehouse_str
        elif city_str:
            return city_str
        else:
            return ""

    # ==========================================================
    # STATUS DETERMINATION
    # ==========================================================

    def _determine_delivery_status(self, record: Dict[str, Any]) -> str:
        """
        Determine delivery status based on dates.
        """
        if record.get("pod_date"):
            return "Completed"
        elif record.get("good_issue_date"):
            return "In Transit"
        else:
            return "Pending"

    def _determine_pgi_status(self, record: Dict[str, Any]) -> str:
        """
        Determine PGI status based on good_issue_date.
        """
        if record.get("good_issue_date"):
            return "Completed"
        else:
            return "Pending"

    def _determine_pod_status(self, record: Dict[str, Any]) -> str:
        """
        Determine POD status based on pod_date.
        """
        if record.get("pod_date"):
            return "Received"
        else:
            return "Pending"

    def _determine_pending_flag(self, record: Dict[str, Any]) -> bool:
        """
        Determine if delivery is pending (no POD date).
        """
        return record.get("pod_date") is None

    # ==========================================================
    # BULK INSERT / UPDATE
    # ==========================================================

    def _bulk_insert(
        self, 
        records: List[Dict[str, Any]], 
        skip_duplicates: bool = True,
        update_existing: bool = False
    ) -> Tuple[int, int, int]:
        """
        Bulk insert/update records into database.
        Returns (inserted_count, updated_count, skipped_count)
        """
        if not records:
            return 0, 0, 0
        
        inserted_count = 0
        updated_count = 0
        skipped_count = 0
        
        # Separate new records from updates
        updates = [r for r in records if "_existing_id" in r]
        inserts = [r for r in records if "_existing_id" not in r]
        
        # Handle updates (if update_existing is True)
        if update_existing and updates:
            for record in updates:
                try:
                    existing_id = record.pop("_existing_id")
                    # Remove fields that shouldn't be updated
                    record.pop("created_at", None)
                    record.pop("imported_at", None)
                    record["updated_at"] = datetime.utcnow()
                    
                    # Update the record
                    self.db.query(DeliveryReport).filter(
                        DeliveryReport.id == existing_id
                    ).update(record)
                    updated_count += 1
                except Exception as e:
                    logger.error(f"Failed to update record: {str(e)}")
                    skipped_count += 1
            
            self.db.commit()
        
        # Handle inserts
        if inserts:
            try:
                # Clean inserts (remove _existing_id if present)
                for record in inserts:
                    record.pop("_existing_id", None)
                
                self.db.bulk_insert_mappings(DeliveryReport, inserts)
                self.db.commit()
                inserted_count = len(inserts)
            except Exception as e:
                self.db.rollback()
                logger.error(f"Bulk insert failed: {str(e)}")
                # Fallback to individual inserts
                inserted_count = self._individual_insert(inserts)
        
        return inserted_count, updated_count, skipped_count

    def _individual_insert(self, records: List[Dict[str, Any]]) -> int:
        """
        Fallback method: insert records one by one.
        """
        inserted = 0
        for record in records:
            try:
                # Remove any internal fields
                record.pop("_existing_id", None)
                
                delivery = DeliveryReport(**record)
                self.db.add(delivery)
                self.db.commit()
                inserted += 1
            except Exception as e:
                self.db.rollback()
                logger.error(f"Failed to insert record: {str(e)}")
                continue
        return inserted

    # ==========================================================
    # DERIVED FIELDS UPDATE
    # ==========================================================

    def _update_derived_fields(self, batch_id: int = None):
        """
        Update derived fields like delivery_location for a batch.
        """
        query = self.db.query(DeliveryReport)
        
        if batch_id:
            query = query.filter(DeliveryReport.upload_batch_id == batch_id)
        
        records = query.all()
        
        for record in records:
            # Update delivery_location
            record.delivery_location = self._generate_delivery_location(
                record.warehouse,
                record.ship_to_city
            )
            
            # Update statuses
            record.delivery_status = self._determine_delivery_status({
                "pod_date": record.pod_date,
                "good_issue_date": record.good_issue_date
            })
            record.pgi_status = self._determine_pgi_status({
                "good_issue_date": record.good_issue_date
            })
            record.pod_status = self._determine_pod_status({
                "pod_date": record.pod_date
            })
            record.pending_flag = self._determine_pending_flag({
                "pod_date": record.pod_date
            })
            record.updated_at = datetime.utcnow()
        
        self.db.commit()
        logger.info(f"Updated derived fields for {len(records)} records")

    # ==========================================================
    # UTILITY METHODS
    # ==========================================================

    def get_import_summary(self, batch_id: int) -> Dict[str, Any]:
        """
        Get summary statistics for a specific batch import.
        """
        records = self.db.query(DeliveryReport).filter(
            DeliveryReport.upload_batch_id == batch_id
        ).all()
        
        if not records:
            return {"error": f"Batch {batch_id} not found"}
        
        pending_count = sum(1 for r in records if r.pending_flag)
        completed_count = len(records) - pending_count
        
        total_amount = sum(r.dn_amount or 0 for r in records)
        pending_amount = sum(r.dn_amount or 0 for r in records if r.pending_flag)
        
        # Group by status
        by_status = {}
        for r in records:
            status = r.delivery_status or "Unknown"
            by_status[status] = by_status.get(status, 0) + 1
        
        return {
            "batch_id": batch_id,
            "total_records": len(records),
            "pending_count": pending_count,
            "completed_count": completed_count,
            "total_amount": float(total_amount),
            "pending_amount": float(pending_amount),
            "by_status": by_status,
            "source_files": list(set(r.source_file for r in records if r.source_file))
        }

    def delete_batch(self, batch_id: int) -> Dict[str, Any]:
        """
        Delete all records for a specific batch.
        """
        try:
            count = self.db.query(DeliveryReport).filter(
                DeliveryReport.upload_batch_id == batch_id
            ).count()
            
            deleted = self.db.query(DeliveryReport).filter(
                DeliveryReport.upload_batch_id == batch_id
            ).delete()
            
            self.db.commit()
            
            return {
                "success": True,
                "deleted_count": deleted,
                "batch_id": batch_id,
                "original_count": count
            }
        except Exception as e:
            self.db.rollback()
            return {
                "success": False,
                "error": str(e)
            }


# ==========================================================
# CONVENIENCE FUNCTIONS
# ==========================================================

def import_delivery_report_excel(
    db: Session,
    file_path: str,
    source_filename: str,
    batch_id: int = None,
    skip_duplicates: bool = True,
    update_existing: bool = False
) -> Dict[str, Any]:
    """
    Convenience function to import Excel file.
    """
    service = ExcelImportService(db)
    return service.import_excel(
        file_path, 
        source_filename, 
        batch_id,
        skip_duplicates,
        update_existing
    )


def get_batch_summary(
    db: Session,
    batch_id: int
) -> Dict[str, Any]:
    """
    Convenience function to get batch summary.
    """
    service = ExcelImportService(db)
    return service.get_import_summary(batch_id)


def delete_import_batch(
    db: Session,
    batch_id: int
) -> Dict[str, Any]:
    """
    Convenience function to delete a batch.
    """
    service = ExcelImportService(db)
    return service.delete_batch(batch_id)
