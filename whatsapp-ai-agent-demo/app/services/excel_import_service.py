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

    # ==========================================================
    # PHASE 2: EXPANDED COLUMN ALIASES
    # ==========================================================
    
    COLUMN_ALIASES = {
        # DN variations - Phase 2 expanded
        "DN": "DN No",
        "DN NO": "DN No",
        "DN NO.": "DN No",
        "DN NUMBER": "DN No",
        "DELIVERY": "DN No",
        "DELIVERY NO": "DN No",
        "DELIVERY NUMBER": "DN No",
        "DELIVERY NOTE": "DN No",
        "DELV NO": "DN No",
        "DOCUMENT": "DN No",
        "DOCUMENT NO": "DN No",
        "DOCUMENT NUMBER": "DN No",
        "INVOICE": "DN No",
        "INVOICE NO": "DN No",
        "REFERENCE NO": "DN No",
        "ORDER NO": "DN No",
        "OUTBOUND DELIVERY": "DN No",
        "OUTBOUND DELIVERY NUMBER": "DN No",  # Phase 2
        "DELIVERY DOCUMENT NUMBER": "DN No",   # Phase 2
        "OUTBOUND DELIVERY NO": "DN No",
        "DELIVERY DOCUMENT": "DN No",
        
        # Customer variations - Phase 2 expanded
        "CUSTOMER": "Customer Name",
        "CUSTOMER NAME": "Customer Name",
        "CUSTOMERNAME": "Customer Name",
        "DEALER": "Customer Name",
        "DEALER NAME": "Customer Name",
        "PARTY": "Customer Name",
        "PARTY NAME": "Customer Name",
        "SHIP TO": "Customer Name",           # Phase 2
        "SHIP TO CUSTOMER": "Customer Name",  # Phase 2
        "SHIP TO PARTY": "Customer Name",     # Phase 2
        "SOLD TO PARTY": "Customer Name",     # Phase 2
        "SOLD-TO PARTY": "Customer Name",     # Phase 2
        "CUSTOMER DESC": "Customer Name",     # Phase 2
        "CUSTOMER DESCRIPTION": "Customer Name",
        "CUSTOMER CODE": "Customer Code",
        "DEALER CODE": "Dealer Code",
        
        # Amount variations - Phase 2 expanded
        "AMOUNT": "DN Amount",
        "VALUE": "DN Amount",
        "NET VALUE": "DN Amount",
        "DN VALUE": "DN Amount",
        "DELIVERY AMOUNT": "DN Amount",
        "TOTAL AMOUNT": "DN Amount",
        "INVOICE AMOUNT": "DN Amount",
        "DELIVERY VALUE": "DN Amount",
        "NET AMOUNT": "DN Amount",
        "TOTAL VALUE": "DN Amount",
        "NET SALES": "DN Amount",             # Phase 2
        "SALES VALUE": "DN Amount",           # Phase 2
        "NET VALUE": "DN Amount",
        
        # City variations - Phase 2 expanded
        "CITY": "Ship To City",
        "DESTINATION": "Ship To City",
        "DESTINATION CITY": "Ship To City",
        "SHIP TO CITY": "Ship To City",
        "SHIP-TO CITY": "Ship To City",
        "SHIP CITY": "Ship To City",
        "DELIVERY CITY": "Ship To City",
        "DEST CITY": "Ship To City",
        "DESTINATION LOCATION": "Ship To City",  # Phase 2
        "DESTINATION POINT": "Ship To City",     # Phase 2
        "DELIVERY POINT": "Ship To City",        # Phase 2
        
        # Warehouse variations - Phase 2 expanded
        "WAREHOUSE": "Warehouse",
        "WHSE": "Warehouse",
        "STORAGE": "Storage Location",
        "STORAGE LOCATION": "Storage Location",
        "STORAGE LOC": "Storage Location",
        "PLANT": "Warehouse",
        "DEPOT": "Warehouse",
        
        # Material variations
        "MATERIAL": "Material No",
        "MATERIAL NO": "Material No",
        "MATERIAL NUMBER": "Material No",
        "PRODUCT": "Material No",
        "PRODUCT CODE": "Material No",
        
        # Division variations
        "DIVISION": "Division",
        "DIV": "Division",
        "BUSINESS UNIT": "Division",
        
        # Sales variations
        "SALES OFFICE": "Sales Office",
        "SALES MANAGER": "Sales Manager",
        
        # Date variations - Phase 2 expanded
        "DN DATE": "DN Create Date",
        "CREATE DATE": "DN Create Date",
        "CREATED DATE": "DN Create Date",
        "GOOD ISSUE DATE": "Good Issue Date",
        "PGI DATE": "Good Issue Date",
        "ACTUAL PGI DATE": "Good Issue Date",
        "PGI": "Good Issue Date",
        "GOODS ISSUE DATE": "Good Issue Date",        # Phase 2
        "ACTUAL GOODS ISSUE DATE": "Good Issue Date", # Phase 2
        "POD DATE": "POD Date",
        "PROOF OF DELIVERY": "POD Date",
        "PROOF OF DELIVERY DATE": "POD Date",
        "POD": "POD Date",
        
        # Quantity variations
        "QUANTITY": "DN Qty",
        "QTY": "DN Qty",
        "DN QTY": "DN Qty",
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

    # Phase 2: Expanded DN column variants with contains matching
    DN_COLUMN_VARIANTS = [
        "DN No", "DN", "Delivery No", "Delivery Number", "Delivery Document",
        "Document No", "Document", "Invoice No", "Reference No", "Order No",
        "Outbound Delivery", "Outbound Delivery Number", "Outbound Delivery No",
        "Delivery Document Number", "Doc No"
    ]
    
    # Phase 1: Weighted scoring for header detection
    HEADER_WEIGHTS = {
        "customer name": 3,
        "dealer name": 3,
        "ship to city": 3,
        "dn amount": 3,
        "warehouse": 3,
        "dn": 2,
        "delivery": 2,
        "pgi": 2,
        "pod": 2,
        "document": 1,
        "invoice": 1,
        "order": 1,
    }
    
    REQUIRED_COLUMNS = DN_COLUMN_VARIANTS

    def __init__(self, db: Session):
        self.db = db
        self.import_stats = {
            "rows_read": 0,
            "rows_removed_empty": 0,
            "rows_removed_total": 0,
            "rows_skipped_no_dn": 0,
            "rows_imported": 0,
            "columns_detected": []
        }

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
        """Import Excel file into delivery_reports table."""
        try:
            # Phase 1 & 2: Improved sheet and header detection
            df, sheet_score = self._find_and_read_sheet(file_path)
            
            if df is None:
                return {
                    "success": False,
                    "error": "Could not find a valid data sheet in Excel file",
                    "available_columns": []
                }
            
            self.import_stats["rows_read"] = len(df)
            
            # Phase 3: Preserve merged columns by converting Unnamed to Column_N
            df = self._handle_unnamed_columns(df)
            
            # Phase 3: Remove duplicate columns
            df = self._remove_duplicate_columns(df)
            
            # Debug logging
            logger.info(f"Excel Columns Found: {list(df.columns)}")
            print("=" * 70)
            print("📊 EXCEL IMPORT DEBUG")
            print("=" * 70)
            print(f"File: {source_filename}")
            print(f"Sheet Score: {sheet_score}")
            print(f"Columns Found ({len(df.columns)}): {list(df.columns)}")
            print(f"Total Rows: {len(df)}")
            
            # Phase 4: Show column confidence
            self._show_column_confidence(df)
            
            # Show first few rows
            print("\n📋 FIRST 3 ROWS OF DATA:")
            for idx, row in df.head(3).iterrows():
                print(f"  Row {idx + 1}: {dict(row)}")
            print("=" * 70)
            
            # Clean headers
            df = self._clean_headers(df)
            
            # Remove empty rows
            df = self._remove_empty_rows(df)
            
            # Remove total/summary rows
            df = self._remove_total_rows(df)
            
            # Phase 3: Remove duplicate rows
            df = self._remove_duplicate_rows(df)
            
            # Normalize column names
            df = self._normalize_columns(df)
            
            # Phase 4: Log normalized columns
            print(f"\n📋 NORMALIZED COLUMNS: {list(df.columns)}")
            
            # Smart validation
            validation_result = self._validate_columns_smart(df)
            if not validation_result["is_valid"]:
                return {
                    "success": False,
                    "error": validation_result["error"],
                    "missing_columns": validation_result.get("missing_columns", []),
                    "available_columns": validation_result.get("available_columns", [])
                }
            
            # Phase 3: Remove rows with empty DN
            df = self._remove_empty_dn_rows(df)
            
            # Transform data
            records = self._transform_data(df, source_filename, batch_id)
            
            # Phase 4: Show first record preview
            if records:
                print(f"\n🔍 FIRST RECORD PREVIEW:")
                for key, value in list(records[0].items())[:10]:
                    print(f"  {key}: {value}")
            
            # Handle duplicates
            if skip_duplicates or update_existing:
                records = self._handle_duplicates(records, update_existing)
            
            # Bulk insert
            inserted_count, updated_count, skipped_count = self._bulk_insert(
                records, 
                skip_duplicates=skip_duplicates,
                update_existing=update_existing
            )
            
            self.import_stats["rows_imported"] = inserted_count
            self.import_stats["columns_detected"] = list(df.columns)
            
            # Update derived fields
            if batch_id:
                self._update_derived_fields(batch_id)
            
            # Phase 4: Show import statistics
            self._show_import_statistics()
            
            logger.info(f"Import complete: {inserted_count} inserted, {updated_count} updated, {skipped_count} skipped")
            
            return {
                "success": True,
                "inserted_count": inserted_count,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "total_rows": len(df),
                "batch_id": batch_id,
                "source_file": source_filename,
                "available_columns": list(df.columns),
                "import_stats": self.import_stats
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
    # PHASE 1 & 2: IMPROVED SHEET AND HEADER DETECTION
    # ==========================================================

    def _find_and_read_sheet(self, file_path: str) -> Tuple[Optional[pd.DataFrame], int]:
        """Find the correct sheet using weighted scoring."""
        try:
            excel_file = pd.ExcelFile(file_path)
            best_sheet = None
            best_score = 0
            best_header_row = 0
            best_df = None
            
            for sheet_name in excel_file.sheet_names:
                # Phase 2: Search up to 50 rows for headers
                for header_row in range(50):
                    try:
                        test_df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
                        
                        # Phase 1: Weighted scoring
                        column_str = ' '.join([str(col).lower() for col in test_df.columns])
                        score = 0
                        
                        for keyword, weight in self.HEADER_WEIGHTS.items():
                            if keyword in column_str:
                                score += weight
                        
                        # Phase 1: Need score >= 8 to accept
                        if score > best_score and score >= 8:
                            # Phase 1: Also consider column count and data rows
                            column_count_score = min(len(test_df.columns) * 0.5, 5)
                            data_rows_score = min(len(test_df) * 0.1, 5)
                            total_score = score + column_count_score + data_rows_score
                            
                            if total_score > best_score:
                                best_score = total_score
                                best_sheet = sheet_name
                                best_header_row = header_row
                                best_df = test_df
                                logger.info(f"Sheet '{sheet_name}' scored {total_score:.1f} at row {header_row + 1}")
                                print(f"📊 Sheet '{sheet_name}' - Score: {total_score:.1f} at row {header_row + 1}")
                                break
                                
                    except Exception as e:
                        continue
            
            if best_df:
                print(f"✅ Selected sheet: '{best_sheet}' with score {best_score:.1f}")
                return best_df, best_score
            
            print("⚠️ No good sheet found, using first sheet")
            return pd.read_excel(file_path), 0
            
        except Exception as e:
            logger.error(f"Sheet detection failed: {e}")
            return None, 0

    # ==========================================================
    # PHASE 3: DATA CLEANING IMPROVEMENTS
    # ==========================================================

    def _handle_unnamed_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert Unnamed columns to Column_N instead of removing."""
        renamed_cols = {}
        unnamed_count = 0
        
        for col in df.columns:
            if "Unnamed" in str(col):
                renamed_cols[col] = f"Column_{unnamed_count + 1}"
                unnamed_count += 1
        
        if renamed_cols:
            df = df.rename(columns=renamed_cols)
            logger.info(f"Renamed {unnamed_count} unnamed columns")
            print(f"📝 Renamed {unnamed_count} unnamed columns to Column_1, Column_2, etc.")
        
        return df

    def _remove_duplicate_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove duplicate columns after normalization."""
        before = len(df.columns)
        df = df.loc[:, ~df.columns.duplicated()]
        after = len(df.columns)
        
        if before != after:
            logger.info(f"Removed {before - after} duplicate columns")
            print(f"🗑️ Removed {before - after} duplicate columns")
        
        return df

    def _remove_duplicate_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove duplicate rows based on all columns."""
        before = len(df)
        df = df.drop_duplicates()
        after = len(df)
        
        if before != after:
            self.import_stats["rows_removed_empty"] += (before - after)
            logger.info(f"Removed {before - after} duplicate rows")
            print(f"🗑️ Removed {before - after} duplicate rows")
        
        return df

    def _remove_empty_dn_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove rows where DN No is empty."""
        if "DN No" in df.columns:
            before = len(df)
            df = df[df["DN No"].notna()]
            after = len(df)
            
            if before != after:
                self.import_stats["rows_skipped_no_dn"] = before - after
                logger.info(f"Removed {before - after} rows with empty DN")
                print(f"🗑️ Removed {before - after} rows with empty DN")
        
        return df

    def _remove_empty_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove completely empty rows."""
        before = len(df)
        df = df.dropna(how="all")
        after = len(df)
        
        if before != after:
            self.import_stats["rows_removed_empty"] += (before - after)
            logger.info(f"Removed {before - after} empty rows")
            print(f"🗑️ Removed {before - after} empty rows")
        
        return df

    def _remove_total_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        """Remove total/summary/grand total rows."""
        before = len(df)
        
        mask = ~df.astype(str).apply(
            lambda row: row.str.contains(
                "total|summary|grand total|subtotal|grandtotal",
                case=False,
                na=False
            ).any(),
            axis=1
        )
        df = df[mask]
        
        after = len(df)
        if before != after:
            self.import_stats["rows_removed_total"] = before - after
            logger.info(f"Removed {before - after} total/summary rows")
            print(f"🗑️ Removed {before - after} total/summary rows")
        
        return df

    # ==========================================================
    # PHASE 4: IMPORT ACCURACY & LOGGING
    # ==========================================================

    def _show_column_confidence(self, df: pd.DataFrame):
        """Show confidence score for detected columns."""
        print("\n📊 COLUMN DETECTION CONFIDENCE:")
        print("-" * 40)
        
        high_value_cols = ["DN", "Customer", "City", "Amount", "Warehouse"]
        found_count = 0
        
        for col in df.columns:
            col_lower = str(col).lower()
            confidence = "✓"
            
            for hc in high_value_cols:
                if hc.lower() in col_lower:
                    confidence = "⭐"
                    found_count += 1
                    break
            
            print(f"  {confidence} {col}")
        
        print(f"\n  High-value columns found: {found_count}/{len(high_value_cols)}")
        print("-" * 40)

    def _show_import_statistics(self):
        """Show detailed import statistics."""
        print("\n" + "=" * 70)
        print("📊 IMPORT STATISTICS")
        print("=" * 70)
        print(f"  Rows Read:           {self.import_stats['rows_read']}")
        print(f"  Rows Removed (Empty):{self.import_stats['rows_removed_empty']}")
        print(f"  Rows Removed (Total):{self.import_stats['rows_removed_total']}")
        print(f"  Rows Skipped (No DN):{self.import_stats['rows_skipped_no_dn']}")
        print(f"  Rows Imported:       {self.import_stats['rows_imported']}")
        print(f"  Columns Detected:    {len(self.import_stats['columns_detected'])}")
        print("=" * 70)

    def _clean_headers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean column names."""
        df.columns = [
            str(col)
            .replace("\n", " ")
            .replace("\r", "")
            .replace("\t", " ")
            .strip()
            for col in df.columns
        ]
        return df

    # ==========================================================
    # COLUMN NORMALIZATION
    # ==========================================================

    def _normalize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize column names using aliases."""
        column_mapping = {}
        
        for col in df.columns:
            clean_col = str(col).strip()
            
            # Exact match
            if clean_col in self.COLUMN_ALIASES:
                column_mapping[col] = self.COLUMN_ALIASES[clean_col]
            else:
                # Case-insensitive match
                for alias, standard in self.COLUMN_ALIASES.items():
                    if clean_col.lower() == alias.lower():
                        column_mapping[col] = standard
                        break
                else:
                    # Phase 2: Contains matching
                    for alias, standard in self.COLUMN_ALIASES.items():
                        if alias.lower() in clean_col.lower() or clean_col.lower() in alias.lower():
                            column_mapping[col] = standard
                            break
        
        if column_mapping:
            df = df.rename(columns=column_mapping)
            logger.info(f"Normalized {len(column_mapping)} columns")
        
        return df

    # ==========================================================
    # SMART VALIDATION
    # ==========================================================

    def _validate_columns_smart(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Validate required columns with multiple DN variants."""
        available_columns = [str(col).strip() for col in df.columns]
        
        # Phase 2: Contains matching for DN
        dn_column_found = None
        for dn_variant in self.DN_COLUMN_VARIANTS:
            for available in available_columns:
                if dn_variant.lower() in available.lower() or available.lower() in dn_variant.lower():
                    dn_column_found = available
                    break
            if dn_column_found:
                break
        
        if not dn_column_found:
            return {
                "is_valid": False,
                "error": f"Missing DN column. Expected one containing: {', '.join(self.DN_COLUMN_VARIANTS[:8])}",
                "missing_columns": self.DN_COLUMN_VARIANTS[:5],
                "available_columns": available_columns[:20]
            }
        
        # Map to standard name
        if dn_column_found != "DN No":
            df.rename(columns={dn_column_found: "DN No"}, inplace=True)
            print(f"📝 Mapped '{dn_column_found}' to 'DN No'")
        
        # Log optional missing columns
        optional_missing = []
        for std_col in self.COLUMN_MAPPING.keys():
            if std_col != "DN No":
                found = False
                for available in available_columns:
                    if available.lower() == std_col.lower():
                        found = True
                        break
                if not found:
                    optional_missing.append(std_col)
        
        if optional_missing:
            print(f"⚠️ Optional columns missing ({len(optional_missing)}): {optional_missing[:5]}...")
        
        return {
            "is_valid": True,
            "optional_missing": optional_missing,
            "available_columns": available_columns,
            "dn_column_mapped": dn_column_found
        }

    # ==========================================================
    # DATA TRANSFORMATION
    # ==========================================================

    def _transform_data(
        self, 
        df: pd.DataFrame, 
        source_filename: str, 
        batch_id: int = None
    ) -> List[Dict[str, Any]]:
        """Transform Excel data to match DeliveryReport model."""
        records = []
        current_time = datetime.utcnow()
        
        for index, row in df.iterrows():
            try:
                record = {}
                
                for standard_col, db_col in self.COLUMN_MAPPING.items():
                    found_col = None
                    for col in df.columns:
                        if col.lower() == standard_col.lower():
                            found_col = col
                            break
                    
                    if found_col:
                        value = row[found_col]
                        
                        if pd.isna(value):
                            value = None
                        
                        if db_col in ["dn_create_date", "good_issue_date", "pod_date"]:
                            value = self._parse_date(value)
                        
                        if db_col in ["dn_qty", "dn_amount"]:
                            value = self._parse_numeric(value)
                        
                        record[db_col] = value
                
                if not record.get("dn_no"):
                    continue
                
                record["source_file"] = source_filename
                record["upload_batch_id"] = batch_id
                record["imported_at"] = current_time
                record["created_at"] = current_time
                record["updated_at"] = current_time
                record["delivery_location"] = self._generate_delivery_location(
                    record.get("warehouse"),
                    record.get("ship_to_city")
                )
                
                # Phase 5: Auto-detect status
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
                
                records.append(record)
                
            except Exception as e:
                logger.error(f"Error transforming row {index + 2}: {str(e)}")
                continue
        
        print(f"📊 Transformed {len(records)} records")
        return records

    # ==========================================================
    # HELPER FUNCTIONS
    # ==========================================================

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
            logger.warning(f"Failed to parse date: {value}")
            return None

    def _parse_numeric(self, value: Any) -> Optional[float]:
        """Parse numeric values safely."""
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return None
        
        try:
            if isinstance(value, str):
                value = value.replace('$', '').replace('₹', '').replace(',', '').strip()
                return float(value) if value else None
            return float(value)
        except:
            return None

    def _generate_delivery_location(self, warehouse: str, city: str) -> str:
        """Generate delivery location string."""
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
    # BULK OPERATIONS (Simplified - keep existing)
    # ==========================================================

    def _handle_duplicates(self, records: List[Dict[str, Any]], update_existing: bool = False) -> List[Dict[str, Any]]:
        """Handle duplicate DN records."""
        if not records:
            return records
        
        dn_numbers = [r.get("dn_no") for r in records if r.get("dn_no")]
        if not dn_numbers:
            return records
        
        existing_records = self.db.query(DeliveryReport).filter(
            DeliveryReport.dn_no.in_(dn_numbers)
        ).all()
        
        existing_dn_map = {r.dn_no: r for r in existing_records}
        processed_records = []
        skipped_count = 0
        
        for record in records:
            dn_no = record.get("dn_no")
            
            if dn_no and dn_no in existing_dn_map:
                if update_existing:
                    record["_existing_id"] = existing_dn_map[dn_no].id
                    processed_records.append(record)
                else:
                    skipped_count += 1
                    continue
            else:
                processed_records.append(record)
        
        if skipped_count > 0:
            print(f"📊 Skipped {skipped_count} duplicate DN(s)")
        
        return processed_records

    def _bulk_insert(self, records: List[Dict[str, Any]], skip_duplicates: bool = True, update_existing: bool = False) -> Tuple[int, int, int]:
        """Bulk insert/update records."""
        if not records:
            return 0, 0, 0
        
        inserted_count = 0
        updated_count = 0
        skipped_count = 0
        
        updates = [r for r in records if "_existing_id" in r]
        inserts = [r for r in records if "_existing_id" not in r]
        
        if update_existing and updates:
            for record in updates:
                try:
                    existing_id = record.pop("_existing_id")
                    record.pop("created_at", None)
                    record.pop("imported_at", None)
                    record["updated_at"] = datetime.utcnow()
                    
                    self.db.query(DeliveryReport).filter(DeliveryReport.id == existing_id).update(record)
                    updated_count += 1
                except Exception as e:
                    logger.error(f"Failed to update record: {str(e)}")
                    skipped_count += 1
            
            self.db.commit()
        
        if inserts:
            try:
                for record in inserts:
                    record.pop("_existing_id", None)
                
                self.db.bulk_insert_mappings(DeliveryReport, inserts)
                self.db.commit()
                inserted_count = len(inserts)
            except Exception as e:
                self.db.rollback()
                logger.error(f"Bulk insert failed: {str(e)}")
                for record in inserts:
                    try:
                        delivery = DeliveryReport(**record)
                        self.db.add(delivery)
                        self.db.commit()
                        inserted_count += 1
                    except:
                        skipped_count += 1
        
        return inserted_count, updated_count, skipped_count

    def _update_derived_fields(self, batch_id: int = None):
        """Update derived fields for a batch."""
        query = self.db.query(DeliveryReport)
        if batch_id:
            query = query.filter(DeliveryReport.upload_batch_id == batch_id)
        
        records = query.all()
        
        for record in records:
            record.delivery_location = self._generate_delivery_location(record.warehouse, record.ship_to_city)
            
            if record.pod_date:
                record.delivery_status = "Completed"
                record.pod_status = "Received"
                record.pending_flag = False
            elif record.good_issue_date:
                record.delivery_status = "In Transit"
                record.pgi_status = "Completed"
                record.pending_flag = True
            else:
                record.delivery_status = "Pending"
                record.pending_flag = True
            
            record.updated_at = datetime.utcnow()
        
        self.db.commit()
        logger.info(f"Updated derived fields for {len(records)} records")

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
    service = ExcelImportService(db)
    return service.import_excel(file_path, source_filename, batch_id, skip_duplicates, update_existing)


def get_batch_summary(db: Session, batch_id: int) -> Dict[str, Any]:
    service = ExcelImportService(db)
    return service.get_import_summary(batch_id)


def delete_import_batch(db: Session, batch_id: int) -> Dict[str, Any]:
    service = ExcelImportService(db)
    return service.delete_batch(batch_id)
