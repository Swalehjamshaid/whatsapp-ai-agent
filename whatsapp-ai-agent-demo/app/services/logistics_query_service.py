# ==========================================================
# FILE: app/services/logistics_query_service.py (v9.0 - PRODUCTION READY)
# ==========================================================
# PURPOSE: Single source of truth for operational logistics tracking
#
# IMPROVEMENTS v9.0:
# - ✅ Removed hardcoded table names (uses ORM exclusively)
# - ✅ Added database startup validation
# - ✅ Added comprehensive health check
# - ✅ Standardized business rules for aging calculations
# - ✅ Multi-stage DN search with normalization
# - ✅ DN aggregation engine (1 DN = multiple products, count once)
# - ✅ Mandatory DN response structure
# - ✅ Dealer operational intelligence
# - ✅ Warehouse intelligence with priority buckets
# - ✅ Region intelligence with performance metrics
# - ✅ Proper error handling (UserError vs SystemError)
# - ✅ Performance caching for dealers/warehouses/regions
# ==========================================================

from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, cast, String, Integer, Float, text, inspect
from cachetools import TTLCache
from loguru import logger

from app.models import DeliveryReport


# ==========================================================
# ERROR CLASSES
# ==========================================================

class UserError(Exception):
    """User-friendly error - shown to WhatsApp user"""
    pass

class SystemError(Exception):
    """System error - logged but not shown to user"""
    pass

class DatabaseError(Exception):
    """Database connection/query error"""
    pass


# ==========================================================
# LOGISTICS QUERY SERVICE
# ==========================================================

