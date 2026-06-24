# ==========================================================
# FILE: app/services/dn_analysis.py
# ==========================================================
# PURPOSE: DN Analytics Engine - Direct PostgreSQL Integration
# SOURCE: delivery_reports table ONLY
# VERSION: 1.0 - PRODUCTION GRADE
#
# This is the FIRST business service activated by ai_provider_service.py.
# When this file becomes READY, WhatsApp immediately starts answering
# DN-related questions.
#
# RULES:
# - 100% PostgreSQL Integration
# - No CSV, Excel, JSON, Mock Data, Hardcoded Data
# - All data comes directly from DeliveryReport model
# - DN Count = COUNT(DISTINCT dn_no)
# - Units = SUM(dn_qty)
# - Revenue = SUM(dn_amount)
# ==========================================================

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, date
from sqlalchemy import text, func, and_, or_, desc, asc
from sqlalchemy.orm import Session
import threading
import re

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None


# ==========================================================
# BLOCK 2: DNAnalysisService CLASS
# ==========================================================

class DNAnalysisService:
    """
    DN Analytics Service - Direct PostgreSQL Connection.
    
    This service connects directly to PostgreSQL without any repository layer.
    All data comes from delivery_reports table.
    
    This is the FIRST business service activated by ai_provider_service.py.
    """
    
    def __init__(self):
        """Initialize DN Analytics Service."""
        self._service_name = "dn_analysis"
        self._version = "1.0"
        self._status = "READY"
        self._session = None
        logger.info("✅ DNAnalysisService initialized")
    
    # ==========================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ==========================================================
    
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
        session = self._get_session()
        if not session:
            return []
        
        try:
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            session.close()
            return rows
        except Exception as e:
            logger.error(f"❌ Query execution failed: {e}")
            session.close()
            return []
    
    def _execute_orm_query(self, query) -> List[Any]:
        """Execute ORM query and return results."""
        session = self._get_session()
        if not session:
            return []
        
        try:
            result = session.execute(query)
            rows = result.fetchall()
            session.close()
            return rows
        except Exception as e:
            logger.error(f"❌ ORM query execution failed: {e}")
            session.close()
            return []
    
    # ==========================================================
    # BLOCK 4: HEALTH & VALIDATION METHODS
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """
        Validate service readiness.
        
        Must verify:
        - Database Connection
        - DeliveryReport Table Exists
        - Required Columns Exist
        - Query Execution Works
        """
        result = {
            "healthy": False,
            "service": self._service_name,
            "database": "disconnected",
            "errors": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Check 1: Database connection
            session = self._get_session()
            if not session:
                result["errors"].append("SessionLocal not available")
                return result
            
            # Check 2: Test connection
            try:
                session.execute(text("SELECT 1"))
                result["database"] = "connected"
            except Exception as e:
                result["errors"].append(f"Connection test failed: {str(e)}")
                session.close()
                return result
            
            # Check 3: Table exists
            from sqlalchemy import inspect
            inspector = inspect(session.bind)
            tables = inspector.get_table_names()
            
            if "delivery_reports" not in tables:
                result["errors"].append("Table 'delivery_reports' does not exist")
                session.close()
                return result
            
            # Check 4: Required columns exist
            required_columns = [
                "dn_no", "customer_name", "warehouse", "ship_to_city",
                "dn_qty", "dn_amount", "dn_create_date", "good_issue_date",
                "pod_date", "delivery_status", "pgi_status", "pod_status",
                "pending_flag"
            ]
            
            columns = [col["name"] for col in inspector.get_columns("delivery_reports")]
            missing_columns = [col for col in required_columns if col not in columns]
            
            if missing_columns:
                result["errors"].append(f"Missing columns: {missing_columns}")
                session.close()
                return result
            
            # Check 5: Test query execution
            try:
                test_query = "SELECT COUNT(*) as count FROM delivery_reports LIMIT 1"
                session.execute(text(test_query))
            except Exception as e:
                result["errors"].append(f"Test query failed: {str(e)}")
                session.close()
                return result
            
            session.close()
            
            # All checks passed
            result["healthy"] = True
            result["database"] = "connected"
            
            logger.info("✅ DN Analytics Service health check passed")
            return result
            
        except Exception as e:
            result["errors"].append(f"Health check failed: {str(e)}")
            logger.error(f"❌ Health check failed: {e}")
            return result
    
    def validation_query(self) -> Dict[str, Any]:
        """
        Used by ai_provider_service.py for validation.
        
        Executes COUNT(*) against DeliveryReport.
        """
        result = {
            "success": False,
            "records": 0,
            "error": None
        }
        
        try:
            session = self._get_session()
            if not session:
                result["error"] = "SessionLocal not available"
                return result
            
            count = session.query(func.count(DeliveryReport.dn_no)).scalar() or 0
            session.close()
            
            result["success"] = True
            result["records"] = count
            
            logger.info(f"✅ Validation query successful: {count} records")
            return result
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"❌ Validation query failed: {e}")
            return result
    
    def get_service_metadata(self) -> Dict[str, Any]:
        """Get service metadata for ai_provider_service.py."""
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "DN Analytics Service - PostgreSQL Integration",
            "methods": [
                "health_check",
                "validation_query",
                "get_service_metadata",
                "search_dn",
                "verify_dn",
                "get_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod",
                "calculate_delivery_aging",
                "calculate_pod_aging",
                "calculate_total_cycle"
            ]
        }
    
    # ==========================================================
    # BLOCK 5: DN SEARCH & VERIFICATION
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        """
        Search for a specific DN.
        
        Returns full DN details with aggregation.
        """
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS customer_name,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS ship_to_city,
                SUM(dn_qty) AS dn_qty,
                SUM(dn_amount) AS dn_amount,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MAX(division) AS division,
                MAX(customer_code) AS customer_code,
                MAX(dealer_code) AS dealer_code,
                MAX(sales_office) AS sales_office,
                MAX(sales_manager) AS sales_manager,
                MAX(dn_work) AS dn_work,
                MAX(order_type) AS order_type,
                MAX(storage_location) AS storage_location,
                MAX(delivery_location) AS delivery_location,
                MAX(remarks) AS remarks,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE dn_no = :dn_no
            GROUP BY dn_no
        """
        
        results = self._execute_query(query, {"dn_no": dn_no})
        
        if not results:
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = results[0]
        
        # Calculate aging
        data['delivery_aging_days'] = self.calculate_delivery_aging(
            data.get('dn_create_date'),
            data.get('good_issue_date')
        )
        data['pod_aging_days'] = self.calculate_pod_aging(
            data.get('good_issue_date'),
            data.get('pod_date')
        )
        data['total_cycle_days'] = self.calculate_total_cycle(
            data.get('dn_create_date'),
            data.get('pod_date')
        )
        
        return {"success": True, "data": data}
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        """Verify if DN exists."""
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports WHERE dn_no = :dn_no"
        results = self._execute_query(query, {"dn_no": dn_no})
        exists = results and results[0].get('count', 0) > 0
        
        return {"success": True, "exists": exists}
    
    # ==========================================================
    # BLOCK 6: DN DASHBOARD - PRIMARY WHATSAPP DASHBOARD
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Primary WhatsApp DN Dashboard.
        
        Returns complete DN analytics including:
        - DN Number
        - DN Creation Date
        - PGI Date
        - POD Date
        - Dealer Name
        - Dealer Code
        - Customer Code
        - Warehouse
        - City
        - Sales Manager
        - Division
        - Total Units
        - Revenue
        - Delivery Status
        - PGI Status
        - POD Status
        - Delivery Aging
        - POD Aging
        - Total Cycle
        - Pending Flag
        """
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        query = """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(ship_to_city) AS city,
                MAX(sales_manager) AS sales_manager,
                MAX(division) AS division,
                MAX(sales_office) AS sales_office,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                MAX(dn_work) AS dn_work,
                MAX(order_type) AS order_type,
                MAX(storage_location) AS storage_location,
                MAX(delivery_location) AS delivery_location,
                MAX(remarks) AS remarks,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE dn_no = :dn_no
            GROUP BY dn_no
        """
        
        results = self._execute_query(query, {"dn_no": dn_no})
        
        if not results:
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = results[0]
        
        # Format dates for display
        for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
            if data.get(date_field):
                if isinstance(data[date_field], (datetime, date)):
                    data[date_field] = data[date_field].strftime("%Y-%m-%d")
        
        # Calculate aging
        delivery_aging = self.calculate_delivery_aging(
            data.get('dn_create_date'),
            data.get('good_issue_date')
        )
        pod_aging = self.calculate_pod_aging(
            data.get('good_issue_date'),
            data.get('pod_date')
        )
        total_cycle = self.calculate_total_cycle(
            data.get('dn_create_date'),
            data.get('pod_date')
        )
        
        data['delivery_aging_days'] = delivery_aging
        data['pod_aging_days'] = pod_aging
        data['total_cycle_days'] = total_cycle
        
        # Add aging text descriptions
        data['delivery_aging_text'] = self._format_aging_text(delivery_aging)
        data['pod_aging_text'] = self._format_aging_text(pod_aging)
        data['total_cycle_text'] = self._format_aging_text(total_cycle)
        
        # Add status emoji
        status = data.get('delivery_status', '')
        if status in ['Completed', 'Delivered', 'Closed']:
            data['status_emoji'] = '✅'
            data['status_text'] = 'Delivered'
        elif status in ['In Transit', 'Transit']:
            data['status_emoji'] = '🚚'
            data['status_text'] = 'In Transit'
        elif status in ['Pending', 'Open']:
            data['status_emoji'] = '⏳'
            data['status_text'] = 'Pending'
        else:
            data['status_emoji'] = '❓'
            data['status_text'] = status or 'Unknown'
        
        # Add PGI status text
        pgi_status = data.get('pgi_status', '')
        if pgi_status == 'Completed':
            data['pgi_status_text'] = '✅ Completed'
        else:
            data['pgi_status_text'] = '⏳ Pending'
        
        # Add POD status text
        pod_status = data.get('pod_status', '')
        if pod_status == 'Completed':
            data['pod_status_text'] = '✅ Completed'
        else:
            data['pod_status_text'] = '⏳ Pending'
        
        # Add pending flag text
        pending_flag = data.get('pending_flag', 'N')
        data['pending_flag_text'] = '⚠️ Yes' if pending_flag == 'Y' else '✅ No'
        
        return {"success": True, "data": data}
    
    # ==========================================================
    # BLOCK 7: PENDING METHODS
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get all pending DNs.
        
        Pending DN if:
        - good_issue_date IS NULL
        - OR pgi_status = 'Pending'
        - OR pending_flag = 'Y'
        - OR delivery_status = 'Pending'
        """
        try:
            # Get total count
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR delivery_status = 'Pending'
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending DNs found"
                }
            
            # Get pending DNs with aggregation
            pending_query = """
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
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                   OR pending_flag = 'Y'
                   OR delivery_status = 'Pending'
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            # Format results
            formatted_results = []
            for row in results:
                # Calculate aging
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                # Format dates
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag": row.get('pending_flag') or "Y",
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": self._format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending DNs: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get all pending PGI deliveries.
        
        Pending PGI if:
        - good_issue_date IS NULL
        - OR pgi_status = 'Pending'
        """
        try:
            # Get total count
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending PGI found"
                }
            
            # Get pending PGI with aggregation
            pending_query = """
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
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR pgi_status = 'Pending'
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            # Format results
            formatted_results = []
            for row in results:
                # Calculate aging
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
                # Format dates
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "Pending",
                    "pgi_status": row.get('pgi_status') or "Pending",
                    "pod_status": row.get('pod_status') or "Unknown",
                    "pending_flag": row.get('pending_flag') or "Y",
                    "delivery_aging_days": delivery_aging,
                    "delivery_aging_text": self._format_aging_text(delivery_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending PGI: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """
        Get all pending POD deliveries.
        
        Pending POD if:
        - pod_date IS NULL
        - OR pod_status = 'Pending'
        - AND good_issue_date IS NOT NULL
        """
        try:
            # Get total count
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND (pod_date IS NULL OR pod_status = 'Pending')
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending POD found"
                }
            
            # Get pending POD with aggregation
            pending_query = """
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
                    MAX(pgi_status) AS pgi_status,
                    MAX(pod_status) AS pod_status,
                    MAX(pending_flag) AS pending_flag,
                    MAX(sales_manager) AS sales_manager,
                    MAX(division) AS division,
                    COUNT(*) AS material_count
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND (pod_date IS NULL OR pod_status = 'Pending')
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            # Format results
            formatted_results = []
            for row in results:
                # Calculate aging
                pod_aging = self.calculate_pod_aging(
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                # Format dates
                for date_field in ['dn_create_date', 'good_issue_date', 'pod_date']:
                    if row.get(date_field):
                        if isinstance(row[date_field], (datetime, date)):
                            row[date_field] = row[date_field].strftime("%Y-%m-%d")
                
                formatted_row = {
                    "dn_no": row.get('dn_no'),
                    "dealer_name": row.get('dealer_name') or "Unknown Dealer",
                    "warehouse": row.get('warehouse') or "Unknown Warehouse",
                    "city": row.get('city') or "Unknown City",
                    "total_units": int(row.get('total_units') or 0),
                    "total_revenue": float(row.get('total_revenue') or 0),
                    "dn_create_date": row.get('dn_create_date'),
                    "good_issue_date": row.get('good_issue_date'),
                    "pod_date": row.get('pod_date'),
                    "delivery_status": row.get('delivery_status') or "In Transit",
                    "pgi_status": row.get('pgi_status') or "Completed",
                    "pod_status": row.get('pod_status') or "Pending",
                    "pending_flag": row.get('pending_flag') or "N",
                    "pod_aging_days": pod_aging,
                    "pod_aging_text": self._format_aging_text(pod_aging),
                    "sales_manager": row.get('sales_manager'),
                    "division": row.get('division'),
                    "material_count": row.get('material_count', 1)
                }
                formatted_results.append(formatted_row)
            
            return {
                "success": True,
                "data": formatted_results,
                "total": total_pending,
                "limit": limit,
                "offset": offset,
                "returned": len(formatted_results)
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending POD: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    # ==========================================================
    # BLOCK 8: AGING CALCULATION METHODS
    # ==========================================================
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        """
        Calculate delivery aging.
        
        IF good_issue_date exists:
            good_issue_date - dn_create_date
        ELSE:
            CURRENT_DATE - dn_create_date
        """
        try:
            # Parse dates if they are strings
            if isinstance(dn_create_date, str):
                dn_create_date = datetime.fromisoformat(dn_create_date.replace('Z', '+00:00'))
            if isinstance(good_issue_date, str):
                good_issue_date = datetime.fromisoformat(good_issue_date.replace('Z', '+00:00'))
            
            # If no create date, return 0
            if not dn_create_date:
                return 0
            
            # If good_issue_date exists, use it
            if good_issue_date:
                if isinstance(dn_create_date, datetime) and isinstance(good_issue_date, datetime):
                    return (good_issue_date.date() - dn_create_date.date()).days
                elif isinstance(dn_create_date, date) and isinstance(good_issue_date, date):
                    return (good_issue_date - dn_create_date).days
            
            # Otherwise use current date
            if isinstance(dn_create_date, datetime):
                return (datetime.now().date() - dn_create_date.date()).days
            elif isinstance(dn_create_date, date):
                return (datetime.now().date() - dn_create_date).days
            
            return 0
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate delivery aging: {e}")
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        """
        Calculate POD aging.
        
        IF pod_date exists:
            pod_date - good_issue_date
        ELSE:
            CURRENT_DATE - good_issue_date
        """
        try:
            # Parse dates if they are strings
            if isinstance(good_issue_date, str):
                good_issue_date = datetime.fromisoformat(good_issue_date.replace('Z', '+00:00'))
            if isinstance(pod_date, str):
                pod_date = datetime.fromisoformat(pod_date.replace('Z', '+00:00'))
            
            # If no good_issue_date, return 0
            if not good_issue_date:
                return 0
            
            # If pod_date exists, use it
            if pod_date:
                if isinstance(good_issue_date, datetime) and isinstance(pod_date, datetime):
                    return (pod_date.date() - good_issue_date.date()).days
                elif isinstance(good_issue_date, date) and isinstance(pod_date, date):
                    return (pod_date - good_issue_date).days
            
            # Otherwise use current date
            if isinstance(good_issue_date, datetime):
                return (datetime.now().date() - good_issue_date.date()).days
            elif isinstance(good_issue_date, date):
                return (datetime.now().date() - good_issue_date).days
            
            return 0
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate POD aging: {e}")
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        """
        Calculate total cycle time.
        
        IF pod_date exists:
            pod_date - dn_create_date
        ELSE:
            CURRENT_DATE - dn_create_date
        """
        try:
            # Parse dates if they are strings
            if isinstance(dn_create_date, str):
                dn_create_date = datetime.fromisoformat(dn_create_date.replace('Z', '+00:00'))
            if isinstance(pod_date, str):
                pod_date = datetime.fromisoformat(pod_date.replace('Z', '+00:00'))
            
            # If no create date, return 0
            if not dn_create_date:
                return 0
            
            # If pod_date exists, use it
            if pod_date:
                if isinstance(dn_create_date, datetime) and isinstance(pod_date, datetime):
                    return (pod_date.date() - dn_create_date.date()).days
                elif isinstance(dn_create_date, date) and isinstance(pod_date, date):
                    return (pod_date - dn_create_date).days
            
            # Otherwise use current date
            if isinstance(dn_create_date, datetime):
                return (datetime.now().date() - dn_create_date.date()).days
            elif isinstance(dn_create_date, date):
                return (datetime.now().date() - dn_create_date).days
            
            return 0
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to calculate total cycle: {e}")
            return 0
    
    # ==========================================================
    # BLOCK 9: HELPER METHODS
    # ==========================================================
    
    def _format_aging_text(self, days: int) -> str:
        """Format aging days into human readable text."""
        if days <= 0:
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
        else:
            return f"{days} Days ({days // 30} Months)"


# ==========================================================
# BLOCK 10: THREAD-SAFE SINGLETON
# ==========================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    """Thread-safe singleton getter."""
    global _dn_analytics_service
    
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    _dn_analytics_service = DNAnalysisService()
                    logger.info("✅ DNAnalysisService singleton initialized")
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ==========================================================
# BLOCK 11: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 12: MODULE INITIALIZATION
# ==========================================================

logger.debug("=" * 70)
logger.debug("DNAnalysisService v1.0 - PRODUCTION GRADE")
logger.debug("=" * 70)
logger.debug("")
logger.debug("   SERVICE DETAILS:")
logger.debug("   ✅ Service Name: dn_analysis")
logger.debug("   ✅ Version: 1.0")
logger.debug("   ✅ Status: READY")
logger.debug("   ✅ Source: PostgreSQL (delivery_reports)")
logger.debug("")
logger.debug("   AVAILABLE METHODS:")
logger.debug("   ✅ health_check()")
logger.debug("   ✅ validation_query()")
logger.debug("   ✅ get_service_metadata()")
logger.debug("   ✅ search_dn()")
logger.debug("   ✅ verify_dn()")
logger.debug("   ✅ get_dn_dashboard()")
logger.debug("   ✅ get_pending_dns()")
logger.debug("   ✅ get_pending_pgi()")
logger.debug("   ✅ get_pending_pod()")
logger.debug("   ✅ calculate_delivery_aging()")
logger.debug("   ✅ calculate_pod_aging()")
logger.debug("   ✅ calculate_total_cycle()")
logger.debug("")
logger.debug("   RULES:")
logger.debug("   ✅ DN Count = COUNT(DISTINCT dn_no)")
logger.debug("   ✅ Units = SUM(dn_qty)")
logger.debug("   ✅ Revenue = SUM(dn_amount)")
logger.debug("   ✅ All data from PostgreSQL")
logger.debug("   ❌ No CSV, Excel, JSON, Mock Data")
logger.debug("")
logger.debug("   STATUS: ✅ PRODUCTION READY")
logger.debug("=" * 70)
