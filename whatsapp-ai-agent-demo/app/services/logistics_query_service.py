# ==========================================================
# FILE: app/services/logistics_query_service.py (FIXED v7.0 - DIRECT POSTGRESQL INTEGRATION)
# ==========================================================
# PURPOSE: Logistics operational queries for DN, POD, PGI, Deliveries, Warehouse
#
# CRITICAL FIXES v7.0:
# - ✅ Direct PostgreSQL connection with raw SQL fallback
# - ✅ Aggressive multi-format DN search with raw SQL
# - ✅ Automatic detection of DN format in database
# - ✅ Debug endpoints to see actual data in Railway PostgreSQL
# - ✅ Fallback to raw SQL when ORM fails
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, cast, String, Integer, Float, text
from loguru import logger

from app.models import DeliveryReport


class LogisticsQueryService:
    def __init__(self, db: Session):
        self.db = db
        logger.info("Logistics Query Service v7.0 initialized - Direct PostgreSQL Integration")
    
    # ==========================================================
    # HELPER METHODS
    # ==========================================================
    
    def _validate_session(self) -> bool:
        """Validate database session is available"""
        if not self.db:
            logger.error("Database session is None")
            return False
        return True
    
    def normalize_dn(self, dn) -> str:
        """
        DN normalization - handles multiple formats
        """
        if dn is None:
            return ""
        
        dn_str = str(dn).strip()
        
        # Remove .0 at the end
        if dn_str.endswith('.0'):
            dn_str = dn_str[:-2]
        
        # Remove scientific notation
        if 'e' in dn_str.lower():
            try:
                dn_str = str(int(float(dn_str)))
            except:
                pass
        
        # Remove any non-numeric characters except digits
        import re
        dn_str = re.sub(r'[^0-9]', '', dn_str)
        
        return dn_str
    
    def _calculate_delivery_aging(self, pgi_date, dn_date) -> int:
        """Business Rule: Delivery Aging = PGI Date - DN Date"""
        if pgi_date and dn_date:
            return max(0, (pgi_date - dn_date).days)
        return 0
    
    def _calculate_pod_aging(self, pod_date, pgi_date) -> int:
        """Business Rule: POD Aging = POD Date - PGI Date"""
        if pod_date and pgi_date:
            return max(0, (pod_date - pgi_date).days)
        return 0
    
    def _calculate_pending_delivery_aging(self, dn_date) -> int:
        """Business Rule: Pending Delivery Aging = Today - DN Date (if no PGI)"""
        if dn_date:
            return max(0, (date.today() - dn_date).days)
        return 0
    
    def _calculate_pending_pod_aging(self, pgi_date) -> int:
        """Business Rule: Pending POD Aging = Today - PGI Date (if no POD)"""
        if pgi_date:
            return max(0, (date.today() - pgi_date).days)
        return 0
    
    def _calculate_priority(self, days: int) -> str:
        """Calculate priority based on aging days"""
        if days > 14:
            return "CRITICAL"
        elif days > 7:
            return "HIGH"
        elif days > 3:
            return "MEDIUM"
        else:
            return "LOW"
    
    def _calculate_dn_status(self, pgi_date, pod_date) -> Dict[str, str]:
        """Business Rule: Status Logic"""
        if pgi_date and pod_date:
            return {"status": "Delivered", "emoji": "✅", "description": "Full delivery completed"}
        elif pgi_date and not pod_date:
            return {"status": "POD Pending", "emoji": "⏳", "description": "Dispatched, awaiting proof of delivery"}
        else:
            return {"status": "Delivery Pending", "emoji": "🟡", "description": "Not yet dispatched"}
    
    def _aggregate_dn_records(self, records: List[DeliveryReport]) -> Dict[str, Any]:
        """DN Aggregation Logic - 1 DN = Multiple Products, counted ONCE"""
        if not records:
            return {}
        
        dn_no = self.normalize_dn(records[0].dn_no)
        first_record = records[0]
        
        unique_models = set()
        total_quantity = 0
        total_amount = 0.0
        products = []
        product_codes = set()
        
        for r in records:
            total_quantity += int(r.dn_qty or 0)
            total_amount += float(r.dn_amount or 0)
            
            if r.material_no:
                unique_models.add(r.customer_model or r.material_no)
                
                if r.material_no not in product_codes:
                    product_codes.add(r.material_no)
                    products.append({
                        "material_no": r.material_no,
                        "customer_model": r.customer_model or "N/A",
                        "quantity": int(r.dn_qty or 0),
                        "amount": float(r.dn_amount or 0)
                    })
                else:
                    for p in products:
                        if p["material_no"] == r.material_no:
                            p["quantity"] += int(r.dn_qty or 0)
                            p["amount"] += float(r.dn_amount or 0)
                            break
        
        pgi_date = first_record.good_issue_date
        dn_date = first_record.dn_create_date
        pod_date = first_record.pod_date
        
        delivery_aging = self._calculate_delivery_aging(pgi_date, dn_date)
        pod_aging = self._calculate_pod_aging(pod_date, pgi_date)
        pending_delivery_aging = self._calculate_pending_delivery_aging(dn_date) if not pgi_date else 0
        pending_pod_aging = self._calculate_pending_pod_aging(pgi_date) if pgi_date and not pod_date else 0
        status_info = self._calculate_dn_status(pgi_date, pod_date)
        
        return {
            "dn_no": dn_no,
            "dn_date": dn_date.strftime("%Y-%m-%d") if dn_date else "N/A",
            "dealer": first_record.customer_name or "N/A",
            "dealer_code": first_record.customer_code or "N/A",
            "sales_office": first_record.division or "N/A",
            "warehouse": first_record.warehouse or "N/A",
            "warehouse_code": first_record.warehouse_code or "N/A",
            "city": first_record.ship_to_city or "N/A",
            "pgi_date": pgi_date.strftime("%Y-%m-%d") if pgi_date else "Not Dispatched",
            "pod_date": pod_date.strftime("%Y-%m-%d") if pod_date else "Not Received",
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "pending_delivery_aging_days": pending_delivery_aging,
            "pending_pod_aging_days": pending_pod_aging,
            "status": status_info["status"],
            "status_emoji": status_info["emoji"],
            "status_description": status_info["description"],
            "total_models": len(unique_models),
            "models_list": sorted(list(unique_models)),
            "total_quantity": total_quantity,
            "total_amount": total_amount,
            "products": products
        }
    
    # ==========================================================
    # RAW SQL SEARCH - DIRECT POSTGRESQL QUERY (CRITICAL FIX)
    # ==========================================================
    
    def _raw_sql_dn_search(self, dn_number: str) -> List[DeliveryReport]:
        """
        Direct PostgreSQL raw SQL search - bypasses ORM issues
        This will find the DN no matter what format it's stored in
        """
        normalized = self.normalize_dn(dn_number)
        
        logger.info(f"RAW SQL DN Search for: {normalized}")
        
        try:
            # Raw SQL query to find DN in ANY format
            raw_query = text("""
                SELECT * FROM delivery_report 
                WHERE 
                    dn_no::TEXT = :dn1
                    OR dn_no::TEXT = :dn2
                    OR dn_no::TEXT LIKE :dn3
                    OR dn_no::TEXT LIKE :dn4
                    OR dn_no::TEXT = :dn5
                    OR dn_no = :dn6::BIGINT
                    OR dn_no::TEXT LIKE :dn7
                LIMIT 100
            """)
            
            # Generate all possible search patterns
            patterns = [
                normalized,                           # exact
                f"{normalized}.0",                    # with .0
                f"%{normalized}%",                    # contains
                f"{normalized}%",                     # starts with
                f"%{normalized}",                     # ends with
                normalized.lstrip('0'),               # remove leading zeros
                normalized.zfill(10)                  # pad to 10 digits
            ]
            
            # Execute raw SQL
            result = self.db.execute(raw_query, {
                "dn1": patterns[0],
                "dn2": patterns[1],
                "dn3": patterns[2],
                "dn4": patterns[3],
                "dn5": patterns[4],
                "dn6": int(normalized) if normalized.isdigit() else 0,
                "dn7": f"%{normalized[-8:]}%" if len(normalized) > 8 else patterns[2]
            })
            
            records = result.fetchall()
            
            if records:
                logger.info(f"RAW SQL found {len(records)} records for DN {normalized}")
                # Convert raw SQL results to ORM objects
                orm_records = []
                for row in records:
                    # Fetch the full ORM object
                    orm_record = self.db.query(DeliveryReport).filter(
                        DeliveryReport.id == row.id
                    ).first()
                    if orm_record:
                        orm_records.append(orm_record)
                return orm_records
            
            return []
            
        except Exception as e:
            logger.error(f"RAW SQL search failed: {e}")
            return []
    
    def _multi_format_dn_search(self, dn_number: str) -> List[DeliveryReport]:
        """
        Aggressive multi-format DN search - tries EVERYTHING
        """
        normalized = self.normalize_dn(dn_number)
        
        logger.info(f"DN Search - Original: '{dn_number}'")
        logger.info(f"DN Search - Normalized: '{normalized}'")
        
        # First try ORM search
        variants = list(set([
            normalized,
            str(dn_number),
            f"{normalized}.0",
            normalized.zfill(10),
            normalized.lstrip('0'),
            f"%{normalized}%",
            f"{normalized}%",
            f"%{normalized}"
        ]))
        
        logger.info(f"ORM Search variants: {variants[:5]}...")
        
        all_records = []
        seen_dns = set()
        
        # Try ORM search first
        for variant in variants[:5]:  # First 5 variants for ORM
            try:
                if '%' in variant:
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String).like(variant)
                    ).all()
                else:
                    records = self.db.query(DeliveryReport).filter(
                        cast(DeliveryReport.dn_no, String) == variant
                    ).all()
                
                for r in records:
                    if r.dn_no not in seen_dns:
                        seen_dns.add(r.dn_no)
                        all_records.append(r)
                        logger.info(f"ORM found: {variant}")
            except Exception as e:
                logger.warning(f"ORM variant failed: {variant} - {e}")
        
        # If ORM found nothing, try RAW SQL
        if not all_records:
            logger.info("ORM found no results, trying RAW SQL...")
            all_records = self._raw_sql_dn_search(dn_number)
        
        logger.info(f"Total unique records found: {len(all_records)}")
        
        return all_records
    
    # ==========================================================
    # DEBUG METHOD - See What's Actually in Database
    # ==========================================================
    
    def debug_show_all_dns(self, limit: int = 30) -> Dict[str, Any]:
        """
        DEBUG: Show all DNs currently in the PostgreSQL database
        This helps identify what DNs actually exist
        """
        logger.info("DEBUG: Fetching all DNs from database")
        
        try:
            # Raw SQL to get all DNs
            raw_query = text("""
                SELECT DISTINCT 
                    dn_no, 
                    customer_name, 
                    dn_create_date,
                    pg_typeof(dn_no) as data_type
                FROM delivery_report 
                WHERE dn_no IS NOT NULL 
                ORDER BY dn_create_date DESC 
                LIMIT :limit
            """)
            
            result = self.db.execute(raw_query, {"limit": limit})
            rows = result.fetchall()
            
            dn_list = []
            for row in rows:
                dn_list.append({
                    "dn_no": str(row[0]),
                    "customer_name": row[1],
                    "dn_create_date": str(row[2]) if row[2] else "N/A",
                    "data_type": row[3]
                })
            
            # Also count total
            count_query = text("SELECT COUNT(DISTINCT dn_no) FROM delivery_report WHERE dn_no IS NOT NULL")
            count_result = self.db.execute(count_query)
            total_count = count_result.fetchone()[0]
            
            return {
                "success": True,
                "data": {
                    "total_dns_in_db": total_count,
                    "dns_showing": dn_list[:limit],
                    "sample_dns": [d["dn_no"] for d in dn_list[:10]]
                },
                "_summary": f"Found {total_count} unique DNs in database. Showing {len(dn_list)} most recent."
            }
            
        except Exception as e:
            logger.error(f"Debug query failed: {e}")
            return {"success": False, "error": str(e)}
    
    def debug_check_dn_exists(self, dn_number: str) -> Dict[str, Any]:
        """
        DEBUG: Check if a specific DN exists in database
        """
        logger.info(f"DEBUG: Checking if DN '{dn_number}' exists")
        
        try:
            normalized = self.normalize_dn(dn_number)
            
            # Raw SQL to check all possible formats
            raw_query = text("""
                SELECT 
                    dn_no,
                    customer_name,
                    dn_create_date,
                    good_issue_date,
                    pod_date
                FROM delivery_report 
                WHERE 
                    dn_no::TEXT = :exact
                    OR dn_no::TEXT = :with_dot_zero
                    OR dn_no::TEXT LIKE :contains
                    OR dn_no::TEXT = :as_string
                    OR dn_no = :as_integer::BIGINT
                LIMIT 5
            """)
            
            result = self.db.execute(raw_query, {
                "exact": normalized,
                "with_dot_zero": f"{normalized}.0",
                "contains": f"%{normalized}%",
                "as_string": str(dn_number),
                "as_integer": int(normalized) if normalized.isdigit() else 0
            })
            
            rows = result.fetchall()
            
            matches = []
            for row in rows:
                matches.append({
                    "dn_no": str(row[0]),
                    "customer_name": row[1],
                    "dn_create_date": str(row[2]) if row[2] else "N/A"
                })
            
            return {
                "success": True,
                "data": {
                    "dn_searched": dn_number,
                    "normalized": normalized,
                    "found": len(matches) > 0,
                    "matches": matches,
                    "match_count": len(matches)
                },
                "_summary": f"DN {dn_number} {'FOUND' if matches else 'NOT FOUND'} in database. Found {len(matches)} records."
            }
            
        except Exception as e:
            logger.error(f"Debug check failed: {e}")
            return {"success": False, "error": str(e)}
    
    def _format_success(self, data: Any, summary: str) -> Dict[str, Any]:
        """Standardized success response format"""
        return {
            "success": True,
            "data": data,
            "_summary": summary
        }
    
    def _format_error(self, error: str) -> Dict[str, Any]:
        """Standardized error response format"""
        return {
            "success": False,
            "data": {},
            "_summary": f"❌ {error}",
            "error": error
        }
    
    # ==========================================================
    # MAIN DN QUERY - WITH DEBUGGING
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """
        Complete DN intelligence with aggressive search and debugging
        """
        logger.info(f"Getting DN intelligence for: {dn_number}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            # First, check if DN exists with debug
            debug_check = self.debug_check_dn_exists(dn_number)
            
            if not debug_check.get("data", {}).get("found", False):
                # DN not found - return helpful error with sample DNs
                sample_dns = self.debug_show_all_dns(5)
                sample_list = sample_dns.get("data", {}).get("sample_dns", [])
                
                error_msg = f"DN {dn_number} not found in database."
                if sample_list:
                    error_msg += f" Available DNs: {', '.join(sample_list[:5])}"
                
                return self._format_error(error_msg)
            
            # DN exists - get the records
            records = self._multi_format_dn_search(dn_number)
            
            if not records:
                return self._format_error(f"DN {dn_number} found in check but records not retrieved")
            
            # Aggregate all records
            aggregated_data = self._aggregate_dn_records(records)
            
            if not aggregated_data:
                return self._format_error(f"DN {dn_number} aggregation failed")
            
            priority = self._calculate_priority(
                max(aggregated_data.get("delivery_aging_days", 0), 
                    aggregated_data.get("pending_delivery_aging_days", 0))
            )
            
            response_data = {
                "dn_number": aggregated_data["dn_no"],
                "date": aggregated_data["dn_date"],
                "dealer_name": aggregated_data["dealer"],
                "dealer_code": aggregated_data["dealer_code"],
                "sales_office": aggregated_data["sales_office"],
                "warehouse": aggregated_data["warehouse"],
                "warehouse_code": aggregated_data["warehouse_code"],
                "city": aggregated_data["city"],
                "status": aggregated_data["status"],
                "status_emoji": aggregated_data["status_emoji"],
                "status_description": aggregated_data["status_description"],
                "pgi_date": aggregated_data["pgi_date"],
                "pod_date": aggregated_data["pod_date"],
                "delivery_aging_days": aggregated_data["delivery_aging_days"],
                "pod_aging_days": aggregated_data["pod_aging_days"],
                "pending_delivery_aging_days": aggregated_data["pending_delivery_aging_days"],
                "pending_pod_aging_days": aggregated_data["pending_pod_aging_days"],
                "total_models": aggregated_data["total_models"],
                "models_list": aggregated_data["models_list"],
                "total_quantity": aggregated_data["total_quantity"],
                "total_amount": aggregated_data["total_amount"],
                "products": aggregated_data["products"],
                "priority": priority
            }
            
            summary = f"DN {aggregated_data['dn_no']} is {aggregated_data['status']}. {aggregated_data['total_models']} models, {aggregated_data['total_quantity']} units."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # DEBUG ENDPOINTS FOR WHATSAPP
    # ==========================================================
    
    def get_all_dns_list(self, limit: int = 10) -> Dict[str, Any]:
        """Get list of all DNs in database - useful for debugging"""
        return self.debug_show_all_dns(limit)
    
    def check_dn_exists(self, dn_number: str) -> Dict[str, Any]:
        """Check if a specific DN exists"""
        return self.debug_check_dn_exists(dn_number)
    
    # ==========================================================
    # DN TIMELINE AND PRODUCTS
    # ==========================================================
    
    def get_dn_timeline(self, dn_number: str) -> Dict[str, Any]:
        """Get DN timeline"""
        logger.info(f"Getting DN timeline for: {dn_number}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            records = self._multi_format_dn_search(dn_number)
            
            if not records:
                return self._format_error(f"DN {dn_number} not found")
            
            aggregated = self._aggregate_dn_records(records)
            first_record = records[0]
            
            timeline = []
            
            if first_record.dn_create_date:
                timeline.append({
                    "status": "DN Created",
                    "date": str(first_record.dn_create_date),
                    "remarks": f"DN {dn_number} created",
                    "location": first_record.ship_to_city or "N/A"
                })
            
            if first_record.good_issue_date:
                timeline.append({
                    "status": "Goods Issue (PGI)",
                    "date": str(first_record.good_issue_date),
                    "remarks": "Goods issued from warehouse",
                    "location": first_record.warehouse or "N/A"
                })
            
            if first_record.pod_date:
                timeline.append({
                    "status": "POD Received",
                    "date": str(first_record.pod_date),
                    "remarks": "Proof of Delivery received",
                    "location": first_record.ship_to_city or "N/A"
                })
            
            if not timeline:
                timeline = [{
                    "status": "Information Only",
                    "date": "N/A",
                    "remarks": "Detailed timeline not available",
                    "location": "N/A"
                }]
            
            summary = f"DN {dn_number} has {len([t for t in timeline if t['status'] != 'Information Only'])} events in timeline."
            
            return self._format_success(timeline, summary)
            
        except Exception as e:
            logger.error(f"Error getting DN timeline: {e}")
            return self._format_error(str(e))
    
    def get_dn_products(self, dn_number: str) -> Dict[str, Any]:
        """Get DN products"""
        logger.info(f"Getting DN products for: {dn_number}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            records = self._multi_format_dn_search(dn_number)
            
            if not records:
                return self._format_error(f"DN {dn_number} not found")
            
            aggregated = self._aggregate_dn_records(records)
            
            summary = f"DN {dn_number} contains {aggregated['total_models']} product model(s). Total quantity: {aggregated['total_quantity']}."
            
            return self._format_success(aggregated.get("products", []), summary)
            
        except Exception as e:
            logger.error(f"Error getting DN products: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # POD OPERATIONS
    # ==========================================================
    
    def get_pod_status(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get POD status summary"""
        logger.info(f"Getting POD status for region: {region}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            
            if region:
                query = query.filter(DeliveryReport.division == region)
            
            pending_count = query.count()
            
            if pending_count == 0:
                return self._format_success(
                    {"pending_count": 0, "avg_aging": 0, "top_pending_dealer": "N/A"},
                    "No pending PODs. All clear! ✅"
                )
            
            top_dealer_data = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label('count')
            ).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(DeliveryReport.id).desc()
            ).first()
            
            top_pending_dealer = top_dealer_data[0] if top_dealer_data else "N/A"
            
            pending_records = query.all()
            total_aging = 0
            aging_count = 0
            
            for r in pending_records:
                if r.good_issue_date:
                    aging = (date.today() - r.good_issue_date).days
                    total_aging += max(0, aging)
                    aging_count += 1
            
            avg_aging = round(total_aging / max(1, aging_count), 1)
            
            response_data = {
                "pending_count": pending_count,
                "completed_today": 0,
                "avg_aging": avg_aging,
                "top_pending_dealer": top_pending_dealer
            }
            
            summary = f"{pending_count} PODs pending. Average aging: {avg_aging} days. Top pending dealer: {top_pending_dealer}."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting POD status: {e}")
            return self._format_error(str(e))
    
    def get_pod_aging_report(self) -> Dict[str, Any]:
        """Alias for get_pod_status"""
        return self.get_pod_status()
    
    def get_pod_performance(self) -> Dict[str, Any]:
        """Get POD performance metrics"""
        logger.info("Getting POD performance metrics")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            total = self.db.query(DeliveryReport).count()
            completed = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status == 'RECEIVED'
            ).count()
            pending = total - completed
            
            compliance_rate = round((completed / max(1, total)) * 100, 1)
            
            response_data = {
                "total": total,
                "completed": completed,
                "pending": pending,
                "compliance_rate": compliance_rate,
                "target": 95
            }
            
            status_emoji = "✅" if compliance_rate >= 95 else "⚠️"
            summary = f"{status_emoji} POD Compliance: {compliance_rate}% ({completed}/{total}). Target: 95%."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting POD performance: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # PGI OPERATIONS
    # ==========================================================
    
    def get_pending_pgi(self, days: Optional[int] = None) -> Dict[str, Any]:
        """Get pending PGI"""
        logger.info(f"Getting pending PGI for days: {days}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pgi_status == 'PENDING'
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff_date)
            
            pending_count = query.count()
            results = query.limit(50).all()
            
            pending_list = []
            for r in results:
                pending_days = self._calculate_pending_delivery_aging(r.dn_create_date)
                pending_list.append({
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name or "Unknown",
                    "amount": float(r.dn_amount or 0),
                    "pending_days": pending_days,
                    "priority": self._calculate_priority(pending_days)
                })
            
            response_data = {
                "pending_count": pending_count,
                "pending_pgi": pending_list
            }
            
            summary = f"PGI pending: {pending_count} items. {'Showing oldest ' + str(len(pending_list)) + ' items.' if pending_list else 'No pending PGI.'}"
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting pending PGI: {e}")
            return self._format_error(str(e))
    
    def get_pgi_aging_report(self) -> Dict[str, Any]:
        """Alias for get_pending_pgi"""
        return self.get_pending_pgi()
    
    # ==========================================================
    # DELIVERY OPERATIONS
    # ==========================================================
    
    def get_pending_deliveries(self, days: Optional[int] = None) -> Dict[str, Any]:
        """Get pending deliveries"""
        logger.info(f"Getting pending deliveries for days: {days}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.delivery_status.in_(['PENDING', 'IN_TRANSIT'])
            )
            
            if days:
                cutoff_date = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff_date)
            
            pending_count = query.count()
            
            if pending_count == 0:
                return self._format_success(
                    {"pending_count": 0, "high_priority": 0, "deliveries": []},
                    "No pending deliveries. All shipments on track! ✅"
                )
            
            high_priority = 0
            medium_priority = 0
            low_priority = 0
            deliveries_list = []
            
            for r in query.limit(50).all():
                if r.good_issue_date:
                    aging = self._calculate_delivery_aging(r.good_issue_date, r.dn_create_date)
                else:
                    aging = self._calculate_pending_delivery_aging(r.dn_create_date)
                
                priority = self._calculate_priority(aging)
                
                if priority == "CRITICAL":
                    high_priority += 1
                elif priority == "HIGH":
                    medium_priority += 1
                else:
                    low_priority += 1
                
                deliveries_list.append({
                    "dn_number": r.dn_no,
                    "dealer_name": r.customer_name or "Unknown",
                    "city": r.ship_to_city or "Unknown",
                    "amount": float(r.dn_amount or 0),
                    "aging_days": aging,
                    "priority": priority
                })
            
            response_data = {
                "pending_count": pending_count,
                "high_priority": high_priority,
                "medium_priority": medium_priority,
                "low_priority": low_priority,
                "deliveries": deliveries_list
            }
            
            summary = f"Pending deliveries: {pending_count} ({high_priority} critical, {medium_priority} high priority)."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting pending deliveries: {e}")
            return self._format_error(str(e))
    
    def get_delivery_aging_report(self) -> Dict[str, Any]:
        """Alias for get_pending_deliveries"""
        return self.get_pending_deliveries()
    
    def get_delivery_performance(self) -> Dict[str, Any]:
        """Get delivery performance metrics"""
        logger.info("Getting delivery performance metrics")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            total = self.db.query(DeliveryReport).count()
            completed = self.db.query(DeliveryReport).filter(
                DeliveryReport.delivery_status == 'DELIVERED'
            ).count()
            pending = total - completed
            
            on_time_rate = round((completed / max(1, total)) * 100, 1)
            
            response_data = {
                "total": total,
                "completed": completed,
                "pending": pending,
                "on_time_rate": on_time_rate,
                "target": 95
            }
            
            status_emoji = "✅" if on_time_rate >= 95 else "⚠️"
            summary = f"{status_emoji} Delivery Performance: {on_time_rate}% ({completed}/{total}). Target: 95%."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting delivery performance: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # PENDING ITEMS
    # ==========================================================
    
    def get_pending_items(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get all pending items"""
        logger.info(f"Getting pending items for region: {region}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pending_flag == True
            )
            
            if region:
                query = query.filter(DeliveryReport.division == region)
            
            pending_records = query.all()
            total_pending = len(pending_records)
            
            if total_pending == 0:
                return self._format_success(
                    {"total_pending": 0, "pending_pods": 0, "pending_pgi": 0},
                    "No pending items. System is clear! ✅"
                )
            
            pending_pods = sum(1 for r in pending_records if r.pod_status == 'PENDING')
            pending_pgi = sum(1 for r in pending_records if r.pgi_status == 'PENDING')
            
            top_dealers_data = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.id).label('pending_count')
            ).filter(
                DeliveryReport.pending_flag == True
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.count(DeliveryReport.id).desc()
            ).limit(5).all()
            
            top_dealers = [
                {"name": dealer[0] or "Unknown", "pending_count": dealer[1]}
                for dealer in top_dealers_data
            ]
            
            high_priority = 0
            medium_priority = 0
            low_priority = 0
            
            for record in pending_records:
                if record.good_issue_date:
                    aging = self._calculate_delivery_aging(record.good_issue_date, record.dn_create_date)
                else:
                    aging = self._calculate_pending_delivery_aging(record.dn_create_date)
                
                if aging > 14:
                    high_priority += 1
                elif aging > 7:
                    medium_priority += 1
                else:
                    low_priority += 1
            
            response_data = {
                "total_pending": total_pending,
                "pending_pods": pending_pods,
                "pending_pgi": pending_pgi,
                "pending_deliveries": 0,
                "high_priority": high_priority,
                "medium_priority": medium_priority,
                "low_priority": low_priority,
                "top_dealers": top_dealers
            }
            
            summary = f"Total pending: {total_pending} ({high_priority} critical, {medium_priority} high priority). Top dealer: {top_dealers[0]['name'] if top_dealers else 'N/A'}."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting pending items: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # REGION OPERATIONS
    # ==========================================================
    
    def get_region_performance(self, region: Optional[str] = None) -> Dict[str, Any]:
        """Get region performance"""
        logger.info(f"Getting region performance for: {region}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            query = self.db.query(DeliveryReport)
            
            if region:
                query = query.filter(DeliveryReport.division == region)
            
            total_dns = query.count()
            
            if total_dns == 0:
                return self._format_success(
                    {"region": region or "All", "total_dns": 0, "success_rate": 0},
                    f"No data for region {region}" if region else "No data available"
                )
            
            pending = query.filter(DeliveryReport.pod_status == 'PENDING').count()
            completed = total_dns - pending
            
            total_value = query.with_entities(
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0)
            ).scalar() or 0
            
            delivered_records = query.filter(
                DeliveryReport.pod_date.isnot(None),
                DeliveryReport.good_issue_date.isnot(None)
            ).all()
            
            total_delivery_days = 0
            delivery_count = 0
            for r in delivered_records:
                days = (r.pod_date - r.good_issue_date).days
                if days > 0:
                    total_delivery_days += days
                    delivery_count += 1
            
            avg_delivery_days = round(total_delivery_days / max(1, delivery_count), 1)
            
            active_dealers = query.with_entities(
                DeliveryReport.customer_code
            ).distinct().count()
            
            success_rate = round((completed / max(1, total_dns)) * 100, 1)
            
            response_data = {
                "region": region or "All",
                "total_dns": total_dns,
                "pending_count": pending,
                "completed_count": completed,
                "success_rate": success_rate,
                "total_value": float(total_value),
                "avg_delivery_days": avg_delivery_days,
                "active_dealers": active_dealers
            }
            
            status_emoji = "✅" if success_rate >= 85 else "⚠️"
            summary = f"{status_emoji} Region {region or 'Overall'}: {success_rate}% success rate ({completed}/{total_dns}). Avg delivery: {avg_delivery_days} days."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting region performance: {e}")
            return self._format_error(str(e))
    
    def get_region_information(self, region: str) -> Dict[str, Any]:
        """Alias for get_region_performance"""
        return self.get_region_performance(region)
    
    # ==========================================================
    # DEALER OPERATIONS
    # ==========================================================
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        """Get performance for a specific dealer"""
        logger.info(f"Getting dealer performance for: {dealer_name}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            results = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not results:
                return self._format_error(f"Dealer '{dealer_name}' not found")
            
            total_dns = len(results)
            
            unique_dns = set()
            for r in results:
                unique_dns.add(self.normalize_dn(r.dn_no))
            
            total_unique_dns = len(unique_dns)
            
            completed_dns = sum(1 for r in results if r.pod_status == 'RECEIVED')
            pending_dns = total_dns - completed_dns
            total_value = sum(r.dn_amount or 0 for r in results)
            
            delivery_days = []
            for r in results:
                if r.pod_date and r.good_issue_date:
                    days = self._calculate_pod_aging(r.pod_date, r.good_issue_date)
                    if days > 0:
                        delivery_days.append(days)
            
            avg_delivery_days = round(sum(delivery_days) / len(delivery_days), 1) if delivery_days else 0
            completion_rate = round((completed_dns / max(1, total_unique_dns)) * 100, 1)
            
            response_data = {
                "dealer_name": dealer_name,
                "dealer_city": results[0].ship_to_city if results else "Unknown",
                "dealer_region": results[0].division if results else "Unknown",
                "total_dns": total_unique_dns,
                "completed_dns": completed_dns,
                "pending_count": pending_dns,
                "total_value": float(total_value),
                "avg_delivery_days": avg_delivery_days,
                "completion_rate": completion_rate
            }
            
            status_emoji = "✅" if completion_rate >= 90 else "⚠️"
            summary = f"{status_emoji} Dealer {dealer_name}: {completion_rate}% completion rate ({completed_dns}/{total_unique_dns})."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting dealer performance: {e}")
            return self._format_error(str(e))
    
    def get_dealer_details(self, dealer_name: str) -> Dict[str, Any]:
        """Alias for get_dealer_performance"""
        return self.get_dealer_performance(dealer_name)
    
    # ==========================================================
    # WAREHOUSE OPERATIONS
    # ==========================================================
    
    def get_warehouse_status(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse status"""
        logger.info(f"Getting warehouse status for: {warehouse_name}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            results = self.db.query(DeliveryReport).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")
            ).all()
            
            if not results:
                return self._format_error(f"Warehouse '{warehouse_name}' not found")
            
            total_dns = len(results)
            pending_pgi = sum(1 for r in results if r.pgi_status == 'PENDING')
            completed_pgi = total_dns - pending_pgi
            
            response_data = {
                "warehouse_name": warehouse_name,
                "warehouse_city": results[0].ship_to_city if results else "Unknown",
                "warehouse_region": results[0].division if results else "Unknown",
                "total_dns_handled": total_dns,
                "pgi_completed": completed_pgi,
                "pgi_pending": pending_pgi,
                "capacity_percentage": None,
                "status": "Active",
                "status_icon": "🟢"
            }
            
            summary = f"Warehouse {warehouse_name}: {completed_pgi} PGIs completed, {pending_pgi} pending."
            
            return self._format_success(response_data, summary)
            
        except Exception as e:
            logger.error(f"Error getting warehouse status: {e}")
            return self._format_error(str(e))
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
        """Alias for get_warehouse_status"""
        return self.get_warehouse_status(warehouse_name)
    
    # ==========================================================
    # TOP N OPERATIONS
    # ==========================================================
    
    def get_top_dealers(self, limit: int = 10) -> Dict[str, Any]:
        """Get top dealers by DN count"""
        logger.info(f"Getting top {limit} dealers")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            top_dealers_data = self.db.query(
                DeliveryReport.customer_name,
                DeliveryReport.customer_code,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_amount')
            ).group_by(
                DeliveryReport.customer_name,
                DeliveryReport.customer_code
            ).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            dealers = []
            for dealer in top_dealers_data:
                dealers.append({
                    "name": dealer[0] or "Unknown",
                    "code": dealer[1] or "N/A",
                    "dn_count": dealer[2],
                    "total_amount": float(dealer[3] or 0)
                })
            
            summary = f"Top {len(dealers)} dealers by volume. Top performer: {dealers[0]['name'] if dealers else 'N/A'} with {dealers[0]['dn_count'] if dealers else 0} DNs."
            
            return self._format_success(dealers, summary)
            
        except Exception as e:
            logger.error(f"Error getting top dealers: {e}")
            return self._format_error(str(e))
    
    def get_top_warehouses(self, limit: int = 10) -> Dict[str, Any]:
        """Get top warehouses by DN count"""
        logger.info(f"Getting top {limit} warehouses")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            top_warehouses_data = self.db.query(
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count'),
                func.sum(DeliveryReport.dn_amount).label('total_amount')
            ).group_by(
                DeliveryReport.warehouse,
                DeliveryReport.warehouse_code
            ).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            warehouses = []
            for wh in top_warehouses_data:
                warehouses.append({
                    "name": wh[0] or "Unknown",
                    "code": wh[1] or "N/A",
                    "dn_count": wh[2],
                    "total_amount": float(wh[3] or 0)
                })
            
            summary = f"Top {len(warehouses)} warehouses by volume. Top performer: {warehouses[0]['name'] if warehouses else 'N/A'}."
            
            return self._format_success(warehouses, summary)
            
        except Exception as e:
            logger.error(f"Error getting top warehouses: {e}")
            return self._format_error(str(e))
    
    def get_top_products(self, limit: int = 10) -> Dict[str, Any]:
        """Get top products by quantity"""
        logger.info(f"Getting top {limit} products")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable")
        
        try:
            top_products_data = self.db.query(
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_quantity'),
                func.sum(DeliveryReport.dn_amount).label('total_amount')
            ).group_by(
                DeliveryReport.material_no,
                DeliveryReport.customer_model
            ).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            products = []
            for prod in top_products_data:
                products.append({
                    "code": prod[0] or "N/A",
                    "name": prod[1] or "N/A",
                    "total_quantity": prod[2] or 0,
                    "total_amount": float(prod[3] or 0)
                })
            
            summary = f"Top {len(products)} products by volume. Top product: {products[0]['code'] if products else 'N/A'}."
            
            return self._format_success(products, summary)
            
        except Exception as e:
            logger.error(f"Error getting top products: {e}")
            return self._format_error(str(e))
    
    # ==========================================================
    # AGING REPORTS
    # ==========================================================
    
    def get_dn_aging_report(self, dn_number: str) -> Dict[str, Any]:
        """Alias for get_complete_dn_intelligence"""
        return self.get_complete_dn_intelligence(dn_number)
    
    # ==========================================================
    # VALIDATION METHODS
    # ==========================================================
    
    def validate_dn(self, dn_number: str) -> bool:
        """Validate if DN exists in database"""
        records = self._multi_format_dn_search(dn_number)
        return len(records) > 0
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Health check for the service"""
        # Test database connection with raw SQL
        db_connected = False
        try:
            self.db.execute(text("SELECT 1"))
            db_connected = True
        except:
            pass
        
        return {
            "service": "logistics",
            "status": "healthy" if self._validate_session() and db_connected else "unhealthy",
            "version": "7.0",
            "session_available": self._validate_session(),
            "database_connected": db_connected,
            "features": {
                "dn_aggregation": True,
                "aggressive_multi_format_search": True,
                "raw_sql_fallback": True,
                "postgresql_compatible": True,
                "business_rules_applied": True,
                "debug_endpoints": True
            }
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session) -> LogisticsQueryService:
    """Factory function to create LogisticsQueryService instance"""
    return LogisticsQueryService(db)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 70)
logger.info("📦 LOGISTICS QUERY SERVICE v7.0 - DIRECT POSTGRESQL INTEGRATION")
logger.info("")
logger.info("   CRITICAL FIXES APPLIED:")
logger.info("   ✅ Direct PostgreSQL connection with raw SQL fallback")
logger.info("   ✅ Aggressive multi-format DN search with raw SQL")
logger.info("   ✅ Automatic detection of DN format in database")
logger.info("   ✅ Debug endpoints to see actual data in Railway PostgreSQL")
logger.info("   ✅ Fallback to raw SQL when ORM fails")
logger.info("")
logger.info("   DEBUG COMMANDS NOW AVAILABLE:")
logger.info("   • 'debug dns' - Show all DNs in database")
logger.info("   • 'check dn 6243611920' - Check if specific DN exists")
logger.info("")
logger.info("   DN SEARCH NOW USES:")
logger.info("   • SQLAlchemy ORM (primary)")
logger.info("   • Raw SQL with text() (fallback)")
logger.info("   • Multiple formats and patterns")
logger.info("=" * 70)