class LogisticsQueryService:
    def __init__(self, db: Session):
        self.db = db
        self.table_name = DeliveryReport.__tablename__
        
        # PHASE 10: Performance caching
        self.dealer_cache = TTLCache(maxsize=200, ttl=600)   # 10 minutes
        self.warehouse_cache = TTLCache(maxsize=100, ttl=600)
        self.region_cache = TTLCache(maxsize=50, ttl=600)
        
        # PHASE 2: Startup validation
        self._validate_startup()
        
        logger.info("=" * 70)
        logger.info("📦 LOGISTICS QUERY SERVICE v9.0 - PRODUCTION READY")
        logger.info(f"   Table: {self.table_name}")
        logger.info(f"   Cache: Dealer={self.dealer_cache.maxsize}, Warehouse={self.warehouse_cache.maxsize}")
        logger.info("=" * 70)
    
    # ==========================================================
    # PHASE 2: DATABASE STARTUP VALIDATION
    # ==========================================================
    
    def _validate_startup(self):
        """Validate database table exists at startup"""
        try:
            inspector = inspect(self.db.bind)
            if not inspector.has_table(self.table_name):
                raise DatabaseError(f"Table '{self.table_name}' not found in database")
            
            # Check if table has data
            count = self.db.query(DeliveryReport).count()
            logger.info(f"   ✅ Database table '{self.table_name}' found with {count} records")
            
        except Exception as e:
            logger.error(f"   ❌ Database validation failed: {e}")
            raise DatabaseError(f"Database validation failed: {e}")
    
    # ==========================================================
    # PHASE 1: HELPER METHODS (No hardcoded table names)
    # ==========================================================
    
    def _validate_session(self) -> bool:
        if not self.db:
            logger.error("Database session is None")
            return False
        return True
    
    # ==========================================================
    # PHASE 4: UNIVERSAL DN NORMALIZATION
    # ==========================================================
    
    def normalize_dn(self, dn) -> str:
        """Universal DN normalization - handles all formats"""
        if dn is None:
            return ""
        dn_str = str(dn).strip()
        if dn_str.endswith('.0'):
            dn_str = dn_str[:-2]
        import re
        dn_str = re.sub(r'[^0-9]', '', dn_str)
        return dn_str
    
    # ==========================================================
    # PHASE 3: BUSINESS RULE ENGINE (Standardized Aging)
    # ==========================================================
    
    def calculate_delivery_days(self, pgi_date, dn_date) -> int:
        """Business Rule: Delivery Days = PGI Date - DN Date"""
        if pgi_date and dn_date:
            return max(0, (pgi_date - dn_date).days)
        return 0
    
    def calculate_pod_days(self, pod_date, pgi_date) -> int:
        """Business Rule: POD Days = POD Date - PGI Date"""
        if pod_date and pgi_date:
            return max(0, (pod_date - pgi_date).days)
        return 0
    
    def calculate_pending_delivery_days(self, dn_date) -> int:
        """Business Rule: Pending Delivery Days = Today - DN Date"""
        if dn_date:
            return max(0, (date.today() - dn_date).days)
        return 0
    
    def calculate_pending_pod_days(self, pgi_date) -> int:
        """Business Rule: Pending POD Days = Today - PGI Date"""
        if pgi_date:
            return max(0, (date.today() - pgi_date).days)
        return 0
    
    def calculate_priority(self, days: int) -> str:
        if days > 14:
            return "CRITICAL"
        elif days > 7:
            return "HIGH"
        elif days > 3:
            return "MEDIUM"
        else:
            return "LOW"
    
    def calculate_dn_status(self, pgi_date, pod_date) -> Dict[str, str]:
        if pgi_date and pod_date:
            return {"status": "Delivered", "emoji": "✅", "description": "Full delivery completed"}
        elif pgi_date and not pod_date:
            return {"status": "POD Pending", "emoji": "⏳", "description": "Dispatched, awaiting proof of delivery"}
        else:
            return {"status": "Delivery Pending", "emoji": "🟡", "description": "Not yet dispatched"}
    
    # ==========================================================
    # PHASE 6: DN AGGREGATION ENGINE
    # ==========================================================
    
    def aggregate_dn_records(self, records: List[DeliveryReport]) -> Dict[str, Any]:
        """
        Aggregate multiple records of same DN into one.
        Business Rule: 1 DN = Multiple Products, counted ONCE
        """
        if not records:
            return {}
        
        first = records[0]
        dn_number = self.normalize_dn(first.dn_no)
        
        unique_models = set()
        products = []
        product_codes = set()
        total_quantity = 0
        total_amount = 0.0
        
        for r in records:
            total_quantity += int(r.dn_qty or 0)
            total_amount += float(r.dn_amount or 0)
            
            if r.material_no:
                model_name = r.customer_model or r.material_no
                unique_models.add(model_name)
                
                if r.material_no not in product_codes:
                    product_codes.add(r.material_no)
                    products.append({
                        "material_no": r.material_no,
                        "customer_model": model_name,
                        "quantity": int(r.dn_qty or 0),
                        "amount": float(r.dn_amount or 0)
                    })
                else:
                    for p in products:
                        if p["material_no"] == r.material_no:
                            p["quantity"] += int(r.dn_qty or 0)
                            p["amount"] += float(r.dn_amount or 0)
                            break
        
        delivery_days = self.calculate_delivery_days(first.good_issue_date, first.dn_create_date)
        pod_days = self.calculate_pod_days(first.pod_date, first.good_issue_date)
        status_info = self.calculate_dn_status(first.good_issue_date, first.pod_date)
        
        return {
            "dn_no": dn_number,
            "customer_name": first.customer_name,
            "customer_code": first.customer_code,
            "division": first.division,
            "warehouse": first.warehouse,
            "warehouse_code": first.warehouse_code,
            "city": first.ship_to_city,
            "dn_date": first.dn_create_date,
            "dn_date_str": first.dn_create_date.strftime("%Y-%m-%d") if first.dn_create_date else "N/A",
            "pgi_date": first.good_issue_date,
            "pgi_date_str": first.good_issue_date.strftime("%Y-%m-%d") if first.good_issue_date else "Not Dispatched",
            "pod_date": first.pod_date,
            "pod_date_str": first.pod_date.strftime("%Y-%m-%d") if first.pod_date else "Not Received",
            "delivery_days": delivery_days,
            "pod_days": pod_days,
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
    # PHASE 5: MULTI-STAGE DN SEARCH
    # ==========================================================
    
    def _search_dn(self, dn_number: str) -> List[DeliveryReport]:
        """Multi-stage DN search with multiple fallbacks"""
        normalized = self.normalize_dn(dn_number)
        
        # Stage 1: Exact match
        exact = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String) == normalized
        ).all()
        if exact:
            logger.info(f"✅ DN found via exact match: {normalized}")
            return exact
        
        # Stage 2: With .0
        with_dot = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String) == f"{normalized}.0"
        ).all()
        if with_dot:
            logger.info(f"✅ DN found via .0 match: {normalized}.0")
            return with_dot
        
        # Stage 3: Contains
        contains = self.db.query(DeliveryReport).filter(
            cast(DeliveryReport.dn_no, String).like(f"%{normalized}%")
        ).all()
        if contains:
            logger.info(f"✅ DN found via contains match: %{normalized}%")
            return contains
        
        # Stage 4: Direct cast (for integer stored as number)
        try:
            int_val = int(normalized)
            integer_match = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == int_val
            ).all()
            if integer_match:
                logger.info(f"✅ DN found via integer match: {int_val}")
                return integer_match
        except:
            pass
        
        logger.info(f"❌ DN {dn_number} not found after all search stages")
        return []
    
    # ==========================================================
    # PHASE 7: MANDATORY DN RESPONSE STRUCTURE
    # ==========================================================
    
    def build_standard_dn_response(self, aggregated: Dict[str, Any]) -> Dict[str, Any]:
        """Build standard DN response with all mandatory fields"""
        return {
            "dn_no": aggregated.get("dn_no", "N/A"),
            "date": aggregated.get("dn_date_str", "N/A"),
            "dealer_name": aggregated.get("customer_name", "N/A"),
            "dealer_code": aggregated.get("customer_code", "N/A"),
            "sales_office": aggregated.get("division", "N/A"),
            "warehouse": aggregated.get("warehouse", "N/A"),
            "warehouse_code": aggregated.get("warehouse_code", "N/A"),
            "city": aggregated.get("city", "N/A"),
            "status": aggregated.get("status", "Unknown"),
            "status_emoji": aggregated.get("status_emoji", "❓"),
            "status_description": aggregated.get("status_description", ""),
            "pgi_date": aggregated.get("pgi_date_str", "Not Dispatched"),
            "pod_date": aggregated.get("pod_date_str", "Not Received"),
            "delivery_days": aggregated.get("delivery_days", 0),
            "pod_days": aggregated.get("pod_days", 0),
            "total_models": aggregated.get("total_models", 0),
            "models_list": aggregated.get("models_list", []),
            "total_quantity": aggregated.get("total_quantity", 0),
            "total_amount": aggregated.get("total_amount", 0.0),
            "products": aggregated.get("products", [])
        }
    
    # ==========================================================
    # MAIN DN QUERY ENTRY POINT
    # ==========================================================
    
    def get_complete_dn_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """Get complete DN intelligence - MAIN ENTRY POINT"""
        logger.info(f"🔍 DN Query: {dn_number}")
        
        if not self._validate_session():
            return self._format_error("Database session unavailable", is_user_error=True)
        
        try:
            records = self._search_dn(dn_number)
            
            if not records:
                sample_dns = self._get_sample_dns()
                error_msg = f"DN {dn_number} not found in database."
                if sample_dns:
                    error_msg += f" Available DNs: {', '.join(sample_dns[:5])}"
                return self._format_error(error_msg, is_user_error=True)
            
            aggregated = self.aggregate_dn_records(records)
            response = self.build_standard_dn_response(aggregated)
            
            priority = self.calculate_priority(response.get("delivery_days", 0))
            response["priority"] = priority
            
            summary = f"DN {response['dn_no']} is {response['status']}. {response['total_models']} models, {response['total_quantity']} units."
            
            return self._format_success(response, summary)
            
        except Exception as e:
            logger.error(f"❌ DN query error: {e}")
            return self._format_error("Unable to process DN query", is_user_error=False)
    
    # ==========================================================
    # PHASE 8: DEALER OPERATIONAL INTELLIGENCE
    # ==========================================================
    
    def get_dealer_all_dns(self, dealer_name: str) -> Dict[str, Any]:
        """Get all DNs for a dealer with complete details"""
        cache_key = f"dealer_dns_{dealer_name.lower()}"
        
        if cache_key in self.dealer_cache:
            return self.dealer_cache[cache_key]
        
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return self._format_error(f"Dealer '{dealer_name}' not found", is_user_error=True)
            
            # Group by DN
            dn_groups = {}
            for r in records:
                dn_no = self.normalize_dn(r.dn_no)
                if dn_no not in dn_groups:
                    dn_groups[dn_no] = []
                dn_groups[dn_no].append(r)
            
            dns = []
            for dn_no, group in dn_groups.items():
                aggregated = self.aggregate_dn_records(group)
                dns.append({
                    "dn_no": dn_no,
                    "date": aggregated.get("dn_date_str", "N/A"),
                    "pgi_date": aggregated.get("pgi_date_str", "N/A"),
                    "pod_date": aggregated.get("pod_date_str", "N/A"),
                    "warehouse": aggregated.get("warehouse", "N/A"),
                    "status": aggregated.get("status", "Unknown"),
                    "total_models": aggregated.get("total_models", 0),
                    "total_quantity": aggregated.get("total_quantity", 0)
                })
            
            dns.sort(key=lambda x: x.get("date", ""), reverse=True)
            
            result = self._format_success(
                {"dealer_name": dealer_name, "total_dns": len(dns), "dns": dns},
                f"Dealer {dealer_name} has {len(dns)} DNs"
            )
            
            self.dealer_cache[cache_key] = result
            return result
            
        except Exception as e:
            logger.error(f"Dealer DNs error: {e}")
            return self._format_error("Unable to retrieve dealer DNs", is_user_error=False)
    
    def get_dealer_operational_summary(self, dealer_name: str) -> Dict[str, Any]:
        """Get operational summary for a dealer (pending deliveries, pending POD, delays)"""
        try:
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_name}%")
            ).all()
            
            if not records:
                return self._format_error(f"Dealer '{dealer_name}' not found", is_user_error=True)
            
            pending_delivery = []
            pending_pod = []
            delayed = []
            
            for r in records:
                dn_no = self.normalize_dn(r.dn_no)
                if not r.good_issue_date:
                    days = self.calculate_pending_delivery_days(r.dn_create_date)
                    pending_delivery.append({"dn_no": dn_no, "days": days})
                elif r.good_issue_date and not r.pod_date:
                    days = self.calculate_pending_pod_days(r.good_issue_date)
                    pending_pod.append({"dn_no": dn_no, "days": days})
                
                delivery_days = self.calculate_delivery_days(r.good_issue_date, r.dn_create_date)
                if delivery_days > 7:
                    delayed.append({"dn_no": dn_no, "days": delivery_days})
            
            return self._format_success(
                {
                    "dealer_name": dealer_name,
                    "pending_deliveries": len(pending_delivery),
                    "pending_pod": len(pending_pod),
                    "delayed_shipments": len(delayed),
                    "pending_delivery_list": pending_delivery[:10],
                    "pending_pod_list": pending_pod[:10],
                    "delayed_list": delayed[:10]
                },
                f"Dealer {dealer_name}: {len(pending_delivery)} pending deliveries, {len(pending_pod)} pending POD"
            )
        except Exception as e:
            logger.error(f"Dealer operational summary error: {e}")
            return self._format_error("Unable to retrieve dealer operations", is_user_error=False)
    
    # ==========================================================
    # PHASE 9: WAREHOUSE INTELLIGENCE
    # ==========================================================
    
    def get_warehouse_status(self, warehouse_name: str = None) -> Dict[str, Any]:
        """Get warehouse status with priority buckets"""
        cache_key = f"warehouse_{warehouse_name or 'all'}"
        
        if cache_key in self.warehouse_cache:
            return self.warehouse_cache[cache_key]
        
        try:
            query = self.db.query(DeliveryReport)
            if warehouse_name:
                query = query.filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%"))
            
            records = query.all()
            
            if not records:
                return self._format_error(f"Warehouse '{warehouse_name}' not found" if warehouse_name else "No warehouse data", is_user_error=True)
            
            # Aggregate by warehouse
            warehouses = {}
            for r in records:
                wh = r.warehouse or "Unknown"
                if wh not in warehouses:
                    warehouses[wh] = {
                        "dn_count": 0,
                        "total_quantity": 0,
                        "pending_dispatch": 0,
                        "pending_pod": 0,
                        "critical_delays": 0,
                        "high_priority": 0
                    }
                
                warehouses[wh]["dn_count"] += 1
                warehouses[wh]["total_quantity"] += int(r.dn_qty or 0)
                
                if not r.good_issue_date:
                    warehouses[wh]["pending_dispatch"] += 1
                elif r.good_issue_date and not r.pod_date:
                    warehouses[wh]["pending_pod"] += 1
                
                days = self.calculate_delivery_days(r.good_issue_date, r.dn_create_date)
                if days > 14:
                    warehouses[wh]["critical_delays"] += 1
                elif days > 7:
                    warehouses[wh]["high_priority"] += 1
            
            result_list = []
            for wh, data in warehouses.items():
                result_list.append({
                    "warehouse": wh,
                    "dn_count": data["dn_count"],
                    "total_quantity": data["total_quantity"],
                    "pending_dispatch": data["pending_dispatch"],
                    "pending_pod": data["pending_pod"],
                    "critical_delays": data["critical_delays"],
                    "high_priority": data["high_priority"]
                })
            
            result_list.sort(key=lambda x: x["dn_count"], reverse=True)
            
            result = self._format_success(
                {"warehouses": result_list, "total_warehouses": len(result_list)},
                f"Found {len(result_list)} warehouses"
            )
            
            self.warehouse_cache[cache_key] = result
            return result
            
        except Exception as e:
            logger.error(f"Warehouse status error: {e}")
            return self._format_error("Unable to retrieve warehouse status", is_user_error=False)
    
    # ==========================================================
    # PHASE 10: REGION INTELLIGENCE
    # ==========================================================
    
    def get_region_performance(self, region: str = None) -> Dict[str, Any]:
        """Get region performance metrics"""
        cache_key = f"region_{region or 'all'}"
        
        if cache_key in self.region_cache:
            return self.region_cache[cache_key]
        
        try:
            query = self.db.query(DeliveryReport)
            if region:
                query = query.filter(DeliveryReport.division == region)
            
            records = query.all()
            
            if not records:
                return self._format_success(
                    {"region": region or "All", "total_dns": 0},
                    f"No data for region {region}" if region else "No data available"
                )
            
            unique_dns = set()
            unique_dealers = set()
            unique_warehouses = set()
            total_quantity = 0
            pending_pod = 0
            delivered = 0
            
            for r in records:
                unique_dns.add(self.normalize_dn(r.dn_no))
                if r.customer_name:
                    unique_dealers.add(r.customer_name)
                if r.warehouse:
                    unique_warehouses.add(r.warehouse)
                total_quantity += int(r.dn_qty or 0)
                
                if r.good_issue_date and not r.pod_date:
                    pending_pod += 1
                if r.pod_date:
                    delivered += 1
            
            result = self._format_success(
                {
                    "region": region or "All",
                    "total_dns": len(unique_dns),
                    "total_quantity": total_quantity,
                    "unique_dealers": len(unique_dealers),
                    "unique_warehouses": len(unique_warehouses),
                    "pending_pod": pending_pod,
                    "delivered": delivered,
                    "completion_rate": round((delivered / max(1, len(unique_dns))) * 100, 1)
                },
                f"Region {region or 'All'}: {len(unique_dns)} DNs, {len(unique_dealers)} dealers"
            )
            
            self.region_cache[cache_key] = result
            return result
            
        except Exception as e:
            logger.error(f"Region performance error: {e}")
            return self._format_error("Unable to retrieve region performance", is_user_error=False)
    
    # ==========================================================
    # PHASE 11: POD & DELIVERY DASHBOARDS
    # ==========================================================
    
    def get_pod_status(self, region: str = None) -> Dict[str, Any]:
        """Get POD status with priority buckets"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.pod_status.in_(['PENDING', 'NOT_RECEIVED'])
            )
            if region:
                query = query.filter(DeliveryReport.division == region)
            
            records = query.all()
            
            critical = 0
            high = 0
            medium = 0
            low = 0
            pending_list = []
            
            for r in records:
                days = self.calculate_pending_pod_days(r.good_issue_date)
                priority = self.calculate_priority(days)
                
                if priority == "CRITICAL":
                    critical += 1
                elif priority == "HIGH":
                    high += 1
                elif priority == "MEDIUM":
                    medium += 1
                else:
                    low += 1
                
                pending_list.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "pending_days": days,
                    "priority": priority
                })
            
            pending_list.sort(key=lambda x: x["pending_days"], reverse=True)
            
            return self._format_success(
                {
                    "total_pending": len(records),
                    "critical": critical,
                    "high": high,
                    "medium": medium,
                    "low": low,
                    "pending_list": pending_list[:20]
                },
                f"{len(records)} PODs pending ({critical} critical)"
            )
        except Exception as e:
            logger.error(f"POD status error: {e}")
            return self._format_error("Unable to retrieve POD status", is_user_error=False)
    
    def get_pending_deliveries(self, days: int = None) -> Dict[str, Any]:
        """Get pending deliveries with priority buckets"""
        try:
            query = self.db.query(DeliveryReport).filter(
                DeliveryReport.good_issue_date.is_(None)
            )
            
            if days:
                cutoff = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff)
            
            records = query.all()
            
            critical = 0
            high = 0
            medium = 0
            low = 0
            pending_list = []
            
            for r in records:
                days_pending = self.calculate_pending_delivery_days(r.dn_create_date)
                priority = self.calculate_priority(days_pending)
                
                if priority == "CRITICAL":
                    critical += 1
                elif priority == "HIGH":
                    high += 1
                elif priority == "MEDIUM":
                    medium += 1
                else:
                    low += 1
                
                pending_list.append({
                    "dn_no": r.dn_no,
                    "dealer": r.customer_name,
                    "pending_days": days_pending,
                    "priority": priority
                })
            
            pending_list.sort(key=lambda x: x["pending_days"], reverse=True)
            
            return self._format_success(
                {
                    "total_pending": len(records),
                    "critical": critical,
                    "high": high,
                    "medium": medium,
                    "low": low,
                    "pending_list": pending_list[:20]
                },
                f"{len(records)} deliveries pending ({critical} critical)"
            )
        except Exception as e:
            logger.error(f"Pending deliveries error: {e}")
            return self._format_error("Unable to retrieve pending deliveries", is_user_error=False)
    
    # ==========================================================
    # PHASE 1: SAMPLE DNS HELPER
    # ==========================================================
    
    def _get_sample_dns(self, limit: int = 5) -> List[str]:
        """Get sample DNs from database for error messages"""
        try:
            results = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None)
            ).distinct().limit(limit).all()
            return [str(r[0]) for r in results if r[0]]
        except:
            return []
    
    # ==========================================================
    # PHASE 9: PROPER ERROR HANDLING
    # ==========================================================
    
    def _format_success(self, data: Any, summary: str) -> Dict[str, Any]:
        return {
            "success": True,
            "data": data,
            "_summary": summary
        }
    
    def _format_error(self, error: str, is_user_error: bool = True) -> Dict[str, Any]:
        """Format error - user errors shown, system errors hidden"""
        if is_user_error:
            return {
                "success": False,
                "data": {},
                "_summary": f"❌ {error}",
                "error": error
            }
        else:
            # System error - user sees generic message
            logger.error(f"System error: {error}")
            return {
                "success": False,
                "data": {},
                "_summary": "❌ Unable to process request. Please try again later.",
                "error": "System error"
            }
    
    # ==========================================================
    # COMPATIBILITY METHODS (Aliases)
    # ==========================================================
    
    def get_dn_timeline(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        if result.get("success"):
            dn_data = result.get("data", {})
            timeline = [
                {"status": "DN Created", "date": dn_data.get("date", "N/A")},
                {"status": "PGI Date", "date": dn_data.get("pgi_date", "N/A")},
                {"status": "POD Date", "date": dn_data.get("pod_date", "N/A")}
            ]
            return self._format_success(timeline, f"Timeline for DN {dn_number}")
        return result
    
    def get_dn_products(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        if result.get("success"):
            products = result.get("data", {}).get("products", [])
            return self._format_success(products, f"Products in DN {dn_number}")
        return result
    
    def get_top_dealers(self, limit: int = 10) -> Dict[str, Any]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count')
            ).group_by(DeliveryReport.customer_name).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            dealers = [{"name": r[0] or "Unknown", "dn_count": r[1]} for r in results if r[0]]
            return self._format_success(dealers, f"Top {len(dealers)} dealers")
        except Exception as e:
            return self._format_error("Unable to retrieve top dealers", is_user_error=False)
    
    def get_top_warehouses(self, limit: int = 10) -> Dict[str, Any]:
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(func.distinct(DeliveryReport.dn_no)).label('dn_count')
            ).group_by(DeliveryReport.warehouse).order_by(
                func.count(func.distinct(DeliveryReport.dn_no)).desc()
            ).limit(limit).all()
            
            warehouses = [{"name": r[0] or "Unknown", "dn_count": r[1]} for r in results if r[0]]
            return self._format_success(warehouses, f"Top {len(warehouses)} warehouses")
        except Exception as e:
            return self._format_error("Unable to retrieve top warehouses", is_user_error=False)
    
    def get_top_products(self, limit: int = 10) -> Dict[str, Any]:
        try:
            results = self.db.query(
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_qty).label('total_qty')
            ).group_by(DeliveryReport.material_no, DeliveryReport.customer_model).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            products = [{"code": r[0] or "N/A", "name": r[1] or "N/A", "total_quantity": r[2] or 0} for r in results]
            return self._format_success(products, f"Top {len(products)} products")
        except Exception as e:
            return self._format_error("Unable to retrieve top products", is_user_error=False)
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
        return self.get_warehouse_status(warehouse_name)
    
    def get_region_information(self, region: str) -> Dict[str, Any]:
        return self.get_region_performance(region)
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        return self.get_dealer_all_dns(dealer_name)
    
    def get_dealer_details(self, dealer_name: str) -> Dict[str, Any]:
        return self.get_dealer_all_dns(dealer_name)
    
    def get_pod_aging_report(self) -> Dict[str, Any]:
        return self.get_pod_status()
    
    def get_delivery_aging_report(self) -> Dict[str, Any]:
        return self.get_pending_deliveries()
    
    def get_pending_pgi(self, days: int = None) -> Dict[str, Any]:
        try:
            query = self.db.query(DeliveryReport).filter(DeliveryReport.pgi_status == 'PENDING')
            if days:
                cutoff = date.today() - timedelta(days=days)
                query = query.filter(DeliveryReport.dn_create_date <= cutoff)
            pending_count = query.count()
            return self._format_success(
                {"pending_count": pending_count, "pending_pgi": []},
                f"{pending_count} PGI pending"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve pending PGI", is_user_error=False)
    
    def get_pgi_aging_report(self) -> Dict[str, Any]:
        return self.get_pending_pgi()
    
    def get_pod_performance(self) -> Dict[str, Any]:
        try:
            total = self.db.query(DeliveryReport).count()
            completed = self.db.query(DeliveryReport).filter(DeliveryReport.pod_status == 'RECEIVED').count()
            rate = round((completed / max(1, total)) * 100, 1)
            return self._format_success(
                {"total": total, "completed": completed, "compliance_rate": rate, "target": 95},
                f"POD Compliance: {rate}% ({completed}/{total})"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve POD performance", is_user_error=False)
    
    def get_delivery_performance(self) -> Dict[str, Any]:
        try:
            total = self.db.query(DeliveryReport).count()
            completed = self.db.query(DeliveryReport).filter(DeliveryReport.delivery_status == 'DELIVERED').count()
            rate = round((completed / max(1, total)) * 100, 1)
            return self._format_success(
                {"total": total, "completed": completed, "on_time_rate": rate, "target": 95},
                f"Delivery Performance: {rate}% ({completed}/{total})"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve delivery performance", is_user_error=False)
    
    def get_pending_items(self, region: str = None) -> Dict[str, Any]:
        try:
            query = self.db.query(DeliveryReport).filter(DeliveryReport.pending_flag == True)
            if region:
                query = query.filter(DeliveryReport.division == region)
            pending_count = query.count()
            return self._format_success(
                {"total_pending": pending_count},
                f"Total pending: {pending_count}"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve pending items", is_user_error=False)
    
    def get_dn_aging_report(self, dn_number: str) -> Dict[str, Any]:
        return self.get_complete_dn_intelligence(dn_number)
    
    def validate_dn(self, dn_number: str) -> bool:
        result = self.get_complete_dn_intelligence(dn_number)
        return result.get("success", False)
    
    def get_all_dns_list(self, limit: int = 20) -> Dict[str, Any]:
        try:
            results = self.db.query(DeliveryReport.dn_no).filter(
                DeliveryReport.dn_no.isnot(None)
            ).distinct().limit(limit).all()
            dns = [str(r[0]) for r in results if r[0]]
            return self._format_success(
                {"dns": dns, "total": len(dns)},
                f"Found {len(dns)} DNs"
            )
        except Exception as e:
            return self._format_error("Unable to retrieve DNs", is_user_error=False)
    
    def debug_show_all_dns(self, limit: int = 30) -> Dict[str, Any]:
        return self.get_all_dns_list(limit)
    
    def debug_check_dn_exists(self, dn_number: str) -> Dict[str, Any]:
        result = self.get_complete_dn_intelligence(dn_number)
        return {
            "success": True,
            "data": {
                "dn_searched": dn_number,
                "found": result.get("success", False),
                "match_count": 1 if result.get("success") else 0
            },
            "_summary": f"DN {dn_number} {'FOUND' if result.get('success') else 'NOT FOUND'} in database"
        }
    
    def show_all_dns(self, limit: int = 20) -> Dict[str, Any]:
        return self.get_all_dns_list(limit)
    
    # ==========================================================
    # PHASE 3: DATABASE HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Comprehensive health check for monitoring"""
        db_connected = False
        table_exists = False
        row_count = 0
        
        try:
            self.db.execute(text("SELECT 1"))
            db_connected = True
            
            inspector = inspect(self.db.bind)
            table_exists = inspector.has_table(self.table_name)
            
            if table_exists:
                row_count = self.db.query(DeliveryReport).count()
        except Exception as e:
            logger.error(f"Health check failed: {e}")
        
        return {
            "service": "logistics",
            "version": "9.0",
            "status": "healthy" if db_connected and table_exists else "unhealthy",
            "database_connected": db_connected,
            "table_exists": table_exists,
            "table_name": self.table_name,
            "row_count": row_count,
            "cache_sizes": {
                "dealer_cache": len(self.dealer_cache),
                "warehouse_cache": len(self.warehouse_cache),
                "region_cache": len(self.region_cache)
            },
            "features": {
                "dn_aggregation": True,
                "business_rules_applied": True,
                "priority_buckets": True,
                "multi_stage_search": True
            }
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_logistics_query_service(db: Session) -> LogisticsQueryService:
    return LogisticsQueryService(db)


# ==========================================================
# INITIALIZATION LOG
# ==========================================================

logger.info("=" * 70)
logger.info("📦 LOGISTICS QUERY SERVICE v9.0 - PRODUCTION READY")
logger.info("")
logger.info("   ✅ No hardcoded table names (uses ORM)")
logger.info("   ✅ Database startup validation")
logger.info("   ✅ Comprehensive health check")
logger.info("   ✅ Standardized business rules for aging")
logger.info("   ✅ Multi-stage DN search")
logger.info("   ✅ DN aggregation engine (1 DN = multiple products)")
logger.info("   ✅ Mandatory DN response structure")
logger.info("   ✅ Dealer operational intelligence")
logger.info("   ✅ Warehouse intelligence with priority buckets")
logger.info("   ✅ Region intelligence with performance metrics")
logger.info("   ✅ Proper error handling (UserError vs SystemError)")
logger.info("   ✅ Performance caching")
logger.info("")
logger.info("   SUPPORTS:")
logger.info("   • DN Queries (status, products, quantity, warehouse, POD)")
logger.info("   • Delivery Queries (pending, delayed, critical)")
logger.info("   • POD Queries (pending, critical, aging)")
logger.info("   • Warehouse Queries (status, delays, performance)")
logger.info("   • Region Queries (performance, metrics)")
logger.info("   • Dealer Queries (all DNs, operational summary)")
logger.info("=" * 70)
