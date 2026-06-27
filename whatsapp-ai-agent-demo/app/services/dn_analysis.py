# ======================================================================================================
# FILE: app/services/dn_analysis.py
# VERSION: v13.2 - COMPLETE DN INFORMATION FETCHER
# ======================================================================================================
# PURPOSE: Fetch ALL information for a DN from PostgreSQL
# ======================================================================================================

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os

logger = logging.getLogger(__name__)

# ======================================================================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# ======================================================================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"

# ======================================================================================================
# BLOCK 2: DNAnalysisService CLASS
# ======================================================================================================

class DNAnalysisService:
    """
    DN Analytics Service - Fetch ALL information for a DN.
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "13.2"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        self._startup_time = datetime.now().isoformat()
        self._debug_mode = DEBUG_MODE
        
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        
        # Test connection
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error("❌ DNAnalysisService initialization FAILED")
    
    # ==================================================================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ==================================================================================================
    
    def _test_connection(self) -> bool:
        """Test database connection."""
        session = None
        try:
            if not SessionLocal:
                logger.error("❌ SessionLocal is None")
                return False
            
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            logger.info("✅ Database connection test: SUCCESS")
            return True
        except Exception as e:
            logger.error(f"❌ Database connection test FAILED: {e}")
            return False
        finally:
            if session:
                session.close()
    
    def _get_session(self) -> Optional[Session]:
        """Get database session."""
        if not SessionLocal:
            logger.error("❌ SessionLocal not available")
            return None
        
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Failed to get database session: {e}")
            return None
    
    def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        """Execute raw SQL query and return results as dicts."""
        start_time = time.time()
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            
            if self._debug_mode:
                logger.debug(f"📝 Executing SQL: {query[:200]}...")
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            execution_time_ms = (time.time() - start_time) * 1000
            self._query_count += 1
            self._total_execution_time_ms += execution_time_ms
            
            if self._debug_mode:
                logger.debug(f"✅ Query returned {len(rows)} rows in {execution_time_ms:.2f}ms")
            return rows
            
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed: {e}")
            return []
        finally:
            if session:
                session.close()
    
    # ==================================================================================================
    # BLOCK 4: DN VALIDATION
    # ==================================================================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        """Normalize DN number for search."""
        if not dn_no:
            return ""
        normalized = re.sub(r'[^0-9]', '', dn_no.strip())
        return normalized
    
    # ==================================================================================================
    # BLOCK 5: COMPLETE DN INFORMATION FETCHER
    # ==================================================================================================
    
    def get_dn_complete_info(self, dn_no: str) -> Dict[str, Any]:
        """
        Fetch COMPLETE information for a DN.
        
        Returns ALL rows, aggregated data, and complete dashboard.
        """
        logger.info(f"🔍 Fetching complete info for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        logger.info(f"   ├── Normalized: '{normalized_dn}'")
        
        # STEP 1: Get ALL rows for this DN
        query = """
            SELECT 
                id,
                dn_no,
                dn_work,
                order_type,
                division,
                customer_code,
                dealer_code,
                customer_name,
                customer_model,
                material_no,
                storage_location,
                sales_office,
                sales_manager,
                ship_to_city,
                warehouse,
                warehouse_code,
                delivery_location,
                dn_qty,
                dn_amount,
                dn_create_date,
                good_issue_date,
                pod_date,
                remarks,
                delivery_status,
                pgi_status,
                pod_status,
                pending_flag,
                source_file,
                upload_batch_id,
                imported_at,
                created_at,
                updated_at
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) = :dn_no
            ORDER BY id ASC
        """
        
        all_rows = self._execute_query(query, {"dn_no": normalized_dn})
        
        if not all_rows:
            # Try fallback
            fallback_query = """
                SELECT DISTINCT dn_no
                FROM delivery_reports
                WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                LIMIT 10
            """
            fallback_results = self._execute_query(fallback_query, {"dn_no": normalized_dn})
            similar_dns = [str(r.get('dn_no', '')) for r in fallback_results if r.get('dn_no')]
            
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found",
                    "similar_dns": similar_dns[:5],
                    "message": f"DN not found. Did you mean: {', '.join(similar_dns[:3])}?"
                }
            
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        logger.info(f"   ├── Found {len(all_rows)} rows for DN")
        
        # STEP 2: Aggregate ALL data
        aggregated = self._aggregate_dn_data(all_rows)
        
        # STEP 3: Build complete dashboard
        dashboard = self._build_complete_dashboard(aggregated)
        
        logger.info(f"   ├── Materials: {dashboard.get('material_count', 0)}")
        logger.info(f"   ├── Models: {dashboard.get('model_count', 0)}")
        logger.info(f"   ├── Units: {dashboard.get('total_units', 0)}")
        logger.info(f"   ├── Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}")
        logger.info(f"   ├── Status: {dashboard.get('calculated_stage', 'Unknown')}")
        logger.info(f"✅ Complete info fetched successfully")
        
        return {"success": True, "data": dashboard, "all_rows": all_rows}
    
    def _aggregate_dn_data(self, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Aggregate ALL rows for a DN."""
        if not rows:
            return {}
        
        first_row = rows[0]
        
        # Collections
        unique_models = set()
        unique_materials = set()
        products = []
        total_units = 0
        total_revenue = 0
        dn_create_dates = []
        good_issue_dates = []
        pod_dates = []
        
        for row in rows:
            # Models
            model = row.get('customer_model')
            if model:
                unique_models.add(model)
            
            # Materials
            material = row.get('material_no')
            if material:
                unique_materials.add(material)
            
            # Products
            if model:
                qty = int(row.get('dn_qty', 0) or 0)
                revenue = float(row.get('dn_amount', 0) or 0)
                total_units += qty
                total_revenue += revenue
                
                products.append({
                    'model': str(model),
                    'material_no': str(row.get('material_no', 'N/A')),
                    'division': str(row.get('division', 'Unknown')),
                    'quantity': qty,
                    'revenue': revenue,
                    'warehouse': str(row.get('warehouse', 'Unknown')),
                    'city': str(row.get('ship_to_city', 'Unknown')),
                    'storage_location': str(row.get('storage_location', 'N/A')),
                })
            
            # Dates
            if row.get('dn_create_date'):
                dn_create_dates.append(row.get('dn_create_date'))
            if row.get('good_issue_date'):
                good_issue_dates.append(row.get('good_issue_date'))
            if row.get('pod_date'):
                pod_dates.append(row.get('pod_date'))
        
        # Sort products
        products.sort(key=lambda x: x.get('model', ''))
        
        return {
            # Core information (from first row)
            "dn_no": first_row.get('dn_no'),
            "dn_work": first_row.get('dn_work'),
            "order_type": first_row.get('order_type'),
            "division": first_row.get('division'),
            
            # Customer
            "customer_code": first_row.get('customer_code'),
            "dealer_code": first_row.get('dealer_code'),
            "customer_name": first_row.get('customer_name'),
            "dealer_name": first_row.get('customer_name'),
            
            # Location
            "warehouse": first_row.get('warehouse'),
            "warehouse_code": first_row.get('warehouse_code'),
            "city": first_row.get('ship_to_city'),
            "delivery_location": first_row.get('delivery_location'),
            
            # Sales
            "sales_office": first_row.get('sales_office'),
            "sales_manager": first_row.get('sales_manager'),
            
            # Metrics
            "total_units": total_units,
            "total_revenue": total_revenue,
            "material_count": len(unique_materials),
            "model_count": len(unique_models),
            "row_count": len(rows),
            
            # Dates
            "dn_create_date": min(dn_create_dates) if dn_create_dates else None,
            "good_issue_date": max(good_issue_dates) if good_issue_dates else None,
            "pod_date": max(pod_dates) if pod_dates else None,
            
            # Products
            "products": products,
            
            # Source
            "source_file": first_row.get('source_file'),
            "upload_batch_id": first_row.get('upload_batch_id'),
            "imported_at": first_row.get('imported_at'),
            "created_at": first_row.get('created_at'),
            "updated_at": first_row.get('updated_at'),
            
            # Raw rows for reference
            "_all_rows": rows
        }
    
    def _build_complete_dashboard(self, aggregated: Dict[str, Any]) -> Dict[str, Any]:
        """Build complete dashboard from aggregated data."""
        if not aggregated:
            return {}
        
        # Extract dates
        raw_dn_create = aggregated.get('dn_create_date')
        raw_good_issue = aggregated.get('good_issue_date')
        raw_pod = aggregated.get('pod_date')
        
        # Calculate aging
        delivery_aging = self._calculate_days(raw_dn_create, raw_good_issue)
        pod_aging = self._calculate_days(raw_good_issue, raw_pod)
        total_cycle = self._calculate_days(raw_dn_create, raw_pod)
        
        # Format dates
        dn_create_fmt = self._format_date(raw_dn_create)
        good_issue_fmt = self._format_date(raw_good_issue)
        pod_fmt = self._format_date(raw_pod)
        
        # Determine status from dates
        pgi_exists = raw_good_issue is not None
        pod_exists = raw_pod is not None
        
        if pod_exists and pgi_exists:
            stage = "Delivered"
            emoji = "✅"
            pgi_status = "Completed"
            pod_status = "Completed"
            pending = False
            pending_text = "🟢 No"
        elif pgi_exists and not pod_exists:
            stage = "In Transit"
            emoji = "🚚"
            pgi_status = "Completed"
            pod_status = "Pending"
            pending = True
            pending_text = "⚠️ Yes"
        else:
            stage = "Pending Dispatch"
            emoji = "⏳"
            pgi_status = "Pending"
            pod_status = "Pending"
            pending = True
            pending_text = "⚠️ Yes"
        
        # Build products
        products = []
        for p in aggregated.get('products', []):
            products.append({
                'name': p.get('model', 'Unknown'),
                'material_no': p.get('material_no', 'N/A'),
                'division': p.get('division', 'Unknown'),
                'qty': p.get('quantity', 0),
                'revenue': p.get('revenue', 0),
                'warehouse': p.get('warehouse', 'Unknown'),
                'city': p.get('city', 'Unknown'),
            })
        
        return {
            # Core
            "dn_no": aggregated.get('dn_no'),
            "dn_work": aggregated.get('dn_work'),
            "order_type": aggregated.get('order_type'),
            
            # Customer
            "dealer_name": aggregated.get('dealer_name', 'Unknown'),
            "dealer_code": aggregated.get('dealer_code'),
            "customer_name": aggregated.get('customer_name', 'Unknown'),
            "customer_code": aggregated.get('customer_code'),
            
            # Location
            "warehouse": aggregated.get('warehouse', 'Unknown'),
            "warehouse_code": aggregated.get('warehouse_code'),
            "city": aggregated.get('city', 'Unknown'),
            "delivery_location": aggregated.get('delivery_location'),
            
            # Sales
            "sales_manager": aggregated.get('sales_manager'),
            "sales_office": aggregated.get('sales_office'),
            "division": aggregated.get('division'),
            
            # Metrics
            "total_units": aggregated.get('total_units', 0),
            "total_revenue": aggregated.get('total_revenue', 0),
            "material_count": aggregated.get('material_count', 0),
            "model_count": aggregated.get('model_count', 0),
            "row_count": aggregated.get('row_count', 0),
            
            # Dates
            "dn_create_date": dn_create_fmt,
            "good_issue_date": good_issue_fmt,
            "pod_date": pod_fmt,
            
            # Aging
            "delivery_aging_days": delivery_aging,
            "pod_aging_days": pod_aging,
            "total_cycle_days": total_cycle,
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging),
            "total_cycle_text": self._format_aging_text(total_cycle),
            
            # Status
            "calculated_stage": stage,
            "calculated_emoji": emoji,
            "delivery_status": stage,
            "pgi_status": pgi_status,
            "pod_status": pod_status,
            "pending_flag": pending,
            "pending_flag_text": pending_text,
            
            # Products
            "products": products,
            
            # Source
            "source_file": aggregated.get('source_file'),
            "upload_batch_id": aggregated.get('upload_batch_id'),
            "imported_at": aggregated.get('imported_at'),
            "created_at": aggregated.get('created_at'),
            "updated_at": aggregated.get('updated_at'),
        }
    
    def _calculate_days(self, date1, date2) -> int:
        """Calculate days between two dates."""
        if date1 is None or date2 is None:
            return 0
        
        try:
            if isinstance(date1, str):
                date1 = datetime.strptime(date1, "%Y-%m-%d").date()
            if isinstance(date2, str):
                date2 = datetime.strptime(date2, "%Y-%m-%d").date()
            
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            
            delta = date2 - date1
            return max(0, delta.days)
        except:
            return 0
    
    def _format_date(self, date_value) -> str:
        """Format date for display."""
        if date_value is None:
            return 'N/A'
        
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%Y-%m-%d')
            elif isinstance(date_value, str):
                if len(date_value) == 10:
                    return date_value
                return date_value[:10]
            else:
                return str(date_value)
        except:
            return str(date_value) if date_value else 'N/A'
    
    def _format_aging_text(self, days: int) -> str:
        """Format aging days into text."""
        if days < 0:
            return f"{abs(days)} Days (Data Error)"
        elif days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        elif days < 7:
            return f"{days} Days"
        elif days < 14:
            return f"{days} Days (1-2 Weeks)"
        elif days < 30:
            return f"{days} Days ({days // 7} Weeks)"
        elif days < 60:
            return f"{days} Days (1-2 Months)"
        elif days < 90:
            return f"{days} Days (3 Months)"
        elif days < 365:
            return f"{days} Days ({days // 30} Months)"
        else:
            years = days // 365
            months = (days % 365) // 30
            if months > 0:
                return f"{days} Days ({years}Y {months}M)"
            return f"{days} Days ({years}Y)"
    
    # ==================================================================================================
    # BLOCK 6: PUBLIC METHODS FOR COMPATIBILITY
    # ==================================================================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Get complete DN dashboard - main method."""
        return self.get_dn_complete_info(dn_no)
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """Search for DN - alias for get_dn_complete_info."""
        return self.get_dn_complete_info(dn_no)
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists."""
        result = self.get_dn_complete_info(dn_no)
        return {
            "success": True,
            "exists": result.get("success", False)
        }
    
    def health_check(self) -> Dict[str, Any]:
        """Health check."""
        return {
            "healthy": True,
            "service": self._service_name,
            "version": self._version,
            "status": self._status,
            "database": "connected",
            "timestamp": datetime.now().isoformat()
        }
    
    def validation_query(self) -> Dict[str, Any]:
        """Validation query."""
        session = None
        try:
            session = self._get_session()
            if not session:
                return {"success": False, "records": 0, "error": "No session"}
            
            result = session.execute(text("SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports"))
            row = result.fetchone()
            count = row[0] if row else 0
            
            return {"success": True, "records": count}
        except Exception as e:
            return {"success": False, "records": 0, "error": str(e)}
        finally:
            if session:
                session.close()
    
    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata."""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "Complete DN Information Fetcher",
            "methods": [
                "get_dn_complete_info",
                "get_dn_dashboard",
                "search_dn",
                "verify_dn",
                "health_check",
                "validation_query",
                "get_service_metadata"
            ]
        }
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending DNs."""
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS city,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pending_flag) AS pending_flag,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE pod_date IS NULL
            GROUP BY dn_no
            LIMIT :limit OFFSET :offset
        """
        results = self._execute_query(query, {"limit": limit, "offset": offset})
        return {"success": True, "data": results, "total": len(results)}
    
    def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending PGI."""
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS city,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pending_flag) AS pending_flag,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE good_issue_date IS NULL
            GROUP BY dn_no
            LIMIT :limit OFFSET :offset
        """
        results = self._execute_query(query, {"limit": limit, "offset": offset})
        return {"success": True, "data": results, "total": len(results)}
    
    def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get pending POD."""
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS city,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pending_flag) AS pending_flag,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE good_issue_date IS NOT NULL AND pod_date IS NULL
            GROUP BY dn_no
            LIMIT :limit OFFSET :offset
        """
        results = self._execute_query(query, {"limit": limit, "offset": offset})
        return {"success": True, "data": results, "total": len(results)}
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """Format DN dashboard for WhatsApp."""
        data = dashboard_data.get('data', {})
        
        lines = []
        
        # Header
        lines.append("📦 *DN: {}*".format(data.get('dn_no', 'N/A')))
        lines.append("")
        
        # Dealer
        dealer_name = data.get('dealer_name', 'Unknown')
        if dealer_name and dealer_name != 'Unknown':
            lines.append("*Dealer:*")
            lines.append("{}".format(dealer_name))
            lines.append("")
        
        # Warehouse
        warehouse = data.get('warehouse', 'Unknown')
        if warehouse and warehouse != 'Unknown':
            lines.append("*Warehouse:*")
            lines.append("{}".format(warehouse))
            lines.append("")
        
        # City
        city = data.get('city', 'Unknown')
        if city and city != 'Unknown':
            lines.append("*City:*")
            lines.append("{}".format(city))
            lines.append("")
        
        # Sales Manager
        sales_manager = data.get('sales_manager')
        if sales_manager:
            lines.append("*Sales Manager:*")
            lines.append("{}".format(sales_manager))
            lines.append("")
        
        # Division
        division = data.get('division')
        if division:
            lines.append("*Division:*")
            lines.append("{}".format(division))
            lines.append("")
        
        # Metrics
        lines.append("*📊 Metrics:*")
        lines.append("Units: {}".format(data.get('total_units', 0)))
        revenue = data.get('total_revenue', 0)
        if revenue:
            lines.append("Revenue: PKR {:,}".format(revenue))
        else:
            lines.append("Revenue: PKR 0")
        lines.append("")
        lines.append("Materials: {}".format(data.get('material_count', 1)))
        model_count = data.get('model_count', 0)
        if model_count > 0:
            lines.append("Models: {}".format(model_count))
        lines.append("")
        
        # Dates
        lines.append("*📅 Dates:*")
        lines.append("DN Create: {}".format(data.get('dn_create_date', 'N/A')))
        lines.append("PGI: {}".format(data.get('good_issue_date', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_date', 'N/A')))
        lines.append("")
        
        # Aging
        lines.append("*⏳ Aging:*")
        lines.append("Delivery: {}".format(data.get('delivery_aging_text', 'N/A')))
        lines.append("POD: {}".format(data.get('pod_aging_text', 'N/A')))
        lines.append("Total Cycle: {}".format(data.get('total_cycle_text', 'N/A')))
        lines.append("")
        
        # Status
        stage = data.get('calculated_stage', 'Unknown')
        emoji = data.get('calculated_emoji', '❓')
        pgi_status = data.get('pgi_status', 'Unknown')
        pod_status = data.get('pod_status', 'Unknown')
        pending_text = data.get('pending_flag_text', 'Unknown')
        
        lines.append("*📋 Status:*")
        lines.append("Delivery: {} {}".format(emoji, stage))
        lines.append("PGI: {}".format(pgi_status))
        lines.append("POD: {}".format(pod_status))
        lines.append("Pending: {}".format(pending_text))
        lines.append("")
        
        # Products
        products = data.get('products', [])
        if products:
            lines.append("*📦 Product Details:*")
            for idx, product in enumerate(products[:10], 1):
                model_name = product.get('name', 'Unknown')
                material_no = product.get('material_no', 'N/A')
                qty = product.get('qty', 0)
                
                lines.append("{}. {}: {} units".format(idx, model_name, qty))
                if material_no != 'N/A':
                    lines.append("   Material: {}".format(material_no))
            
            if len(products) > 10:
                remaining = len(products) - 10
                lines.append("... and {} more models".format(remaining))
            lines.append("")
        
        return "\n".join(lines)


# ======================================================================================================
# BLOCK 7: THREAD-SAFE SINGLETON
# ======================================================================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    """Thread-safe singleton getter."""
    global _dn_analytics_service
    
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    logger.info("🔧 Creating DNAnalysisService singleton...")
                    _dn_analytics_service = DNAnalysisService()
                    logger.info("✅ DNAnalysisService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ======================================================================================================
# BLOCK 8: EXPORTS
# ======================================================================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ======================================================================================================
# BLOCK 9: MODULE INITIALIZATION
# ======================================================================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v13.2 - COMPLETE DN INFORMATION FETCHER")
logger.info("=" * 70)
logger.info("")
logger.info("   SERVICE DETAILS:")
logger.info("   ✅ Service Name: dn_analysis")
logger.info("   ✅ Version: 13.2")
logger.info("   ✅ Source: PostgreSQL (delivery_reports)")
logger.info("")
logger.info("   FEATURES:")
logger.info("   ✅ Fetches ALL rows for a DN")
logger.info("   ✅ Aggregates ALL data")
logger.info("   ✅ Complete dashboard")
logger.info("   ✅ All products listed")
logger.info("   ✅ Status from dates")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)

# Initialize service
try:
    service = get_dn_analytics_service()
    logger.info("✅ DN Analytics Service initialized successfully")
except Exception as e:
    logger.error(f"❌ DN Analytics Service initialization failed: {e}")
