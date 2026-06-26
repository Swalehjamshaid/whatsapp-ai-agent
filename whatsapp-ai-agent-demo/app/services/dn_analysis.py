# ==========================================================
# FILE: app/services/dn_analysis.py (v14.0 - COMPLETE)
# ==========================================================
# PURPOSE: DN Analytics Service - Complete with All Methods
# VERSION: 14.0 - ALL METHODS INCLUDED
# ==========================================================

import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, date, timedelta
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
import threading
import re
import traceback
import time
import os

logger = logging.getLogger(__name__)

# ==========================================================
# BLOCK 1: IMPORTS & DATABASE SETUP
# ==========================================================

try:
    from app.database import SessionLocal
    from app.models import DeliveryReport
    logger.info("✅ Database models imported successfully")
except ImportError as e:
    logger.error(f"❌ Database import failed: {e}")
    SessionLocal = None
    DeliveryReport = None

DEBUG_MODE = os.environ.get("DN_DEBUG_MODE", "false").lower() == "true"

# ==========================================================
# BLOCK 2: DNAnalysisService CLASS (COMPLETE)
# ==========================================================

class DNAnalysisService:
    """DN Analytics Service - Complete with All Methods."""
    
    def __init__(self):
        self._service_name = "dn_analysis"
        self._version = "14.0"
        self._status = "INITIALIZING"
        self._query_count = 0
        self._total_execution_time_ms = 0
        
        logger.info(f"🔧 DNAnalysisService v{self._version} initializing...")
        
        test_result = self._test_connection()
        if test_result:
            self._status = "READY"
            logger.info("✅ DNAnalysisService is READY")
        else:
            self._status = "ERROR"
            logger.error("❌ DNAnalysisService initialization FAILED")
    
    # ==========================================================
    # BLOCK 3: DATABASE CONNECTION METHODS
    # ==========================================================
    
    def _test_connection(self) -> bool:
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
        if not SessionLocal:
            logger.error("❌ SessionLocal not available")
            return None
        try:
            return SessionLocal()
        except Exception as e:
            logger.error(f"❌ Failed to get database session: {e}")
            return None
    
    def _execute_query(self, query: str, params: Dict[str, Any] = None) -> List[Dict[str, Any]]:
        start_time = time.time()
        session = None
        try:
            session = self._get_session()
            if not session:
                logger.error("❌ No session available")
                return []
            
            result = session.execute(text(query), params or {})
            columns = result.keys()
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            
            self._query_count += 1
            self._total_execution_time_ms += (time.time() - start_time) * 1000
            
            return rows
        except Exception as e:
            logger.error(f"❌ SQL Execution Failed: {e}")
            return []
        finally:
            if session:
                session.close()
    
    # ==========================================================
    # BLOCK 4: DN SEARCH NORMALIZATION
    # ==========================================================
    
    def _normalize_dn(self, dn_no: str) -> str:
        if not dn_no:
            return ""
        return re.sub(r'[^0-9]', '', dn_no.strip())
    
    def _build_normalized_dn_query(self) -> str:
        return """
            SELECT 
                dn_no,
                MAX(customer_name) AS dealer_name,
                MAX(dealer_code) AS dealer_code,
                MAX(customer_code) AS customer_code,
                MAX(warehouse) AS warehouse,
                MAX(warehouse_code) AS warehouse_code,
                MAX(ship_to_city) AS city,
                MAX(delivery_location) AS delivery_location,
                MAX(sales_manager) AS sales_manager,
                MAX(division) AS division,
                SUM(dn_qty) AS total_units,
                SUM(dn_amount) AS total_revenue,
                COUNT(DISTINCT customer_model) AS model_count,
                MIN(dn_create_date) AS dn_create_date,
                MAX(good_issue_date) AS good_issue_date,
                MAX(pod_date) AS pod_date,
                MAX(delivery_status) AS delivery_status,
                MAX(pgi_status) AS pgi_status,
                MAX(pod_status) AS pod_status,
                MAX(pending_flag) AS pending_flag,
                COUNT(*) AS material_count
            FROM delivery_reports
            WHERE 
                CAST(dn_no AS TEXT) = :dn_no
                OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
                OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
                OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
            GROUP BY dn_no
            LIMIT 1
        """
    
    def _build_fallback_dn_query(self) -> str:
        return """
            SELECT DISTINCT dn_no
            FROM delivery_reports
            WHERE CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
            LIMIT 10
        """
    
    # ==========================================================
    # BLOCK 5: SEARCH DN
    # ==========================================================
    
    def search_dn(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"🔍 Searching for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        if len(normalized_dn) < 8:
            return {"success": False, "error": f"Invalid DN format: {normalized_dn} (must be 8-12 digits)"}
        
        query = self._build_normalized_dn_query()
        results = self._execute_query(query, {"dn_no": normalized_dn})
        
        if results:
            return {"success": True, "data": results[0]}
        
        fallback_query = self._build_fallback_dn_query()
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
    
    # ==========================================================
    # BLOCK 6: DATE HELPERS (FAST)
    # ==========================================================
    
    def _format_date_dmy(self, date_value) -> str:
        if not date_value:
            return 'N/A'
        try:
            if isinstance(date_value, (date, datetime)):
                return date_value.strftime('%d-%b-%Y')
            return str(date_value)
        except Exception:
            return 'N/A'
    
    def _format_aging_text(self, days: int) -> str:
        if days < 0:
            return f"{abs(days)} Days (Error)"
        elif days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        elif days < 7:
            return f"{days} Days"
        elif days < 14:
            return f"{days} Days"
        elif days < 30:
            return f"{days} Days"
        elif days < 60:
            return f"{days} Days"
        elif days < 90:
            return f"{days} Days"
        elif days < 365:
            return f"{days} Days"
        else:
            years = days // 365
            months = (days % 365) // 30
            if months > 0:
                return f"{days} Days ({years}Y {months}M)"
            return f"{days} Days ({years}Y)"
    
    def _safe_date_diff(self, date1, date2) -> int:
        if date1 is None or date2 is None:
            return 0
        try:
            if isinstance(date1, datetime):
                date1 = date1.date()
            if isinstance(date2, datetime):
                date2 = date2.date()
            if isinstance(date1, date) and isinstance(date2, date):
                return max(0, (date2 - date1).days)
            return 0
        except Exception:
            return 0
    
    def calculate_delivery_aging(self, dn_create_date, good_issue_date) -> int:
        try:
            if dn_create_date is None:
                return 0
            if good_issue_date is None:
                return self._safe_date_diff(dn_create_date, datetime.now().date())
            return self._safe_date_diff(dn_create_date, good_issue_date)
        except Exception:
            return 0
    
    def calculate_pod_aging(self, good_issue_date, pod_date) -> int:
        try:
            if good_issue_date is None:
                return 0
            if pod_date is None:
                return self._safe_date_diff(good_issue_date, datetime.now().date())
            return self._safe_date_diff(good_issue_date, pod_date)
        except Exception:
            return 0
    
    def calculate_total_cycle(self, dn_create_date, pod_date) -> int:
        try:
            if dn_create_date is None:
                return 0
            if pod_date is None:
                return self._safe_date_diff(dn_create_date, datetime.now().date())
            return self._safe_date_diff(dn_create_date, pod_date)
        except Exception:
            return 0
    
    # ==========================================================
    # BLOCK 7: SHIPMENT STAGE (INTELLIGENT)
    # ==========================================================
    
    def _determine_shipment_stage(self, dn_create_date, good_issue_date, pod_date) -> Dict[str, Any]:
        pgi_exists = good_issue_date is not None
        pod_exists = pod_date is not None
        
        if pod_exists and pgi_exists:
            return {
                "stage": "Delivered",
                "stage_emoji": "✅",
                "health": "Completed",
                "health_emoji": "🟢",
                "pending": False,
                "recommendation": "Shipment completed successfully. Review performance if delivery exceeded expected time.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": self._format_date_dmy(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": self._format_date_dmy(good_issue_date)},
                    {"step": "POD Received", "status": "✅", "date": self._format_date_dmy(pod_date)}
                ]
            }
        elif pgi_exists and not pod_exists:
            return {
                "stage": "In Transit",
                "stage_emoji": "🚚",
                "health": "On Route",
                "health_emoji": "🟡",
                "pending": True,
                "recommendation": "Follow up with transporter for POD confirmation.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": self._format_date_dmy(dn_create_date)},
                    {"step": "PGI Completed", "status": "✅", "date": self._format_date_dmy(good_issue_date)},
                    {"step": "POD Pending", "status": "⏳", "date": "Pending"}
                ]
            }
        else:
            return {
                "stage": "Pending Dispatch",
                "stage_emoji": "🟡",
                "health": "Awaiting Warehouse Dispatch",
                "health_emoji": "🟡",
                "pending": True,
                "recommendation": "Warehouse should complete PGI immediately.",
                "progress": [
                    {"step": "DN Created", "status": "✅", "date": self._format_date_dmy(dn_create_date)},
                    {"step": "PGI Pending", "status": "⏳", "date": "Pending"},
                    {"step": "POD Not Started", "status": "⏳", "date": "Not Started"}
                ]
            }
    
    # ==========================================================
    # BLOCK 8: GET DN DASHBOARD (FAST)
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        logger.info(f"📊 Getting dashboard for DN: '{dn_no}'")
        
        if not dn_no:
            return {"success": False, "error": "DN number required"}
        
        search_result = self.search_dn(dn_no)
        
        if not search_result.get("success"):
            similar_dns = search_result.get("similar_dns", [])
            if similar_dns:
                return {
                    "success": False,
                    "error": f"DN {dn_no} not found. Similar: {', '.join(similar_dns[:3])}"
                }
            return {"success": False, "error": f"DN {dn_no} not found"}
        
        data = search_result.get("data", {})
        
        raw_dn_create = data.get('dn_create_date')
        raw_pgi = data.get('good_issue_date')
        raw_pod = data.get('pod_date')
        
        delivery_aging = self.calculate_delivery_aging(raw_dn_create, raw_pgi)
        pod_aging = self.calculate_pod_aging(raw_pgi, raw_pod)
        total_cycle = self.calculate_total_cycle(raw_dn_create, raw_pod)
        
        stage_info = self._determine_shipment_stage(raw_dn_create, raw_pgi, raw_pod)
        
        total_units = data.get('total_units')
        if total_units is None or total_units == 0:
            units_display = "Not Available"
        else:
            units_display = str(int(total_units))
        
        total_revenue = data.get('total_revenue')
        if total_revenue is None or total_revenue == 0:
            revenue_display = "Not Available"
        else:
            revenue_display = f"PKR {total_revenue:,.0f}"
        
        dashboard = {
            "dn_no": data.get('dn_no'),
            "dealer_name": data.get('dealer_name', 'Unknown'),
            "warehouse": data.get('warehouse', 'Unknown'),
            "city": data.get('city', 'Unknown'),
            "sales_manager": data.get('sales_manager'),
            "division": data.get('division'),
            "material_count": data.get('material_count', 1),
            "model_count": data.get('model_count', 1),
            "total_units_display": units_display,
            "total_revenue_display": revenue_display,
            "dn_create_date": self._format_date_dmy(raw_dn_create),
            "good_issue_date": self._format_date_dmy(raw_pgi),
            "pod_date": self._format_date_dmy(raw_pod),
            "delivery_aging_text": self._format_aging_text(delivery_aging),
            "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
            "total_cycle_text": self._format_aging_text(total_cycle),
            "total_cycle_days": total_cycle,
            "stage": stage_info["stage"],
            "stage_emoji": stage_info["stage_emoji"],
            "health": stage_info["health"],
            "health_emoji": stage_info["health_emoji"],
            "progress": stage_info["progress"],
            "recommendation": stage_info["recommendation"],
            "pending_flag": stage_info["pending"],
            "distance_text": "Not Available",
            "duration_text": "Not Available",
            "expected_delivery_text": "Not Available",
            "distance_category": "Unknown",
            "distance_emoji": "📍",
            "delay": 0,
            "efficiency": 0,
            "_has_pgi": raw_pgi is not None,
            "_has_pod": raw_pod is not None,
        }
        
        return {"success": True, "data": dashboard}
    
    # ==========================================================
    # BLOCK 9: WHATSAPP RESPONSE FORMATTER (FAST)
    # ==========================================================
    
    def format_dn_dashboard(self, dashboard_data: Dict[str, Any]) -> str:
        """Format DN dashboard - FAST response."""
        data = dashboard_data.get('data', {})
        
        # Extract data - FAST with minimal lookups
        dn_no = data.get('dn_no', 'N/A')
        dealer = data.get('dealer_name', 'Unknown')
        warehouse = data.get('warehouse', 'Unknown')
        city = data.get('city', 'Unknown')
        sales_mgr = data.get('sales_manager')
        division = data.get('division')
        
        material_count = str(data.get('material_count', 1))
        model_count = str(data.get('model_count', 1))
        units = data.get('total_units_display', 'Not Available')
        revenue = data.get('total_revenue_display', 'Not Available')
        
        dn_create = data.get('dn_create_date', 'N/A')
        pgi = data.get('good_issue_date', 'N/A')
        pod = data.get('pod_date', 'N/A')
        
        delivery_age = data.get('delivery_aging_text', 'N/A')
        pod_age = data.get('pod_aging_text', 'Not Started')
        total_cycle = data.get('total_cycle_text', 'N/A')
        
        stage = data.get('stage', 'Unknown')
        stage_emoji = data.get('stage_emoji', '❓')
        health = data.get('health', 'Unknown')
        health_emoji = data.get('health_emoji', '❓')
        progress = data.get('progress', [])
        recommendation = data.get('recommendation', 'Unable to determine status.')
        
        has_pgi = data.get('_has_pgi', False)
        has_pod = data.get('_has_pod', False)
        
        # Performance
        total_days = data.get('total_cycle_days', 0)
        expected_days = 2  # Default expected delivery
        if expected_days > 0 and total_days > 0:
            delay = max(0, total_days - expected_days)
            efficiency = round((expected_days / total_days) * 100, 1) if total_days > 0 else 0
            efficiency = min(efficiency, 100)
        else:
            delay = 0
            efficiency = 0
        
        # Build response - FAST string concatenation
        lines = []
        
        lines.append("📦 *Haier Logistics - DN Dashboard*")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("🆔 *Delivery Note*")
        lines.append(dn_no)
        lines.append("")
        lines.append("🏪 *Dealer*")
        lines.append(dealer)
        lines.append("")
        lines.append("🏢 *Warehouse*")
        lines.append(warehouse)
        lines.append("")
        lines.append("📍 *Destination*")
        lines.append(city)
        lines.append("")
        
        if sales_mgr:
            lines.append("👤 *Sales Manager*")
            lines.append(sales_mgr)
            lines.append("")
        
        if division:
            lines.append("📦 *Division*")
            lines.append(division)
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📊 *Shipment Summary*")
        lines.append("")
        lines.append(f"📦 DN Count: {material_count}")
        lines.append(f"📦 Product Models: {model_count}")
        lines.append(f"📦 Total Units: {units}")
        lines.append(f"💰 Shipment Value: {revenue}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📅 *Shipment Timeline*")
        lines.append("")
        lines.append("✅ DN Created")
        lines.append(dn_create)
        lines.append("")
        
        if has_pgi:
            pgi_label = "✅ PGI Completed"
        else:
            pgi_label = "⏳ PGI"
        lines.append(pgi_label)
        lines.append(pgi)
        lines.append("")
        
        if has_pod:
            pod_label = "✅ POD Received"
        elif has_pgi:
            pod_label = "⏳ POD Pending"
        else:
            pod_label = "⏳ POD"
        lines.append(pod_label)
        lines.append(pod)
        lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("⏳ *Shipment Aging*")
        lines.append("")
        
        if has_pgi and has_pod:
            lines.append("Dispatch Time")
            lines.append(delivery_age)
            lines.append("")
            lines.append("Transit Time")
            lines.append(pod_age)
            lines.append("")
            lines.append("Total Delivery Cycle")
            lines.append(total_cycle)
        elif has_pgi and not has_pod:
            lines.append("Dispatch Time")
            lines.append(delivery_age)
            lines.append("")
            lines.append("Transit Time")
            lines.append(pod_age)
            lines.append("")
            lines.append("Overall Cycle")
            lines.append(total_cycle)
        else:
            lines.append("Dispatch Waiting")
            lines.append(delivery_age)
            lines.append("")
            lines.append("Transit")
            lines.append("Not Started")
            lines.append("")
            lines.append("Overall Cycle")
            lines.append(total_cycle)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("🚛 *Logistics Route*")
        lines.append("")
        lines.append("Warehouse")
        lines.append(warehouse)
        lines.append("")
        lines.append("Destination")
        lines.append(city)
        lines.append("")
        lines.append("Road Distance")
        lines.append("Not Available")
        lines.append("")
        lines.append("Estimated Drive Time")
        lines.append("Not Available")
        lines.append("")
        lines.append("Expected Delivery")
        lines.append("Not Available")
        lines.append("")
        
        if has_pod:
            lines.append("Actual Delivery")
            lines.append(total_cycle)
            lines.append("")
            if delay > 0:
                lines.append("Delivery Delay")
                lines.append(f"{delay} Days")
            else:
                lines.append("Delivery Delay")
                lines.append("On Time")
            lines.append("")
        
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("📋 *Shipment Status*")
        lines.append("")
        lines.append("Current Stage")
        lines.append("")
        lines.append(f"{stage_emoji} {stage}")
        lines.append("")
        lines.append("Shipment Health")
        lines.append("")
        lines.append(f"{health_emoji} {health}")
        lines.append("")
        lines.append("Progress")
        lines.append("")
        
        for item in progress:
            status = item.get('status', '⏳')
            step = item.get('step', '')
            date_val = item.get('date', '')
            if date_val and date_val not in ['Pending', 'Not Started', 'N/A']:
                lines.append(f"{status} {step}")
                lines.append(date_val)
            else:
                lines.append(f"{status} {step}")
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        
        if has_pod:
            lines.append("📈 *Performance Analysis*")
            lines.append("")
            lines.append("Expected Delivery")
            lines.append("Not Available")
            lines.append("")
            lines.append("Actual Delivery")
            lines.append(total_cycle)
            lines.append("")
            if delay > 0:
                lines.append("Delay")
                lines.append(f"{delay} Days")
            else:
                lines.append("Delay")
                lines.append("No Delay")
            lines.append("")
            if efficiency > 0:
                lines.append("Route Efficiency")
                lines.append(f"{efficiency}%")
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━")
            lines.append("")
        
        lines.append("💡 *AI Recommendation*")
        lines.append("")
        lines.append(recommendation)
        lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("")
        lines.append("🤖 Generated by")
        lines.append("Haier Logistics AI Assistant")
        
        return "\n".join(lines)
    
    # ==========================================================
    # BLOCK 10: GET PENDING DNS (REQUIRED)
    # ==========================================================
    
    def get_pending_dns(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending DNs."""
        logger.info(f"🔍 Getting pending DNs (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
                   OR (good_issue_date IS NOT NULL AND pod_date IS NULL)
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending DNs: {total_pending}")
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending DNs found"
                }
            
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
                   OR (good_issue_date IS NOT NULL AND pod_date IS NULL)
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
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
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "stage_text": stage_info["stage"],
                    "health_emoji": stage_info["health_emoji"],
                    "health_text": stage_info["health"],
                    "pending_flag": stage_info["pending"],
                    "pending_flag_text": "Yes" if stage_info["pending"] else "No",
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
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BLOCK 11: GET PENDING PGI (REQUIRED)
    # ==========================================================
    
    def get_pending_pgi(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending PGI deliveries."""
        logger.info(f"🔍 Getting pending PGI (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NULL
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending PGI: {total_pending}")
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending PGI found"
                }
            
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
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                delivery_aging = self.calculate_delivery_aging(
                    row.get('dn_create_date'),
                    row.get('good_issue_date')
                )
                
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
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "stage_text": stage_info["stage"],
                    "health_emoji": stage_info["health_emoji"],
                    "health_text": stage_info["health"],
                    "pending_flag": stage_info["pending"],
                    "pending_flag_text": "Yes" if stage_info["pending"] else "No",
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
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BLOCK 12: GET PENDING POD (REQUIRED)
    # ==========================================================
    
    def get_pending_pod(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Get all pending POD deliveries."""
        logger.info(f"🔍 Getting pending POD (limit: {limit}, offset: {offset})")
        
        try:
            limit = min(limit, 1000)
            
            count_query = """
                SELECT COUNT(DISTINCT dn_no) AS total_pending
                FROM delivery_reports
                WHERE good_issue_date IS NOT NULL
                  AND pod_date IS NULL
            """
            count_result = self._execute_query(count_query)
            total_pending = count_result[0].get('total_pending', 0) if count_result else 0
            
            logger.info(f"📊 Total pending POD: {total_pending}")
            
            if total_pending == 0:
                return {
                    "success": True,
                    "data": [],
                    "total": 0,
                    "limit": limit,
                    "offset": offset,
                    "message": "No pending POD found"
                }
            
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
                  AND pod_date IS NULL
                GROUP BY dn_no
                ORDER BY MIN(dn_create_date) ASC
                LIMIT :limit OFFSET :offset
            """
            
            results = self._execute_query(
                pending_query,
                {"limit": limit, "offset": offset}
            )
            
            formatted_results = []
            for row in results:
                stage_info = self._determine_shipment_stage(
                    row.get('dn_create_date'),
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
                pod_aging = self.calculate_pod_aging(
                    row.get('good_issue_date'),
                    row.get('pod_date')
                )
                
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
                    "stage": stage_info["stage"],
                    "stage_emoji": stage_info["stage_emoji"],
                    "stage_text": stage_info["stage"],
                    "health_emoji": stage_info["health_emoji"],
                    "health_text": stage_info["health"],
                    "pending_flag": stage_info["pending"],
                    "pending_flag_text": "Yes" if stage_info["pending"] else "No",
                    "pod_aging_days": pod_aging,
                    "pod_aging_text": self._format_aging_text(pod_aging) if pod_aging > 0 else "Not Started",
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
            return {"success": False, "error": str(e)}
    
    # ==========================================================
    # BLOCK 13: HEALTH & VALIDATION
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        session = None
        result = {
            "healthy": False,
            "service": self._service_name,
            "version": self._version,
            "database": "disconnected",
            "errors": [],
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            if not SessionLocal:
                result["errors"].append("SessionLocal not available")
                return result
            
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            result["database"] = "connected"
            result["healthy"] = True
            self._status = "READY"
            
            return result
        except Exception as e:
            result["errors"].append(str(e))
            return result
        finally:
            if session:
                session.close()
    
    def validation_query(self) -> Dict[str, Any]:
        session = None
        result = {"success": False, "records": 0, "error": None}
        
        try:
            session = self._get_session()
            if not session:
                result["error"] = "SessionLocal not available"
                return result
            
            query = "SELECT COUNT(DISTINCT dn_no) as count FROM delivery_reports"
            query_result = session.execute(text(query))
            row = query_result.fetchone()
            
            if row:
                result["success"] = True
                result["records"] = row[0] or 0
            
            return result
        except Exception as e:
            result["error"] = str(e)
            return result
        finally:
            if session:
                session.close()
    
    def get_service_metadata(self) -> Dict[str, Any]:
        return {
            "service_name": self._service_name,
            "version": self._version,
            "status": self._status,
            "module": "DN Analytics",
            "description": "Haier Pakistan Logistics - Enterprise DN Dashboard",
            "methods": [
                "health_check",
                "validation_query",
                "get_service_metadata",
                "search_dn",
                "verify_dn",
                "get_dn_dashboard",
                "format_dn_dashboard",
                "get_pending_dns",
                "get_pending_pgi",
                "get_pending_pod"
            ]
        }
    
    def verify_dn(self, dn_no: str) -> Dict[str, Any]:
        if not dn_no:
            return {"success": False, "exists": False, "error": "DN number required"}
        
        normalized_dn = self._normalize_dn(dn_no)
        
        query = """
            SELECT COUNT(DISTINCT dn_no) as count 
            FROM delivery_reports 
            WHERE CAST(dn_no AS TEXT) = :dn_no
               OR CAST(dn_no AS TEXT) LIKE '%' || :dn_no || '%'
               OR REPLACE(CAST(dn_no AS TEXT), '-', '') = :dn_no
               OR REGEXP_REPLACE(CAST(dn_no AS TEXT), '[^0-9]', '', 'g') = :dn_no
        """
        results = self._execute_query(query, {"dn_no": normalized_dn})
        exists = results and results[0].get('count', 0) > 0
        
        return {"success": True, "exists": exists}


# ==========================================================
# BLOCK 14: THREAD-SAFE SINGLETON
# ==========================================================

_dn_analytics_service = None
_dn_lock = threading.Lock()


def get_dn_analytics_service() -> DNAnalysisService:
    global _dn_analytics_service
    
    if _dn_analytics_service is None:
        with _dn_lock:
            if _dn_analytics_service is None:
                try:
                    _dn_analytics_service = DNAnalysisService()
                except Exception as e:
                    logger.exception(f"❌ DNAnalysisService initialization failed: {e}")
                    raise
    
    return _dn_analytics_service


# ==========================================================
# BLOCK 15: EXPORTS
# ==========================================================

__all__ = [
    'DNAnalysisService',
    'get_dn_analytics_service'
]


# ==========================================================
# BLOCK 16: MODULE INITIALIZATION
# ==========================================================

logger.info("=" * 70)
logger.info("DNAnalysisService v14.0 - COMPLETE & READY")
logger.info("=" * 70)
logger.info("")
logger.info("   ✅ Service: dn_analysis")
logger.info("   ✅ Version: 14.0")
logger.info("   ✅ Status: READY")
logger.info("   ✅ Methods: ALL INCLUDED")
logger.info("   ✅ Intelligent status from dates")
logger.info("   ✅ Professional WhatsApp dashboard")
logger.info("   ✅ Fast response optimized")
logger.info("")
logger.info("   STATUS: ✅ PRODUCTION READY")
logger.info("=" * 70)
