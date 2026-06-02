# ==========================================================
# FILE: app/services/excel_import_service.py
# ==========================================================

import os
import pandas as pd
from datetime import datetime, date
from sqlalchemy.orm import Session
from typing import Dict, List, Any, Tuple, Optional
import logging
import sys

from app.models import DeliveryReport

# Force logging to print immediately
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)


# ==========================================================
# EXCEL IMPORT SERVICE
# ==========================================================

class ExcelImportService:
    """
    Service for importing Excel delivery reports into the database.
    """

    def __init__(self, db: Session):
        self.db = db

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
        """
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
            print("\n" + "="*80)
            print("🚀 EXCEL IMPORT STARTED")
            print("="*80)
            print(f"📁 File: {source_filename}")
            print(f"🆔 Batch ID: {batch_id}")
            sys.stdout.flush()
            
            # Step 1: Check if file exists
            if not os.path.exists(file_path):
                error_msg = f"File not found: {file_path}"
                print(f"❌ {error_msg}")
                result["error"] = error_msg
                return result
            
            print(f"✅ File exists, size: {os.path.getsize(file_path)} bytes")
            sys.stdout.flush()
            
            # ==========================================================
            # CRITICAL FIX: Auto-detect header row
            # ==========================================================
            print("\n🔍 Step 2: Detecting header row...")
            
            # Try to find the row that contains column headers
            header_row = None
            found_headers = None
            
            # Search first 20 rows for headers
            for row_num in range(20):
                try:
                    # Read just this row to check
                    test_df = pd.read_excel(file_path, header=None, nrows=row_num + 1)
                    
                    # Get the last row as potential headers
                    potential_headers = test_df.iloc[row_num].astype(str).tolist()
                    
                    # Check if this row looks like column headers
                    header_text = ' '.join(potential_headers).lower()
                    
                    # Look for logistics-related keywords
                    if any(keyword in header_text for keyword in [
                        'dn', 'delivery', 'customer', 'order', 'division', 
                        'material', 'warehouse', 'city', 'amount', 'pgi', 'pod'
                    ]):
                        header_row = row_num
                        found_headers = potential_headers
                        print(f"   ✅ Found header row at row {row_num + 1}")
                        print(f"   📋 Headers: {found_headers[:10]}...")  # Show first 10
                        break
                        
                except Exception as e:
                    continue
            
            # If header found, read the file with that header row
            if header_row is not None:
                print(f"\n📖 Step 3: Reading Excel with header at row {header_row + 1}...")
                df = pd.read_excel(file_path, header=header_row)
            else:
                print(f"\n📖 Step 3: No header detected, reading first row as headers...")
                df = pd.read_excel(file_path)
            
            print(f"   ✅ Successfully read Excel file")
            print(f"   📊 Rows: {len(df)}")
            print(f"   📊 Columns: {len(df.columns)}")
            print(f"   📋 Column names: {list(df.columns)}")
            sys.stdout.flush()
            
            result["total_rows"] = len(df)
            
            # Step 4: Clean column names (remove NaN, empty, etc.)
            print("\n🧹 Step 4: Cleaning column names...")
            
            # Remove any NaN or empty column names
            clean_columns = []
            for col in df.columns:
                if pd.isna(col) or str(col).strip() == '' or 'Unnamed' in str(col):
                    clean_columns.append(None)
                else:
                    clean_columns.append(str(col).strip())
            
            # Rename columns
            new_columns = {}
            for idx, col in enumerate(df.columns):
                if clean_columns[idx] is None:
                    new_columns[col] = f"Column_{idx}"
                else:
                    new_columns[col] = clean_columns[idx]
            
            df = df.rename(columns=new_columns)
            print(f"   📋 Cleaned columns: {list(df.columns)}")
            sys.stdout.flush()
            
            # Step 5: Show sample data
            print("\n📋 Step 5: Sample data (first 2 rows):")
            for idx in range(min(2, len(df))):
                row_dict = {}
                for col in df.columns[:10]:  # Show first 10 columns only
                    val = df.iloc[idx][col]
                    if pd.isna(val):
                        val = "NULL"
                    elif isinstance(val, (float, int)):
                        val = str(val)
                    else:
                        val = str(val)[:30]
                    row_dict[col] = val
                print(f"   Row {idx+1}: {row_dict}")
            sys.stdout.flush()
            
            # Step 6: Map column names
            print("\n🔄 Step 6: Mapping column names...")
            
            # Define column mappings (case-insensitive)
            column_mapping = {
                "DN NO": "DN No",
                "DN NO.": "DN No",
                "DN NUMBER": "DN No",
                "DN": "DN No",
                "DELIVERY NO": "DN No",
                "DELIVERY": "DN No",
                "DELIVERY NOTE": "DN No",
                "DOCUMENT NO": "DN No",
                "ORDER TYPE": "Order Type",
                "DN AMOUNT": "DN Amount",
                "DN QTY": "DN Qty",
                "DN WORK": "DN Work",
                "DIVISION": "Division",
                "MATERIAL NO": "Material No",
                "CUSTOMER MODEL": "Customer Model",
                "SALES OFFICE": "Sales Office",
                "SOLD-TO-PARTY NAME": "Customer Name",
                "SOLD TO PARTY NAME": "Customer Name",
                "SHIP-TO CITY": "Ship To City",
                "SHIP TO CITY": "Ship To City",
                "STORAGE": "Storage Location",
                "STORAGE LOCATION": "Storage Location",
                "WAREHOUSE": "Warehouse",
                "DN CREATE DATE": "DN Create Date",
                "DN CREATE DATE": "DN Create Date",
                "GOOD ISSUE DATE": "Good Issue Date",
                "POD DATE": "POD Date",
                "SALES MANAGER": "Sales Manager",
                "CUSTOMER NAME": "Customer Name",
                "CUSTOMER": "Customer Name",
                "DEALER": "Customer Name",
                "DEALER NAME": "Customer Name",
            }
            
            # Apply mappings (case-insensitive)
            mapping_applied = {}
            for col in df.columns:
                col_upper = str(col).upper().strip()
                for old_key, new_key in column_mapping.items():
                    if col_upper == old_key or old_key in col_upper:
                        mapping_applied[col] = new_key
                        print(f"   ✅ Mapped '{col}' -> '{new_key}'")
                        break
            
            if mapping_applied:
                df = df.rename(columns=mapping_applied)
            else:
                print("   ⚠️ No columns were mapped")
            
            print(f"   📋 Columns after mapping: {list(df.columns)}")
            sys.stdout.flush()
            
            # Step 7: Check for DN No column
            print("\n🔍 Step 7: Validating DN No column...")
            if "DN No" not in df.columns:
                # Try to find any column containing DN
                dn_column = None
                for col in df.columns:
                    if "DN" in str(col).upper():
                        dn_column = col
                        break
                
                if dn_column:
                    df.rename(columns={dn_column: "DN No"}, inplace=True)
                    print(f"   ✅ Found DN column: '{dn_column}' -> renamed to 'DN No'")
                else:
                    error_msg = f"DN No column not found. Available columns: {list(df.columns)}"
                    print(f"   ❌ {error_msg}")
                    result["error"] = error_msg
                    result["available_columns"] = list(df.columns)
                    return result
            
            print(f"   ✅ DN No column found")
            sys.stdout.flush()
            
            # Step 8: Remove rows with empty DN
            before = len(df)
            df = df[df["DN No"].notna()]
            after = len(df)
            print(f"   🗑️ Removed {before - after} rows with empty DN")
            print(f"   ✅ Remaining rows: {after}")
            sys.stdout.flush()
            
            # Step 9: Transform each row
            print("\n🔄 Step 9: Transforming data...")
            records = []
            current_time = datetime.utcnow()
            
            for idx, row in df.iterrows():
                try:
                    record = self._transform_row(row, source_filename, batch_id, current_time)
                    if record and record.get("dn_no"):
                        records.append(record)
                except Exception as e:
                    print(f"   ⚠️ Row {idx} transformation failed: {e}")
                    continue
            
            print(f"   ✅ Transformed {len(records)} records")
            sys.stdout.flush()
            
            # Step 10: Show first record preview
            if records:
                print("\n🔍 Step 10: First record preview:")
                preview_record = records[0]
                for key in ["dn_no", "customer_name", "ship_to_city", "dn_amount", "delivery_status"]:
                    if key in preview_record:
                        print(f"   {key}: {preview_record[key]}")
            else:
                print("\n⚠️ No records to insert!")
                result["error"] = "No valid records found in Excel file"
                return result
            
            # Step 11: Insert into database
            print("\n💾 Step 11: Inserting into PostgreSQL...")
            inserted_count = 0
            
            try:
                # Try bulk insert first
                for record in records:
                    # Remove any keys that shouldn't be in the insert
                    clean_record = {k: v for k, v in record.items() if hasattr(DeliveryReport, k)}
                    delivery = DeliveryReport(**clean_record)
                    self.db.add(delivery)
                
                self.db.commit()
                inserted_count = len(records)
                print(f"   ✅ Successfully inserted {inserted_count} records")
                
            except Exception as e:
                print(f"   ❌ Insert failed: {e}")
                self.db.rollback()
                
                # Try individual inserts
                print(f"   🔄 Trying individual inserts...")
                for record in records:
                    try:
                        clean_record = {k: v for k, v in record.items() if hasattr(DeliveryReport, k)}
                        delivery = DeliveryReport(**clean_record)
                        self.db.add(delivery)
                        self.db.commit()
                        inserted_count += 1
                    except Exception as inner_e:
                        print(f"      ❌ Failed to insert DN {record.get('dn_no', 'Unknown')}: {inner_e}")
                        self.db.rollback()
            
            result["success"] = True
            result["inserted_count"] = inserted_count
            
            print("\n" + "="*80)
            print(f"✅ IMPORT COMPLETE: {inserted_count} records inserted")
            print("="*80)
            sys.stdout.flush()
            
        except Exception as e:
            print(f"\n❌ FATAL ERROR: {str(e)}")
            import traceback
            traceback.print_exc()
            result["error"] = str(e)
            sys.stdout.flush()
        
        return result

    def _transform_row(self, row, source_filename: str, batch_id: int, current_time: datetime) -> Dict[str, Any]:
        """Transform a single row to database record."""
        
        # Helper function to safely get string value
        def get_str(col):
            val = row.get(col)
            if pd.isna(val):
                return None
            return str(val).strip() if val else None
        
        # Helper function to safely get numeric value
        def get_num(col):
            val = row.get(col)
            if pd.isna(val):
                return None
            try:
                if isinstance(val, str):
                    val = val.replace(',', '').replace('$', '').replace('₹', '').strip()
                    return float(val) if val else None
                return float(val) if val is not None else None
            except:
                return None
        
        # Helper function to safely get date
        def get_date(col):
            val = row.get(col)
            if pd.isna(val):
                return None
            try:
                if isinstance(val, datetime):
                    return val.date()
                if isinstance(val, date):
                    return val
                if isinstance(val, str):
                    for fmt in ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y%m%d"]:
                        try:
                            return datetime.strptime(val.strip(), fmt).date()
                        except:
                            continue
                if isinstance(val, (int, float)):
                    return datetime.fromordinal(datetime(1900, 1, 1).toordinal() + int(val) - 2).date()
                return None
            except:
                return None
        
        # Build the record
        record = {
            "dn_no": get_str("DN No"),
            "order_type": get_str("Order Type"),
            "dn_amount": get_num("DN Amount"),
            "dn_qty": get_num("DN Qty"),
            "dn_work": get_str("DN Work"),
            "division": get_str("Division"),
            "material_no": get_str("Material No"),
            "customer_model": get_str("Customer Model"),
            "sales_office": get_str("Sales Office"),
            "customer_name": get_str("Customer Name"),
            "ship_to_city": get_str("Ship To City"),
            "storage_location": get_str("Storage Location"),
            "warehouse": get_str("Warehouse"),
            "dn_create_date": get_date("DN Create Date"),
            "good_issue_date": get_date("Good Issue Date"),
            "pod_date": get_date("POD Date"),
            "sales_manager": get_str("Sales Manager"),
            "source_file": source_filename,
            "upload_batch_id": batch_id,
            "imported_at": current_time,
            "created_at": current_time,
            "updated_at": current_time,
        }
        
        # Set status based on dates
        if record.get("pod_date"):
            record["delivery_status"] = "Completed"
            record["pod_status"] = "Received"
            record["pending_flag"] = False
            record["pgi_status"] = "Completed"
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
        
        # Set delivery location
        warehouse = record.get("warehouse") or ""
        city = record.get("ship_to_city") or ""
        if warehouse and city:
            record["delivery_location"] = f"{warehouse} → {city}"
        elif warehouse:
            record["delivery_location"] = warehouse
        elif city:
            record["delivery_location"] = city
        else:
            record["delivery_location"] = None
        
        return record

    def get_import_summary(self, batch_id: int) -> Dict[str, Any]:
        """Get import batch summary."""
        records = self.db.query(DeliveryReport).filter(DeliveryReport.upload_batch_id == batch_id).all()
        
        if not records:
            return {"error": f"Batch {batch_id} not found"}
        
        pending_count = sum(1 for r in records if r.pending_flag)
        total_amount = sum(r.dn_amount or 0 for r in records)
        pending_amount = sum(r.dn_amount or 0 for r in records if r.pending_flag)
        
        return {
            "batch_id": batch_id,
            "total_records": len(records),
            "pending_count": pending_count,
            "completed_count": len(records) - pending_count,
            "total_amount": float(total_amount),
            "pending_amount": float(pending_amount),
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
    service = ExcelImportService(db)
    return service.import_excel(file_path, source_filename, batch_id, skip_duplicates, update_existing)


def get_batch_summary(db: Session, batch_id: int) -> Dict[str, Any]:
    service = ExcelImportService(db)
    return service.get_import_summary(batch_id)


def delete_import_batch(db: Session, batch_id: int) -> Dict[str, Any]:
    service = ExcelImportService(db)
    return service.delete_batch(batch_id)
