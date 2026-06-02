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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', stream=sys.stdout)
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
            # Dynamic Header Detection with Improved Scoring
            # ==========================================================
            print("\n📖 Step 2: Reading Excel file with dynamic header detection...")
            
            excel_file = pd.ExcelFile(file_path)
            print(f"   📊 Available sheets: {excel_file.sheet_names}")
            
            best_df = None
            best_score = 0
            best_sheet = None
            best_header_row = None
            
            # Required headers for validation
            required_headers = ["DN", "CITY", "WAREHOUSE", "AMOUNT"]
            
            for sheet in excel_file.sheet_names:
                print(f"\n   🔍 Checking sheet: '{sheet}'")
                
                # Get sheet info for safer iteration
                sheet_df = pd.read_excel(file_path, sheet_name=sheet, nrows=0)
                sheet_rows = min(75, len(sheet_df.index) + 10) if hasattr(sheet_df, 'index') else 75
                
                for header_row in range(sheet_rows):
                    try:
                        test_df = pd.read_excel(
                            file_path,
                            sheet_name=sheet,
                            header=header_row
                        )
                        
                        # Normalize column names for scoring
                        columns = [
                            str(col).strip().upper()
                            for col in test_df.columns
                        ]
                        
                        score = 0
                        header_hits = 0
                        
                        # IMPROVED: Split words to avoid partial matches like "UNNAMED" matching "DN"
                        for col in columns:
                            # Split by spaces, hyphens, underscores
                            words = col.replace("-", " ").replace("_", " ").split()
                            
                            # Check for exact word matches
                            if "DN" in words or "DELIVERY" in words or "OUTBOUND" in words:
                                score += 5
                            
                            if "CITY" in words:
                                score += 3
                            
                            if "WAREHOUSE" in words or "STORAGE" in words:
                                score += 3
                            
                            if "AMOUNT" in words or "QTY" in words:
                                score += 3
                            
                            if "CUSTOMER" in words or "SOLD" in words:
                                score += 2
                            
                            # Check for required headers
                            for req in required_headers:
                                if req in words:
                                    header_hits += 1
                        
                        # Require at least 2 required headers
                        if header_hits < 2:
                            if header_row == 0:  # Only log for first few rows to avoid spam
                                print(f"      ⏭️ Skipping row {header_row}: only {header_hits}/2 required headers found")
                            continue
                        
                        # Sheet name priority bonus
                        sheet_upper = sheet.upper()
                        if "PGI" in sheet_upper:
                            score += 10
                            print(f"      🎯 +10 bonus for 'PGI' in sheet name")
                        
                        if "DN" in sheet_upper.split():
                            score += 10
                            print(f"      🎯 +10 bonus for 'DN' in sheet name")
                        
                        if "DELIVERY" in sheet_upper.split():
                            score += 10
                            print(f"      🎯 +10 bonus for 'DELIVERY' in sheet name")
                        
                        if score > best_score:
                            best_score = score
                            best_df = test_df
                            best_sheet = sheet
                            best_header_row = header_row
                            print(f"      ✅ New best match! Score: {score}, Header row: {header_row}, Required headers hit: {header_hits}")
                            
                    except Exception as e:
                        # Better error logging for debugging
                        logger.debug(f"Sheet={sheet}, row={header_row}: {e}")
                        pass
            
            # Minimum score validation
            min_required_score = 10
            if best_df is None or best_score < min_required_score:
                error_msg = f"No valid delivery report found. Best score={best_score if best_df else 0}. Required minimum={min_required_score}"
                print(f"   ❌ {error_msg}")
                raise Exception(error_msg)
            
            print(f"\n   ✅ Selected sheet: '{best_sheet}'")
            print(f"   ✅ Header row: {best_header_row}")
            print(f"   ✅ Detection score: {best_score} (minimum required: {min_required_score})")
            
            df = best_df
            
            # Normalize column names
            print("\n🧹 Step 3: Normalizing column names...")
            df.columns = [
                str(col).strip().upper()
                for col in df.columns
            ]
            
            print(f"   📋 Normalized columns: {list(df.columns)}")
            sys.stdout.flush()
            
            # Log actual columns found
            print("\n📋 ACTUAL COLUMNS FOUND IN EXCEL:")
            for idx, col in enumerate(df.columns):
                print(f"   {idx+1}. '{col}'")
            sys.stdout.flush()
            
            result["total_rows"] = len(df)
            
            # Remove unnamed columns
            print("\n🧹 Step 4: Removing unnamed columns...")
            unnamed_cols = [col for col in df.columns if 'UNNAMED' in col]
            if unnamed_cols:
                print(f"   🗑️ Removing unnamed columns: {unnamed_cols}")
                df = df.drop(columns=unnamed_cols)
                print(f"   ✅ Remaining columns: {len(df.columns)}")
            sys.stdout.flush()
            
            # Show sample data
            print("\n📋 Step 5: Sample data (first 2 rows):")
            for idx in range(min(2, len(df))):
                row_dict = {}
                for col in df.columns[:10]:
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
            
            # Map column names
            print("\n🔄 Step 6: Mapping column names...")
            
            column_mapping = {
                "DN NO": "DN No",
                "DN NO.": "DN No",
                "DN NUMBER": "DN No",
                "DN": "DN No",
                "DELIVERY NO": "DN No",
                "DELIVERY": "DN No",
                "DELIVERY NOTE": "DN No",
                "DOCUMENT NO": "DN No",
                "OUTBOUND DELIVERY": "DN No",
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
                "GOOD ISSUE DATE": "Good Issue Date",
                "POD DATE": "POD Date",
                "SALES MANAGER": "Sales Manager",
                "CUSTOMER NAME": "Customer Name",
                "CUSTOMER": "Customer Name",
                "DEALER": "Customer Name",
                "DEALER NAME": "Customer Name",
            }
            
            mapping_applied = {}
            for col in df.columns:
                col_upper = str(col).strip().upper()
                if col_upper in column_mapping:
                    new_name = column_mapping[col_upper]
                    mapping_applied[col] = new_name
                    print(f"   ✅ Mapped '{col}' -> '{new_name}'")
                else:
                    for old_key, new_key in column_mapping.items():
                        if old_key in col_upper:
                            mapping_applied[col] = new_key
                            print(f"   ✅ Mapped '{col}' -> '{new_key}' (partial match: {old_key})")
                            break
            
            if mapping_applied:
                df = df.rename(columns=mapping_applied)
            else:
                print("   ⚠️ No columns were mapped")
            
            print(f"   📋 Columns after mapping: {list(df.columns)}")
            sys.stdout.flush()
            
            # Better DN Detection
            print("\n🔍 Step 7: Validating DN No column...")
            if "DN No" not in df.columns:
                dn_column = None
                for col in df.columns:
                    col_upper = str(col).upper()
                    # Split words to avoid "UNNAMED" matching "DN"
                    words = col_upper.replace("-", " ").replace("_", " ").split()
                    if ("DN" in words or 
                        "DELIVERY" in words or 
                        "OUTBOUND" in words):
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
            
            # Remove rows with empty DN
            before = len(df)
            df = df[df["DN No"].notna()]
            after = len(df)
            print(f"\n🗑️ Removed {before - after} rows with empty DN")
            print(f"✅ Remaining rows: {after}")
            sys.stdout.flush()
            
            # Remove old records before upload
            print("\n🗑️ Step 8: Removing old records before upload...")
            old_count = self.db.query(DeliveryReport).count()
            print(f"   📊 Database currently contains {old_count} records")
            
            if old_count > 0:
                deleted_count = self.db.query(DeliveryReport).delete(synchronize_session=False)
                self.db.commit()
                print(f"   ✅ Deleted {deleted_count} old records from database")
            else:
                print(f"   ℹ️ No existing records to delete")
            sys.stdout.flush()
            
            # Transform each row
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
            
            # Show first record preview
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
            
            # Insert into database
            print("\n💾 Step 11: Inserting into PostgreSQL...")
            inserted_count = 0
            
            model_columns = DeliveryReport.__table__.columns.keys()
            print(f"   📋 Model columns: {model_columns[:10]}...")
            
            # Bulk insert all records
            try:
                for record in records:
                    clean_record = {
                        k: v for k, v in record.items() 
                        if k in model_columns
                    }
                    delivery = DeliveryReport(**clean_record)
                    self.db.add(delivery)
                    inserted_count += 1
                    
                    # Commit in batches of 100
                    if inserted_count % 100 == 0:
                        self.db.commit()
                        print(f"      ✅ Committed {inserted_count} records so far")
                
                # Final commit
                if inserted_count % 100 != 0:
                    self.db.commit()
                
                print(f"   ✅ Successfully inserted {inserted_count} records")
                print(f"   📊 Expected rows: {len(records)}")
                
            except Exception as e:
                print(f"   ❌ Bulk insert failed: {e}")
                self.db.rollback()
                
                # Try individual inserts
                print(f"   🔄 Trying individual inserts...")
                inserted_count = 0
                for record in records:
                    try:
                        clean_record = {
                            k: v for k, v in record.items() 
                            if k in model_columns
                        }
                        delivery = DeliveryReport(**clean_record)
                        self.db.add(delivery)
                        self.db.commit()
                        inserted_count += 1
                        print(f"      ✅ Inserted DN: {record.get('dn_no', 'Unknown')}")
                    except Exception as inner_e:
                        print(f"      ❌ Failed to insert DN {record.get('dn_no', 'Unknown')}: {inner_e}")
                        self.db.rollback()
            
            # Database count verification
            final_count = self.db.query(DeliveryReport).count()
            print(f"\n   ✅ Database verification: {final_count} total rows now in database")
            print(f"   📊 Inserted: {inserted_count}, Expected: {len(records)}")
            
            result["success"] = inserted_count > 0
            result["inserted_count"] = inserted_count
            
            # Show comparison between expected and actual
            if inserted_count != len(records):
                print(f"   ⚠️ WARNING: Expected {len(records)} records but inserted {inserted_count}")
                result["debug_info"]["expected_records"] = len(records)
                result["debug_info"]["inserted_records"] = inserted_count
            
            print("\n" + "="*80)
            if result["success"]:
                print(f"✅ IMPORT COMPLETE: {inserted_count} records inserted")
                print(f"📊 Database now contains {final_count} rows")
            else:
                print(f"❌ IMPORT FAILED: No records were inserted")
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
        
        # Improved date conversion using pandas
        def get_date(col):
            val = row.get(col)
            if pd.isna(val):
                return None
            try:
                # Use pandas to_datetime for better Excel date handling
                if isinstance(val, (datetime, date)):
                    return val.date() if isinstance(val, datetime) else val
                
                # Try pandas to_datetime for string and numeric values
                converted = pd.to_datetime(val, errors='coerce')
                if pd.notna(converted):
                    return converted.date()
                
                return None
            except Exception as e:
                # Fallback to original method if pandas fails
                try:
                    if isinstance(val, (int, float)):
                        # Excel dates are days since 1900-01-01
                        return datetime.fromordinal(datetime(1900, 1, 1).toordinal() + int(val) - 2).date()
                except:
                    pass
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
            deleted = self.db.query(DeliveryReport).filter(DeliveryReport.upload_batch_id == batch_id).delete(synchronize_session=False)
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
