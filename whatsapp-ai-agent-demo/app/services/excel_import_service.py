# ==========================================================
# FILE: app/services/excel_import_service.py
# ==========================================================

import os
import pandas as pd
from datetime import datetime, date
from sqlalchemy.orm import Session
from typing import Dict, List, Any, Tuple, Optional
import logging
import traceback

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

    # Column mappings (simplified for debugging)
    COLUMN_ALIASES = {
        "DN NO": "DN No",
        "DN NO.": "DN No",
        "DN NUMBER": "DN No",
        "DELIVERY": "DN No",
        "DELIVERY NO": "DN No",
        "ORDER TYPE": "Order Type",
        "DN AMOUNT": "DN Amount",
        "DN QTY": "DN Qty",
        "DN WORK": "DN Work",
        "DIVISION": "Division",
        "MATERIAL NO": "Material No",
        "CUSTOMER MODEL": "Customer Model",
        "SALES OFFICE": "Sales Office",
        "SOLD-TO-PARTY NAME": "Customer Name",
        "SHIP-TO CITY": "Ship To City",
        "STORAGE": "Storage Location",
        "WAREHOUSE": "Warehouse",
        "DN CREATE DATE": "DN Create Date",
        "GOOD ISSUE DATE": "Good Issue Date",
        "POD DATE": "POD Date",
        "SALES MANAGER": "Sales Manager",
        "CUSTOMER": "Customer Name",
        "CUSTOMER NAME": "Customer Name",
    }

    COLUMN_MAPPING = {
        "DN No": "dn_no",
        "Order Type": "order_type",
        "DN Amount": "dn_amount",
        "DN Qty": "dn_qty",
        "DN Work": "dn_work",
        "Division": "division",
        "Material No": "material_no",
        "Customer Model": "customer_model",
        "Sales Office": "sales_office",
        "Customer Name": "customer_name",
        "Ship To City": "ship_to_city",
        "Storage Location": "storage_location",
        "Warehouse": "warehouse",
        "DN Create Date": "dn_create_date",
        "Good Issue Date": "good_issue_date",
        "POD Date": "pod_date",
        "Sales Manager": "sales_manager",
        "Dealer Code": "dealer_code",
        "Customer Code": "customer_code",
        "Warehouse Code": "warehouse_code",
    }

    def __init__(self, db: Session):
        self.db = db

    # ==========================================================
    # MAIN IMPORT METHOD
    # ==========================================================

    def import_excel(
        self,
        file_path: str,
        source_filename: str,
        batch_id: int = None,
        skip_duplicates: bool = True,
        update_existing: bool = False
    ) -> Dict[str, Any]:
        """Import Excel file into delivery_reports table."""
        result = {
            "success": False,
            "inserted_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "total_rows": 0,
            "error": None,
            "debug_info": {}
        }
        
        try:
            print("=" * 70)
            print("📊 STARTING EXCEL IMPORT")
            print("=" * 70)
            print(f"File: {source_filename}")
            print(f"Batch ID: {batch_id}")
            
            # Step 1: Read Excel file
            print("\n📖 Step 1: Reading Excel file...")
            df = pd.read_excel(file_path)
            result["total_rows"] = len(df)
            print(f"   ✓ Found {len(df)} rows and {len(df.columns)} columns")
            print(f"   ✓ Columns: {list(df.columns)}")
            
            # Step 2: Show first few rows for debugging
            print("\n📋 Step 2: First 3 rows of data:")
            for idx, row in df.head(3).iterrows():
                print(f"   Row {idx + 1}: {dict(row)}")
            
            # Step 3: Normalize column names
            print("\n🔧 Step 3: Normalizing column names...")
            df = self._normalize_columns(df)
            print(f"   ✓ Normalized columns: {list(df.columns)}")
            
            # Step 4: Remove rows with empty DN
            print("\n🔧 Step 4: Validating DN column...")
            if "DN No" not in df.columns:
                result["error"] = "DN No column not found after normalization"
                print(f"   ❌ {result['error']}")
                return result
            
            before = len(df)
            df = df[df["DN No"].notna()]
            after = len(df)
            print(f"   ✓ Kept {after} rows with valid DN (removed {before - after} empty DN rows)")
            
            # Step 5: Transform data
            print("\n🔄 Step 5: Transforming data for database...")
            records = []
            for index, row in df.iterrows():
                try:
                    record = self._transform_row(row, source_filename, batch_id)
                    if record:
                        records.append(record)
                except Exception as e:
                    print(f"   ⚠️ Error transforming row {index}: {e}")
                    continue
            
            print(f"   ✓ Transformed {len(records)} records")
            
            # Step 6: Show first record preview
            if records:
                print("\n🔍 Step 6: First record preview:")
                for key, value in list(records[0].items())[:10]:
                    print(f"   {key}: {value}")
            
            # Step 7: Bulk insert
            print("\n💾 Step 7: Saving to PostgreSQL...")
            inserted_count = 0
            
            try:
                # Use bulk insert for better performance
                self.db.bulk_insert_mappings(DeliveryReport, records)
                self.db.commit()
                inserted_count = len(records)
                print(f"   ✓ Successfully inserted {inserted_count} records via bulk_insert_mappings")
            except Exception as e:
                print(f"   ⚠️ Bulk insert failed: {e}")
                print(f"   🔄 Trying individual inserts...")
                
                # Fallback to individual inserts
                for record in records:
                    try:
                        delivery = DeliveryReport(**record)
                        self.db.add(delivery)
                        self.db.commit()
                        inserted_count += 1
                    except Exception as inner_e:
                        print(f"   ❌ Failed to insert record: {inner_e}")
                        continue
            
            result["success"] = True
            result["inserted_count"] = inserted_count
            result["debug_info"] = {
                "rows_read": len(df),
                "records_transformed": len(records),
                "columns_found": list(df.columns)
            }
            
            print("\n" + "=" * 70)
            print(f"✅ IMPORT COMPLETE: {inserted_count} records inserted")
            print("=" * 70)
            
        except Exception as e:
            print(f"\n❌ IMPORT FAILED: {str(e)}")
            traceback.print_exc()
            result["error"] = str(e)
            try:
                self.db.rollback()
            except:
                pass
        
        return result

    # ==========================================================
    # HELPER METHODS
    # ==========================================================

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names using aliases."""
        column_mapping = {}
        
        for col in df.columns:
            clean_col = str(col).strip().upper()
            
            # Try exact match
            if clean_col in self.COLUMN_ALIASES:
                column_mapping[col] = self.COLUMN_ALIASES[clean_col]
            else:
                # Try contains match
                for alias, standard in self.COLUMN_ALIASES.items():
                    if alias in clean_col or clean_col in alias:
                        column_mapping[col] = standard
                        break
        
        if column_mapping:
            df = df.rename(columns=column_mapping)
        
        return df

    def _transform_row(self, row, source_filename: str, batch_id: int) -> Optional[Dict[str, Any]]:
        """Transform a single row to database record."""
        current_time = datetime.utcnow()
        
        # Parse dates
        dn_create_date = self._parse_date(row.get("DN Create Date"))
        good_issue_date = self._parse_date(row.get("Good Issue Date"))
        pod_date = self._parse_date(row.get("POD Date"))
        
        # Parse numeric values
        dn_amount = self._parse_numeric(row.get("DN Amount"))
        dn_qty = self._parse_numeric(row.get("DN Qty"))
        
        record = {
            # Core fields
            "dn_no": str(row.get("DN No")) if row.get("DN No") else None,
            "order_type": str(row.get("Order Type")) if row.get("Order Type") else None,
            "dn_amount": dn_amount,
            "dn_qty": dn_qty,
            "dn_work": str(row.get("DN Work")) if row.get("DN Work") else None,
            "division": str(row.get("Division")) if row.get("Division") else None,
            
            # Material and customer fields
            "material_no": str(row.get("Material No")) if row.get("Material No") else None,
            "customer_model": str(row.get("Customer Model")) if row.get("Customer Model") else None,
            "sales_office": str(row.get("Sales Office")) if row.get("Sales Office") else None,
            "customer_name": str(row.get("Customer Name")) if row.get("Customer Name") else None,
            
            # Location fields
            "ship_to_city": str(row.get("Ship To City")) if row.get("Ship To City") else None,
            "storage_location": str(row.get("Storage Location")) if row.get("Storage Location") else None,
            "warehouse": str(row.get("Warehouse")) if row.get("Warehouse") else None,
            
            # Date fields
            "dn_create_date": dn_create_date,
            "good_issue_date": good_issue_date,
            "pod_date": pod_date,
            
            # Sales fields
            "sales_manager": str(row.get("Sales Manager")) if row.get("Sales Manager") else None,
            
            # Tracking fields
            "source_file": source_filename,
            "upload_batch_id": batch_id,
            "imported_at": current_time,
            "created_at": current_time,
            "updated_at": current_time,
        }
        
        # Auto-detect status
        if record.get("pod_date"):
            record["delivery_status"] = "Completed"
            record["pod_status"] = "Received"
            record["pending_flag"] = False
        elif record.get("good_issue_date"):
            record["delivery_status"] = "In Transit"
            record["pgi_status"] = "Completed"
            record["pod_status"] = "Pending"
            record["pending_flag"] = True
        else:
            record["delivery_status"] = "Pending"
            record["pgi_status"] = "Pending"
            record["pod_status"] = "Pending"
            record["pending_flag"] = True
        
        # Generate delivery location
        warehouse = record.get("warehouse") or ""
        city = record.get("ship_to_city") or ""
        if warehouse and city:
            record["delivery_location"] = f"{warehouse} → {city}"
        elif warehouse:
            record["delivery_location"] = warehouse
        elif city:
            record["delivery_location"] = city
        else:
            record["delivery_location"] = ""
        
        return record if record.get("dn_no") else None

    def _parse_date(self, value: Any) -> Optional[date]:
        """Parse date from various formats."""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        
        try:
            if isinstance(value, (datetime, date)):
                return value if isinstance(value, date) else value.date()
            
            if isinstance(value, str):
                value = value.strip()
                for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d", "%d.%m.%Y", "%d-%m-%Y"]:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
            
            if isinstance(value, (int, float)):
                try:
                    return datetime.fromordinal(datetime(1900, 1, 1).toordinal() + int(value) - 2).date()
                except:
                    return None
            
            return None
            
        except Exception as e:
            print(f"   ⚠️ Date parse error: {value} - {e}")
            return None

    def _parse_numeric(self, value: Any) -> Optional[float]:
        """Parse numeric values safely."""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        
        try:
            if isinstance(value, str):
                value = value.replace('$', '').replace('₹', '').replace('PKR', '').replace('Rs.', '').replace('Rs', '').replace(',', '').strip()
                return float(value) if value else None
            return float(value)
        except:
            return None

    # ==========================================================
    # UTILITY METHODS
    # ==========================================================

    def get_import_summary(self, batch_id: int) -> Dict[str, Any]:
        """Get import batch summary."""
        records = self.db.query(DeliveryReport).filter(DeliveryReport.upload_batch_id == batch_id).all()
        
        if not records:
            return {"error": f"Batch {batch_id} not found"}
        
        pending_count = sum(1 for r in records if r.pending_flag)
        
        return {
            "batch_id": batch_id,
            "total_records": len(records),
            "pending_count": pending_count,
            "completed_count": len(records) - pending_count,
            "total_amount": float(sum(r.dn_amount or 0 for r in records)),
            "pending_amount": float(sum(r.dn_amount or 0 for r in records if r.pending_flag)),
            "source_files": list(set(r.source_file for r in records if r.source_file))
        }

    def delete_batch(self, batch_id: int) -> Dict[str, Any]:
        """Delete a batch."""
        try:
            count = self.db.query(DeliveryReport).filter(DeliveryReport.upload_batch_id == batch_id).count()
            deleted = self.db.query(DeliveryReport).filter(DeliveryReport.upload_batch_id == batch_id).delete()
            self.db.commit()
            
            return {"success": True, "deleted_count": deleted, "batch_id": batch_id, "original_count": count}
        except Exception as e:
            self.db.rollback()
            return {"success": False, "error": str(e)}


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
    """Convenience function to import Excel file."""
    service = ExcelImportService(db)
    return service.import_excel(file_path, source_filename, batch_id, skip_duplicates, update_existing)


def get_batch_summary(db: Session, batch_id: int) -> Dict[str, Any]:
    """Convenience function to get batch summary."""
    service = ExcelImportService(db)
    return service.get_import_summary(batch_id)


def delete_import_batch(db: Session, batch_id: int) -> Dict[str, Any]:
    """Convenience function to delete a batch."""
    service = ExcelImportService(db)
    return service.delete_batch(batch_id)
