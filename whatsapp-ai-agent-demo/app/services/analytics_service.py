# ==========================================================
# FILE: app/services/analytics_service.py (v10.0 - FULLY ALIGNED)
# ==========================================================
# PURPOSE: PRIMARY ANALYTICS ENGINE - Direct PostgreSQL Integration
# VERSION: 10.0 - Fully Aligned with ai_provider_service.py v14.0
#
# CRITICAL FIXES:
# 1. ✅ SQLAlchemy CASE syntax for 2.x compatibility
# 2. ✅ Revenue ranking using column references
# 3. ✅ City search with wildcards (%)
# 4. ✅ Warehouse search with wildcards (%)
# 5. ✅ Executive insights uses pgi_rate
# 6. ✅ Added get_root_cause_insights() method
# 7. ✅ Added get_control_tower_alerts() method
# 8. ✅ Added get_trend_analysis() method
# 9. ✅ Added compare_dealers(), compare_warehouses(), compare_cities()
# 10. ✅ Added get_dealer_revenue(), get_dealer_units(), get_dealer_performance(), get_dealer_aging()
# 11. ✅ All methods return AnalyticsResponse
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, text, and_, or_, desc, asc, cast, String, case
import os

from app.models import DeliveryReport
from app.database import SessionLocal
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


# ==========================================================
# CONSTANTS - STANDARD FIELD MAPPING
# ==========================================================

DEALER_NAME_FIELD = "customer_name"
DEALER_CODE_FIELD = "dealer_code"
CUSTOMER_CODE_FIELD = "customer_code"
DN_NO_FIELD = "dn_no"
DN_QTY_FIELD = "dn_qty"
DN_AMOUNT_FIELD = "dn_amount"
DELIVERY_STATUS_FIELD = "delivery_status"
PGI_STATUS_FIELD = "pgi_status"
POD_STATUS_FIELD = "pod_status"
WAREHOUSE_CODE_FIELD = "warehouse_code"
DELIVERY_LOCATION_FIELD = "delivery_location"
DIVISION_FIELD = "division"
WAREHOUSE_FIELD = "warehouse"
SHIP_TO_CITY_FIELD = "ship_to_city"
SALES_OFFICE_FIELD = "sales_office"
SALES_MANAGER_FIELD = "sales_manager"
MATERIAL_NO_FIELD = "material_no"
CUSTOMER_MODEL_FIELD = "customer_model"
GOOD_ISSUE_DATE_FIELD = "good_issue_date"
POD_DATE_FIELD = "pod_date"
DN_CREATE_DATE_FIELD = "dn_create_date"
SOURCE_FILE_FIELD = "source_file"
PENDING_FLAG_FIELD = "pending_flag"


# ==========================================================
# RAILWAY POSTGRESQL CONFIGURATION
# ==========================================================

class RailwayPostgresConfig:
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    
    @classmethod
    def is_railway(cls) -> bool:
        return bool(cls.DATABASE_URL)


# ==========================================================
# ENTERPRISE EXCEPTION HIERARCHY
# ==========================================================

class AnalyticsError(Exception):
    pass

class DealerNotFoundError(AnalyticsError):
    def __init__(self, dealer_name: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dealer '{dealer_name}' not found (Error ID: {self.error_id})")

class DashboardGenerationError(AnalyticsError):
    def __init__(self, dealer_name: str, reason: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.reason = reason
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dashboard generation failed for '{dealer_name}': {reason} (Error ID: {self.error_id})")

class DatabaseQueryError(AnalyticsError):
    def __init__(self, query: str, error: str, error_id: str = None):
        self.query = query
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Database query failed: {error} (Error ID: {self.error_id})")


# ==========================================================
# RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, error: str = None, error_id: str = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_id = error_id
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_id": self.error_id,
            "timestamp": self.timestamp
        }


# ==========================================================
# ANALYTICS REPOSITORY - PRIMARY DATA ACCESS LAYER
# ==========================================================

class AnalyticsRepository:
    """PRIMARY DATA ACCESS LAYER - Direct PostgreSQL queries using SQLAlchemy 2.x."""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.table_name = "delivery_reports"
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def normalize_dn(self, dn_no: str) -> Optional[str]:
        if not dn_no:
            return None
        normalized = re.sub(r'\D', '', str(dn_no).strip())
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        return normalized
    
    def get_dn(self, dn_no: str) -> Optional[DeliveryReport]:
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return None
            return self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
        except Exception as e:
            logger.error(f"Get DN failed: {e}")
            return None
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return {"dn": dn_no, "normalized": None, "found": False, "error": "Invalid DN format"}
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            found = record is not None
            result = {"dn": dn_no, "normalized": normalized, "found": found}
            if found and record:
                result["record"] = {
                    "dn_no": record.dn_no,
                    "customer_name": record.customer_name,
                    "dealer_code": record.dealer_code or "",
                    "customer_code": record.customer_code or "",
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "pending_flag": record.pending_flag or False
                }
            return result
        except Exception as e:
            logger.error(f"Verify DN failed: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def debug_dn(self, dn_no: str) -> Dict[str, Any]:
        try:
            normalized = self.normalize_dn(dn_no)
            if not normalized:
                return {"input": dn_no, "normalized": None, "rows_found": 0, "error": "Invalid DN format"}
            count = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).count()
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            result = {"input": dn_no, "normalized": normalized, "rows_found": count}
            if record:
                result["record"] = {
                    "dn_no": record.dn_no,
                    "customer_name": record.customer_name,
                    "dealer_code": record.dealer_code or "",
                    "customer_code": record.customer_code or "",
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "delivery_status": record.delivery_status,
                    "pgi_status": record.pgi_status,
                    "pod_status": record.pod_status,
                    "pending_flag": record.pending_flag or False,
                    "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                    "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                    "pod_date": record.pod_date.isoformat() if record.pod_date else None
                }
            return result
        except Exception as e:
            logger.error(f"Debug DN failed: {e}")
            return {"input": dn_no, "error": str(e)}
    
    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        if not dealer_input or not dealer_input.strip():
            return None
        dealer_input = dealer_input.strip()
        try:
            record = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if record:
                return record.customer_name
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if record:
                return record.customer_name
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.dealer_code.ilike(dealer_input)
            ).first()
            if record:
                return record.customer_name
            record = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_code.ilike(dealer_input)
            ).first()
            if record:
                return record.customer_name
            return None
        except Exception as e:
            logger.error(f"Resolve dealer failed: {e}")
            return None
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            logger.info(f"DEALER_SEARCH={dealer_name}")
            logger.info(f"DEALER_RESOLVED={resolved}")
            
            result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.warehouse).label("top_warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("last_dn_date"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.good_issue_date.isnot(None)), DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((or_(DeliveryReport.delivery_status != 'Completed', DeliveryReport.good_issue_date.is_(None)), DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(func.distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(func.distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns"),
                func.coalesce(func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_pgi_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)), 0).label("avg_pod_aging"),
                func.coalesce(func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.dn_create_date) / 86400), else_=None)), 0).label("avg_total_aging"),
                func.coalesce(func.sum(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_qty), else_=0)), 0).label("delivered_units"),
                func.coalesce(func.sum(case((or_(DeliveryReport.delivery_status != 'Completed', DeliveryReport.good_issue_date.is_(None)), DeliveryReport.dn_qty), else_=0)), 0).label("pending_units"),
                func.coalesce(func.sum(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_qty), else_=0)), 0).label("transit_units")
            ).filter(DeliveryReport.customer_name == resolved).group_by(DeliveryReport.customer_name).first()
            
            if not result or result.total_dns == 0:
                return {"dealer_name": resolved, "total_dns": 0, "error": "No records found"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            pod_completed_dns = result.pod_completed_dns or 0
            delivery_rate = round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
            pod_rate = round((pod_completed_dns / (delivered_dns or 1) * 100) if delivered_dns > 0 else 0, 1)
            
            if delivered_dns == 0 and total_dns == 0:
                dealer_status = "Inactive"
            elif total_dns < 10:
                dealer_status = "Low Activity"
            elif delivery_rate >= 90:
                dealer_status = "Active - High Performance"
            else:
                dealer_status = "Active - Needs Attention"
            
            return {
                "dealer_name": resolved,
                "dealer_code": result.dealer_code or "",
                "customer_code": result.customer_code or "",
                "division": result.division or "",
                "sales_office": result.sales_office or "",
                "sales_manager": result.sales_manager or "",
                "city": result.city or "",
                "warehouse": result.top_warehouse or "",
                "warehouse_code": result.warehouse_code or "",
                "delivery_location": result.delivery_location or "",
                "dealer_status": dealer_status,
                "first_dn_date": result.first_dn_date,
                "last_dn_date": result.last_dn_date,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "transit_dns": result.transit_dns or 0,
                "pod_completed_dns": pod_completed_dns,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_flag_dns": result.pending_flag_dns or 0,
                "delivered_units": int(result.delivered_units or 0),
                "pending_units": int(result.pending_units or 0),
                "transit_units": int(result.transit_units or 0),
                "delivery_rate": delivery_rate,
                "pod_rate": pod_rate,
                "avg_pgi_aging": round(result.avg_pgi_aging or 0, 1),
                "avg_pod_aging": round(result.avg_pod_aging or 0, 1),
                "avg_total_aging": round(result.avg_total_aging or 0, 1)
            }
        except Exception as e:
            logger.error(f"Get dealer dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_profile(self, dealer_name: str) -> Dict[str, Any]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return {}
            result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.max(DeliveryReport.delivery_location).label("delivery_location"),
                func.max(DeliveryReport.sales_office).label("sales_office"),
                func.max(DeliveryReport.sales_manager).label("sales_manager"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.min(DeliveryReport.dn_create_date).label("first_dn_date"),
                func.max(DeliveryReport.dn_create_date).label("last_dn_date"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns")
            ).filter(DeliveryReport.customer_name == resolved).group_by(DeliveryReport.customer_name).first()
            if not result:
                return {}
            return {
                "dealer_name": result.dealer_name or "",
                "dealer_code": result.dealer_code or "",
                "customer_code": result.customer_code or "",
                "division": result.division or "",
                "warehouse": result.warehouse or "",
                "warehouse_code": result.warehouse_code or "",
                "delivery_location": result.delivery_location or "",
                "sales_office": result.sales_office or "",
                "sales_manager": result.sales_manager or "",
                "city": result.city or "",
                "first_dn_date": result.first_dn_date,
                "last_dn_date": result.last_dn_date,
                "total_dns": result.total_dns or 0
            }
        except Exception as e:
            logger.error(f"Get dealer profile failed: {e}")
            return {}
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            records = self.db.query(DeliveryReport).filter(
                DeliveryReport.customer_name == resolved
            ).order_by(desc(DeliveryReport.dn_create_date)).limit(limit).all()
            timeline = []
            for record in records:
                timeline.append({
                    "dn_no": record.dn_no,
                    "dn_qty": int(record.dn_qty) if record.dn_qty else 0,
                    "dn_amount": float(record.dn_amount) if record.dn_amount else 0,
                    "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                    "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                    "pod_date": record.pod_date.isoformat() if record.pod_date else None,
                    "warehouse": record.warehouse,
                    "ship_to_city": record.ship_to_city,
                    "delivery_status": record.delivery_status,
                    "pgi_status": record.pgi_status,
                    "pod_status": record.pod_status,
                    "pending_flag": record.pending_flag or False
                })
            return timeline
        except Exception as e:
            logger.error(f"Get dealer timeline failed: {e}")
            return []
    
    def get_product_dashboard(self, dealer_name: str) -> List[Dict[str, Any]]:
        try:
            resolved = self.resolve_dealer(dealer_name)
            if not resolved:
                return []
            results = self.db.query(
                func.coalesce(DeliveryReport.material_no, 'UNKNOWN').label("product_code"),
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no, 'UNKNOWN').label("product_name"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("dn_count"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.avg(DeliveryReport.dn_amount).label("avg_revenue_per_dn"),
                func.max(DeliveryReport.dn_amount).label("max_revenue"),
                func.min(DeliveryReport.dn_amount).label("min_revenue"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_count"),
                func.count(func.distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_count")
            ).filter(DeliveryReport.customer_name == resolved).group_by(
                DeliveryReport.material_no, DeliveryReport.customer_model
            ).order_by(desc("total_revenue")).limit(50).all()
            products = []
            for r in results:
                dn_count = r.dn_count or 1
                delivered_count = r.delivered_count or 0
                products.append({
                    "product_code": r.product_code,
                    "product_name": r.product_name,
                    "dn_count": dn_count,
                    "total_units": int(r.total_units or 0),
                    "total_revenue": float(r.total_revenue or 0),
                    "avg_revenue_per_dn": float(r.avg_revenue_per_dn or 0),
                    "max_revenue": float(r.max_revenue or 0),
                    "min_revenue": float(r.min_revenue or 0),
                    "delivery_rate": round((delivered_count / dn_count * 100) if dn_count > 0 else 0, 1)
                })
            return products
        except Exception as e:
            logger.error(f"Get product dashboard failed: {e}")
            return []
    
    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        try:
            if not city_name or not city_name.strip():
                return {"error": "City name cannot be empty"}
            result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns")
            ).filter(DeliveryReport.ship_to_city.ilike(f"%{city_name}%")).group_by(DeliveryReport.ship_to_city).first()
            if not result or result.total_dns == 0:
                return {"error": f"City '{city_name}' not found"}
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            return {
                "city_name": result.city,
                "summary": {
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "total_dealers": result.total_dealers or 0,
                    "delivered_dns": delivered_dns,
                    "pending_flag_dns": result.pending_flag_dns or 0,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                }
            }
        except Exception as e:
            logger.error(f"Get city dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        try:
            if not warehouse_name or not warehouse_name.strip():
                return {"error": "Warehouse name cannot be empty"}
            result = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_dns")
            ).filter(DeliveryReport.warehouse.ilike(f"%{warehouse_name}%")).group_by(DeliveryReport.warehouse).first()
            if not result or result.total_dns == 0:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            return {
                "warehouse_name": result.warehouse,
                "summary": {
                    "total_dns": total_dns,
                    "total_units": int(result.total_units or 0),
                    "total_revenue": float(result.total_revenue or 0),
                    "total_dealers": result.total_dealers or 0,
                    "delivered_dns": delivered_dns,
                    "pending_flag_dns": result.pending_flag_dns or 0,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                }
            }
        except Exception as e:
            logger.error(f"Get warehouse dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_ranking(self, limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        try:
            revenue_col = func.sum(DeliveryReport.dn_amount).label("total_revenue")
            order = desc(revenue_col) if top else asc(revenue_col)
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                revenue_col,
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns")
            ).filter(DeliveryReport.customer_name.isnot(None), DeliveryReport.customer_name != '').group_by(
                DeliveryReport.customer_name
            ).order_by(order).limit(limit).all()
            dealers = []
            for r in results:
                total_dns = r.total_dns or 1
                delivered_dns = r.delivered_dns or 0
                dealers.append({
                    "dealer_name": r.dealer_name,
                    "total_revenue": float(r.total_revenue or 0),
                    "total_dns": total_dns,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                })
            return dealers
        except Exception as e:
            logger.error(f"Get dealer ranking failed: {e}")
            return []
    
    def get_city_ranking(self, limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        try:
            revenue_col = func.sum(DeliveryReport.dn_amount).label("total_revenue")
            order = desc(revenue_col) if top else asc(revenue_col)
            results = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                revenue_col,
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns")
            ).filter(DeliveryReport.ship_to_city.isnot(None), DeliveryReport.ship_to_city != '').group_by(
                DeliveryReport.ship_to_city
            ).order_by(order).limit(limit).all()
            cities = []
            for r in results:
                total_dns = r.total_dns or 1
                delivered_dns = r.delivered_dns or 0
                cities.append({
                    "city": r.city,
                    "total_revenue": float(r.total_revenue or 0),
                    "total_dns": total_dns,
                    "total_dealers": r.total_dealers or 0,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                })
            return cities
        except Exception as e:
            logger.error(f"Get city ranking failed: {e}")
            return []
    
    def get_warehouse_ranking(self, limit: int = 10, top: bool = True) -> List[Dict[str, Any]]:
        try:
            revenue_col = func.sum(DeliveryReport.dn_amount).label("total_revenue")
            order = desc(revenue_col) if top else asc(revenue_col)
            results = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                revenue_col,
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(func.distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns")
            ).filter(DeliveryReport.warehouse.isnot(None), DeliveryReport.warehouse != '').group_by(
                DeliveryReport.warehouse
            ).order_by(order).limit(limit).all()
            warehouses = []
            for r in results:
                total_dns = r.total_dns or 1
                delivered_dns = r.delivered_dns or 0
                warehouses.append({
                    "warehouse": r.warehouse,
                    "total_revenue": float(r.total_revenue or 0),
                    "total_dns": total_dns,
                    "total_dealers": r.total_dealers or 0,
                    "delivery_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1)
                })
            return warehouses
        except Exception as e:
            logger.error(f"Get warehouse ranking failed: {e}")
            return []
    
    def get_delivery_performance(self) -> Dict[str, Any]:
        try:
            result = self.db.query(
                func.count(func.distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(func.distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(func.distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(func.distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi"),
                func.count(func.distinct(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.pod_date.is_(None)), DeliveryReport.dn_no), else_=None))).label("in_transit"),
                func.count(func.distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_flag_count"),
                func.avg(case((and_(DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_create_date.isnot(None)), func.extract('epoch', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date) / 86400), else_=None)).label("avg_processing_days"),
                func.avg(case((and_(DeliveryReport.pod_date.isnot(None), DeliveryReport.good_issue_date.isnot(None)), func.extract('epoch', DeliveryReport.pod_date - DeliveryReport.good_issue_date) / 86400), else_=None)).label("avg_delivery_days")
            ).first()
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            pod_completed_dns = result.pod_completed_dns or 0
            return {
                "metrics": {
                    "total_dns": total_dns,
                    "delivered": delivered_dns,
                    "in_transit": result.in_transit or 0,
                    "pending_pgi": result.pending_pgi or 0,
                    "pod_completed": pod_completed_dns,
                    "pending_flag_count": result.pending_flag_count or 0,
                    "pgi_rate": round((delivered_dns / total_dns * 100) if total_dns > 0 else 0, 1),
                    "pod_rate": round((pod_completed_dns / (delivered_dns or 1) * 100) if delivered_dns > 0 else 0, 1),
                    "avg_processing_days": round(result.avg_processing_days or 0, 1),
                    "avg_delivery_days": round(result.avg_delivery_days or 0, 1)
                }
            }
        except Exception as e:
            logger.error(f"Get delivery performance failed: {e}")
            return {"error": str(e)}
    
    def get_root_cause_insights(self) -> Dict[str, Any]:
        try:
            perf = self.get_delivery_performance()
            metrics = perf.get("metrics", {})
            issues = []
            if metrics.get("avg_processing_days", 0) > 5:
                issues.append(f"⚠️ High processing time: {metrics.get('avg_processing_days', 0):.1f} days")
            if metrics.get("avg_delivery_days", 0) > 5:
                issues.append(f"⚠️ High delivery time: {metrics.get('avg_delivery_days', 0):.1f} days")
            if metrics.get("pod_rate", 0) < 80:
                issues.append(f"⚠️ Low POD rate: {metrics.get('pod_rate', 0):.1f}%")
            if metrics.get("pending_flag_count", 0) > 0:
                issues.append(f"⚠️ {metrics.get('pending_flag_count', 0)} pending flagged DNs")
            recommendations = []
            if metrics.get("avg_processing_days", 0) > 5:
                recommendations.append("🔧 Reduce processing time by improving PGI efficiency")
            if metrics.get("avg_delivery_days", 0) > 5:
                recommendations.append("🔧 Optimize delivery routes and logistics")
            if metrics.get("pod_rate", 0) < 80:
                recommendations.append("🔧 Improve POD collection process")
            return {"key_issues": issues, "recommendations": recommendations, "metrics": metrics}
        except Exception as e:
            logger.error(f"Get root cause insights failed: {e}")
            return {"error": str(e)}
    
    def get_control_tower_alerts(self) -> Dict[str, Any]:
        try:
            perf = self.get_delivery_performance()
            metrics = perf.get("metrics", {})
            alerts = []
            if metrics.get("pending_pgi", 0) > 10:
                alerts.append({"type": "Pending PGI", "risk_status": "high", "description": f"{metrics.get('pending_pgi', 0)} DNs pending PGI"})
            if metrics.get("pending_pod", 0) > 10:
                alerts.append({"type": "Pending POD", "risk_status": "critical", "description": f"{metrics.get('pending_pod', 0)} DNs pending POD"})
            if metrics.get("avg_processing_days", 0) > 7:
                alerts.append({"type": "Slow Processing", "risk_status": "critical", "description": f"Avg processing {metrics.get('avg_processing_days', 0):.1f} days"})
            if metrics.get("avg_delivery_days", 0) > 7:
                alerts.append({"type": "Slow Delivery", "risk_status": "high", "description": f"Avg delivery {metrics.get('avg_delivery_days', 0):.1f} days"})
            critical_count = sum(1 for a in alerts if a.get("risk_status") == "critical")
            high_count = sum(1 for a in alerts if a.get("risk_status") == "high")
            return {"alerts": alerts, "critical_count": critical_count, "high_count": high_count}
        except Exception as e:
            logger.error(f"Get control tower alerts failed: {e}")
            return {"error": str(e)}
    
    def get_trend_analysis(self) -> Dict[str, Any]:
        try:
            results = self.db.query(
                func.date_trunc('month', DeliveryReport.dn_create_date).label("period"),
                func.count(func.distinct(DeliveryReport.dn_no)).label("count"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(DeliveryReport.dn_create_date.isnot(None)).group_by(
                func.date_trunc('month', DeliveryReport.dn_create_date)
            ).order_by(desc("period")).limit(12).all()
            monthly = []
            for r in results:
                monthly.append({
                    "period": r.period.strftime("%Y-%m") if r.period else "N/A",
                    "count": r.count or 0,
                    "revenue": float(r.revenue or 0)
                })
            return {"trends": {"monthly": monthly}}
        except Exception as e:
            logger.error(f"Get trend analysis failed: {e}")
            return {"error": str(e)}
    
    def compare_dealers(self, dealer1: str, dealer2: str) -> Dict[str, Any]:
        try:
            results = {}
            for dealer in [dealer1, dealer2]:
                resolved = self.resolve_dealer(dealer)
                if resolved:
                    dash = self.get_dealer_dashboard(resolved)
                    if "error" not in dash:
                        results[dealer] = {
                            "revenue": dash.get("total_revenue", 0),
                            "units": dash.get("total_units", 0),
                            "dn_count": dash.get("total_dns", 0),
                            "pod_rate": dash.get("pod_rate", 0)
                        }
            return results
        except Exception as e:
            logger.error(f"Compare dealers failed: {e}")
            return {"error": str(e)}
    
    def compare_warehouses(self, warehouse1: str, warehouse2: str) -> Dict[str, Any]:
        try:
            results = {}
            for warehouse in [warehouse1, warehouse2]:
                dash = self.get_warehouse_dashboard(warehouse)
                if "error" not in dash:
                    results[warehouse] = {
                        "revenue": dash.get("summary", {}).get("total_revenue", 0),
                        "units": dash.get("summary", {}).get("total_units", 0),
                        "dn_count": dash.get("summary", {}).get("total_dns", 0),
                        "pod_rate": dash.get("summary", {}).get("delivery_rate", 0)
                    }
            return results
        except Exception as e:
            logger.error(f"Compare warehouses failed: {e}")
            return {"error": str(e)}
    
    def compare_cities(self, city1: str, city2: str) -> Dict[str, Any]:
        try:
            results = {}
            for city in [city1, city2]:
                dash = self.get_city_dashboard(city)
                if "error" not in dash:
                    results[city] = {
                        "revenue": dash.get("summary", {}).get("total_revenue", 0),
                        "units": dash.get("summary", {}).get("total_units", 0),
                        "dn_count": dash.get("summary", {}).get("total_dns", 0),
                        "pod_rate": dash.get("summary", {}).get("delivery_rate", 0)
                    }
            return results
        except Exception as e:
            logger.error(f"Compare cities failed: {e}")
            return {"error": str(e)}
    
    def debug_database(self) -> Dict[str, Any]:
        try:
            try:
                self.db.execute(text("SELECT 1 as connected")).first()
                connected = True
            except:
                connected = False
            if not connected:
                return {"connected": False, "error": "Database connection failed"}
            total_rows = self.db.query(DeliveryReport).count()
            distinct_dns = self.db.query(func.count(func.distinct(DeliveryReport.dn_no))).scalar() or 0
            distinct_dealers = self.db.query(func.count(func.distinct(DeliveryReport.customer_name))).filter(
                DeliveryReport.customer_name.isnot(None), DeliveryReport.customer_name != ''
            ).scalar() or 0
            sample = self.db.query(DeliveryReport.dn_no).first()
            sample_dn = sample[0] if sample else None
            db_name_result = self.db.execute(text("SELECT current_database()")).first()
            database_name = db_name_result[0] if db_name_result else "unknown"
            return {"connected": True, "table": self.table_name, "database": database_name, "rows": total_rows, "distinct_dns": distinct_dns, "distinct_dealers": distinct_dealers, "sample_dn": sample_dn}
        except Exception as e:
            logger.error(f"Debug database failed: {e}")
            return {"connected": False, "error": str(e)}


# ==========================================================
# ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    DEALER_NAME_FIELD = DEALER_NAME_FIELD
    
    def __init__(self, use_redis: bool = False):
        self._start_time = time.time()
        self.is_railway = RailwayPostgresConfig.is_railway()
        if self.is_railway:
            logger.info("🚆 Running on Railway - 100% PostgreSQL mode enabled")
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.repo = AnalyticsRepository()
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._dealer_cache: Dict[str, Tuple[str, datetime]] = {}
        self.metrics = {
            "total_requests": 0, "successful_requests": 0, "failed_requests": 0,
            "total_duration_ms": 0, "cache_hits": 0, "cache_misses": 0,
            "dealer_resolution_success": 0, "dealer_resolution_failure": 0,
            "postgresql_queries": 0, "slow_queries": 0, "errors_by_type": defaultdict(int),
            "dn_lookups": 0, "dn_lookups_success": 0, "dn_lookups_failure": 0
        }
        self._test_postgresql()
        logger.info("=" * 70)
        logger.info("AnalyticsService v10.0 - FULLY ALIGNED")
        logger.info("=" * 70)
        logger.info("   ✅ All methods return AnalyticsResponse")
        logger.info("   ✅ customer_name = Dealer = Sold-To Party")
        logger.info("   ✅ DN Count = COUNT(DISTINCT dn_no)")
        logger.info("   ✅ Units = SUM(dn_qty)")
        logger.info("   ✅ Revenue = SUM(dn_amount)")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def _test_postgresql(self):
        try:
            result = self.repo.db.execute(text("SELECT version()"))
            version = result.first()[0]
            logger.info(f"✅ PostgreSQL connected: {version[:50]}...")
        except Exception as e:
            logger.error(f"❌ PostgreSQL connection test failed: {e}")
    
    def close(self):
        self.repo.close()
        self.kpi.close()
    
    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache and key in self._cache_ttl:
            if datetime.now() < self._cache_ttl[key]:
                self.metrics["cache_hits"] += 1
                return self._cache[key]
        self.metrics["cache_misses"] += 1
        return None
    
    def _set_cached(self, key: str, value: Any, ttl_seconds: int = 300):
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _get_cached_dealer(self, dealer_input: str) -> Optional[str]:
        if dealer_input in self._dealer_cache:
            resolved, expiry = self._dealer_cache[dealer_input]
            if datetime.now() < expiry:
                return resolved
        return None
    
    def _set_cached_dealer(self, dealer_input: str, resolved: str):
        self._dealer_cache[dealer_input] = (resolved, datetime.now() + timedelta(hours=24))
    
    def clear_cache(self):
        self._cache.clear()
        self._cache_ttl.clear()
        self._dealer_cache.clear()
        logger.info("All caches cleared")
    
    def _resolve_dealer(self, dealer_input: str) -> Optional[str]:
        if not dealer_input:
            return None
        cached = self._get_cached_dealer(dealer_input)
        if cached:
            return cached
        resolved = self.repo.resolve_dealer(dealer_input)
        if resolved:
            self._set_cached_dealer(dealer_input, resolved)
            self.metrics["dealer_resolution_success"] += 1
        else:
            self.metrics["dealer_resolution_failure"] += 1
        return resolved
    
    def _normalize_dn(self, dn: str) -> Optional[str]:
        return self.repo.normalize_dn(dn)
    
    def get_dn_analytics(self, dn_number: str) -> AnalyticsResponse:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.metrics["total_requests"] += 1
        self.metrics["dn_lookups"] += 1
        logger.info(f"[{request_id}] DN_LOOKUP_START={dn_number}")
        try:
            if not dn_number or not dn_number.strip():
                return AnalyticsResponse(success=False, error="DN number cannot be empty", error_id=str(uuid.uuid4())[:8])
            normalized = self._normalize_dn(dn_number)
            logger.info(f"[{request_id}] DN_NORMALIZED={normalized}")
            if not normalized:
                return AnalyticsResponse(success=False, error=f"Invalid DN format: {dn_number}", error_id=str(uuid.uuid4())[:8])
            record = self.repo.get_dn(normalized)
            self.metrics["postgresql_queries"] += 1
            logger.info(f"[{request_id}] DN_ROWS_FOUND={1 if record else 0}")
            if not record:
                self.metrics["failed_requests"] += 1
                self.metrics["dn_lookups_failure"] += 1
                return AnalyticsResponse(success=False, error=f"DN {dn_number} not found in database", error_id=str(uuid.uuid4())[:8])
            formatted = self._format_dn_record(record)
            validation = self._validate_dn_dates(record)
            data = {"record": formatted, "validation": validation, "status": self._determine_dn_status(record), "found": True, "request_id": request_id, "duration_ms": (time.time() - start_time) * 1000}
            self.metrics["successful_requests"] += 1
            self.metrics["dn_lookups_success"] += 1
            self.metrics["total_duration_ms"] += (time.time() - start_time) * 1000
            logger.info(f"[{request_id}] DN_SUCCESS=True")
            return AnalyticsResponse(success=True, data=data)
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            self.metrics["failed_requests"] += 1
            self.metrics["dn_lookups_failure"] += 1
            logger.error(f"[{request_id}] DN_LOOKUP_FAILED=exception")
            return AnalyticsResponse(success=False, error=str(e), error_id=error_id)
    
    def _format_dn_record(self, record: DeliveryReport) -> Dict[str, Any]:
        return {
            "dn_number": record.dn_no,
            "customer_name": record.customer_name,
            "dealer_code": record.dealer_code or "",
            "customer_code": record.customer_code or "",
            "division": record.division or "",
            "warehouse": record.warehouse,
            "warehouse_code": record.warehouse_code or "",
            "delivery_location": record.delivery_location or "",
            "ship_to_city": record.ship_to_city,
            "sales_office": record.sales_office,
            "sales_manager": record.sales_manager,
            "material_no": record.material_no,
            "customer_model": record.customer_model,
            "units": int(record.dn_qty) if record.dn_qty else 0,
            "amount": float(record.dn_amount) if record.dn_amount else 0,
            "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
            "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
            "pod_date": record.pod_date.isoformat() if record.pod_date else None,
            "delivery_status": record.delivery_status,
            "pgi_status": record.pgi_status,
            "pod_status": record.pod_status,
            "pending_flag": record.pending_flag or False,
            "source_file": record.source_file
        }
    
    def _validate_dn_dates(self, record: DeliveryReport) -> Dict[str, Any]:
        validation = {"is_valid": True, "issues": [], "warnings": [], "durations": {}, "pending_flag": record.pending_flag or False}
        dn_date = record.dn_create_date
        pgi_date = record.good_issue_date
        pod_date = record.pod_date
        missing = []
        if not dn_date:
            missing.append("DN Create Date")
            validation["is_valid"] = False
        if not pgi_date:
            missing.append("PGI Date")
        if not pod_date:
            missing.append("POD Date")
        if missing:
            validation["issues"].append(f"Missing dates: {', '.join(missing)}")
        if dn_date and pgi_date:
            processing_days = (pgi_date - dn_date).days
            if processing_days < 0:
                validation["issues"].append(f"PGI before DN Create (-{abs(processing_days)} days)")
                validation["is_valid"] = False
                validation["durations"]["processing_time_days"] = None
            else:
                validation["durations"]["processing_time_days"] = processing_days
        else:
            validation["durations"]["processing_time_days"] = None
        if pgi_date and pod_date:
            delivery_days = (pod_date - pgi_date).days
            if delivery_days < 0:
                validation["issues"].append(f"POD before PGI (-{abs(delivery_days)} days)")
                validation["is_valid"] = False
                validation["durations"]["delivery_time_days"] = None
            else:
                validation["durations"]["delivery_time_days"] = delivery_days
        else:
            validation["durations"]["delivery_time_days"] = None
        if dn_date and pod_date:
            cycle_days = (pod_date - dn_date).days
            if cycle_days < 0:
                validation["issues"].append(f"POD before DN Create (-{abs(cycle_days)} days)")
                validation["is_valid"] = False
                validation["durations"]["total_cycle_days"] = None
            else:
                validation["durations"]["total_cycle_days"] = cycle_days
        else:
            validation["durations"]["total_cycle_days"] = None
        return validation
    
    def _determine_dn_status(self, record: DeliveryReport) -> str:
        if record.pod_date:
            return "delivered"
        elif record.good_issue_date:
            return "pending_pod"
        elif record.dn_create_date:
            return "pending_pgi"
        return "unknown"
    
    def verify_dn_exists(self, dn_no: str) -> AnalyticsResponse:
        try:
            result = self.repo.verify_dn_exists(dn_no)
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def debug_dn(self, dn_no: str) -> AnalyticsResponse:
        try:
            result = self.repo.debug_dn(dn_no)
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def debug_dealer(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=True, data={"input": dealer_name, "resolved": False, "rows_found": 0})
            dashboard = self.repo.get_dealer_dashboard(resolved)
            return AnalyticsResponse(success=True, data={"input": dealer_name, "resolved": True, "resolved_name": resolved, "rows_found": dashboard.get("total_dns", 0), "profile": self._build_profile(dashboard)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def debug_database(self) -> AnalyticsResponse:
        try:
            result = self.repo.debug_database()
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.metrics["total_requests"] += 1
        try:
            if not dealer_name or not dealer_name.strip():
                return AnalyticsResponse(success=False, error="Dealer name cannot be empty", error_id=str(uuid.uuid4())[:8])
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found", error_id=str(uuid.uuid4())[:8])
            cache_key = f"dashboard:{resolved}"
            dashboard_data = self._get_cached(cache_key)
            if dashboard_data is None:
                dashboard_data = self.repo.get_dealer_dashboard(resolved)
                self.metrics["postgresql_queries"] += 1
                if dashboard_data and "error" not in dashboard_data:
                    self._set_cached(cache_key, dashboard_data, 600)
            if not dashboard_data or "error" in dashboard_data:
                return AnalyticsResponse(success=False, error=dashboard_data.get("error", f"No data for dealer '{resolved}'"), error_id=str(uuid.uuid4())[:8])
            dashboard = self._build_complete_dashboard(dashboard_data, resolved)
            self.metrics["successful_requests"] += 1
            self.metrics["total_duration_ms"] += (time.time() - start_time) * 1000
            return AnalyticsResponse(success=True, data=dashboard)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def _build_complete_dashboard(self, data: Dict, dealer_name: str) -> Dict[str, Any]:
        profile = {"dealer_name": dealer_name, "dealer_code": data.get("dealer_code") or "", "customer_code": data.get("customer_code") or "", "division": data.get("division") or "", "warehouse": data.get("warehouse") or "", "warehouse_code": data.get("warehouse_code") or "", "delivery_location": data.get("delivery_location") or "", "sales_office": data.get("sales_office") or "", "sales_manager": data.get("sales_manager") or "", "city": data.get("city") or "", "dealer_status": data.get("dealer_status") or "Unknown", "first_dn_date": data.get("first_dn_date"), "last_dn_date": data.get("last_dn_date")}
        summary = {"total_dns": data.get("total_dns") or 0, "total_units": data.get("total_units") or 0, "total_revenue": data.get("total_revenue") or 0, "delivered": data.get("delivered_dns") or 0, "in_transit": data.get("transit_dns") or 0, "pending_pgi": data.get("pending_dns") or 0, "pending_pod": data.get("pending_pod_dns") or 0, "pending_flag": data.get("pending_flag_dns") or 0, "delivery_rate": data.get("delivery_rate") or 0, "pod_rate": data.get("pod_rate") or 0}
        aging = {"pending_pgi": data.get("pending_dns") or 0, "pending_pod": data.get("pending_pod_dns") or 0, "avg_delivery_aging": data.get("avg_pgi_aging") or 0, "avg_pod_aging": data.get("avg_pod_aging") or 0, "avg_total_aging": data.get("avg_total_aging") or 0}
        health_score = self._calculate_health_score(data)
        risk_level = self._calculate_risk_level(data)
        performance = {"health_score": health_score, "risk_level": risk_level}
        return {"dealer_name": dealer_name, "request_id": str(uuid.uuid4())[:8], "profile": profile, "summary": summary, "aging": aging, "performance": performance, "generated_at": datetime.now().isoformat()}
    
    def _calculate_health_score(self, data: Dict) -> int:
        delivery_rate = data.get("delivery_rate") or 0
        pod_rate = data.get("pod_rate") or 0
        avg_aging = data.get("avg_total_aging") or 0
        revenue = data.get("total_revenue") or 0
        score = int((min(delivery_rate / 90 * 100, 100) * 0.40) + (min(pod_rate / 90 * 100, 100) * 0.30) + (max(100 - min(avg_aging / 30 * 100, 100), 0) * 0.20) + (min(revenue / 1000000 * 100, 100) * 0.10))
        return min(score, 100)
    
    def _calculate_risk_level(self, data: Dict) -> str:
        delivery_rate = data.get("delivery_rate") or 0
        pod_rate = data.get("pod_rate") or 0
        avg_aging = data.get("avg_total_aging") or 0
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        aging_risk = 0 if avg_aging <= 3 else 50 if avg_aging <= 14 else 100
        risk_score = (delivery_risk + pod_risk + aging_risk) // 3
        if risk_score <= 25: return "Low"
        elif risk_score <= 50: return "Medium"
        else: return "High"
    
    def _build_profile(self, data: Dict) -> Dict[str, Any]:
        return {"dealer_name": data.get("dealer_name") or "", "dealer_code": data.get("dealer_code") or "", "customer_code": data.get("customer_code") or "", "division": data.get("division") or "", "warehouse": data.get("warehouse") or "", "warehouse_code": data.get("warehouse_code") or "", "delivery_location": data.get("delivery_location") or "", "sales_office": data.get("sales_office") or "", "sales_manager": data.get("sales_manager") or "", "city": data.get("city") or "", "dealer_status": data.get("dealer_status") or "Unknown"}
    
    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        try:
            if not city_name or not city_name.strip():
                return AnalyticsResponse(success=False, error="City name cannot be empty", error_id=str(uuid.uuid4())[:8])
            result = self.repo.get_city_dashboard(city_name)
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        try:
            if not warehouse_name or not warehouse_name.strip():
                return AnalyticsResponse(success=False, error="Warehouse name cannot be empty", error_id=str(uuid.uuid4())[:8])
            result = self.repo.get_warehouse_dashboard(warehouse_name)
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        try:
            dealers = self.repo.get_dealer_ranking(limit, top)
            return AnalyticsResponse(success=True, data={"dealers": dealers, "total": len(dealers), "ranking_type": "top" if top else "bottom"})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_city_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        try:
            cities = self.repo.get_city_ranking(limit, top)
            return AnalyticsResponse(success=True, data={"cities": cities, "total": len(cities), "ranking_type": "top" if top else "bottom"})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_warehouse_ranking(self, limit: int = 10, top: bool = True) -> AnalyticsResponse:
        try:
            warehouses = self.repo.get_warehouse_ranking(limit, top)
            return AnalyticsResponse(success=True, data={"warehouses": warehouses, "total": len(warehouses), "ranking_type": "top" if top else "bottom"})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_delivery_performance(self) -> AnalyticsResponse:
        try:
            result = self.repo.get_delivery_performance()
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_root_cause_insights(self) -> AnalyticsResponse:
        try:
            result = self.repo.get_root_cause_insights()
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_control_tower_alerts(self) -> AnalyticsResponse:
        try:
            result = self.repo.get_control_tower_alerts()
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_trend_analysis(self) -> AnalyticsResponse:
        try:
            result = self.repo.get_trend_analysis()
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def compare_dealers(self, dealer1: str, dealer2: str) -> AnalyticsResponse:
        try:
            result = self.repo.compare_dealers(dealer1, dealer2)
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def compare_warehouses(self, warehouse1: str, warehouse2: str) -> AnalyticsResponse:
        try:
            result = self.repo.compare_warehouses(warehouse1, warehouse2)
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def compare_cities(self, city1: str, city2: str) -> AnalyticsResponse:
        try:
            result = self.repo.compare_cities(city1, city2)
            if "error" in result:
                return AnalyticsResponse(success=False, error=result["error"], error_id=str(uuid.uuid4())[:8])
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_revenue(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            return AnalyticsResponse(success=True, data={"total_revenue": dashboard.get("total_revenue", 0), "count": dashboard.get("total_dns", 0), "avg_revenue": dashboard.get("total_revenue", 0) / max(dashboard.get("total_dns", 1), 1)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_units(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            return AnalyticsResponse(success=True, data={"total_units": dashboard.get("total_units", 0), "count": dashboard.get("total_dns", 0), "avg_units": dashboard.get("total_units", 0) / max(dashboard.get("total_dns", 1), 1)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_performance(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            return AnalyticsResponse(success=True, data={"delivery_rate": dashboard.get("delivery_rate", 0), "pod_rate": dashboard.get("pod_rate", 0), "pending_pgi": dashboard.get("pending_dns", 0), "pending_pod": dashboard.get("pending_pod_dns", 0), "avg_aging": dashboard.get("avg_total_aging", 0)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_aging(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            return AnalyticsResponse(success=True, data={"avg_aging": dashboard.get("avg_total_aging", 0), "max_aging": dashboard.get("avg_total_aging", 0), "count": dashboard.get("total_dns", 0)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_product_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            products = self.repo.get_product_dashboard(resolved)
            return AnalyticsResponse(success=True, data={"products": products})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_executive_summary(self) -> AnalyticsResponse:
        try:
            delivery_perf = self.get_delivery_performance()
            metrics = delivery_perf.data.get("metrics", {}) if delivery_perf.success else {}
            top_dealers = self.get_dealer_ranking(limit=5, top=True)
            top_cities = self.get_city_ranking(limit=5, top=True)
            dashboard = {
                "summary": {"total_dns": metrics.get("total_dns", 0), "pgi_rate": metrics.get("pgi_rate", 0), "pod_rate": metrics.get("pod_rate", 0), "avg_processing_days": metrics.get("avg_processing_days", 0), "avg_delivery_days": metrics.get("avg_delivery_days", 0), "pending_flag_count": metrics.get("pending_flag_count", 0)},
                "top_dealers": top_dealers.data.get("dealers", []) if top_dealers.success else [],
                "top_cities": top_cities.data.get("cities", []) if top_cities.success else [],
                "insights": self._generate_executive_insights(metrics),
                "generated_at": datetime.now().isoformat()
            }
            return AnalyticsResponse(success=True, data=dashboard)
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def _generate_executive_insights(self, metrics: Dict) -> List[str]:
        insights = []
        if metrics.get("pgi_rate", 0) >= 90:
            insights.append("✅ Excellent PGI completion rate")
        else:
            insights.append(f"⚠️ PGI rate is {metrics.get('pgi_rate', 0)}% - below target")
        if metrics.get("pod_rate", 0) >= 90:
            insights.append("✅ Excellent POD completion rate")
        else:
            insights.append(f"⚠️ POD rate is {metrics.get('pod_rate', 0)}% - below target")
        if metrics.get("avg_processing_days", 0) <= 3:
            insights.append("✅ Fast average processing time")
        else:
            insights.append(f"⏳ Average processing time is {metrics.get('avg_processing_days', 0)} days")
        if metrics.get("pending_flag_count", 0) > 0:
            insights.append(f"⚠️ {metrics.get('pending_flag_count', 0)} pending flagged DNs require attention")
        return insights
    
    def get_all_dealers_dashboard(self) -> AnalyticsResponse:
        try:
            dealers = self.repo.get_dealer_ranking(limit=100, top=True)
            return AnalyticsResponse(success=True, data={"summary": {"total_dealers": len(dealers), "total_revenue": sum(d.get("total_revenue", 0) for d in dealers)}, "dealers": dealers, "generated_at": datetime.now().isoformat()})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            timeline = self.repo.get_dealer_timeline(resolved, limit)
            return AnalyticsResponse(success=True, data={"dealer_name": resolved, "timeline": timeline, "total": len(timeline)})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_dealer_rankings(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            cache_key = "all_dealers_rankings"
            rankings = self._get_cached(cache_key)
            if rankings is None:
                dealers = self.repo.get_dealer_ranking(limit=1000, top=True)
                rankings = {}
                for i, d in enumerate(dealers, 1):
                    rankings[d.get("dealer_name")] = {"revenue_rank": i, "total_dealers": len(dealers)}
                self._set_cached(cache_key, rankings, 3600)
            if resolved not in rankings:
                return AnalyticsResponse(success=False, error=f"Dealer '{resolved}' not ranked")
            return AnalyticsResponse(success=True, data={"dealer_name": resolved, **rankings[resolved]})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_ai_context(self, dealer_name: str = None) -> AnalyticsResponse:
        if dealer_name:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            return AnalyticsResponse(success=True, data={"dealer_name": resolved, "summary": dashboard.get("summary", {}), "profile": dashboard.get("profile", {}), "context": "AI context from PostgreSQL"})
        else:
            db_health = self.repo.debug_database()
            return AnalyticsResponse(success=True, data={"database": db_health, "context": "Network context from PostgreSQL"})
    
    def get_delivery_aging_analysis(self, dealer_name: str) -> AnalyticsResponse:
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            dashboard = self.repo.get_dealer_dashboard(resolved)
            if "error" in dashboard:
                return AnalyticsResponse(success=False, error=dashboard["error"])
            return AnalyticsResponse(success=True, data={"avg_pgi_aging": dashboard.get("avg_pgi_aging") or 0, "avg_pod_aging": dashboard.get("avg_pod_aging") or 0, "avg_total_aging": dashboard.get("avg_total_aging") or 0, "pending_dns": dashboard.get("pending_dns") or 0, "pending_pod_dns": dashboard.get("pending_pod_dns") or 0})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def get_data_integrity_score(self) -> AnalyticsResponse:
        try:
            db_health = self.repo.debug_database()
            total = db_health.get("distinct_dns") or 0
            return AnalyticsResponse(success=True, data={"total_records": total, "integrity_score": 100 if total > 0 else 0, "quality_status": "Excellent" if total > 100 else "Good" if total > 0 else "No Data"})
        except Exception as e:
            return AnalyticsResponse(success=False, error=str(e), error_id=str(uuid.uuid4())[:8])
    
    def health_check(self) -> Dict[str, Any]:
        status = {"status": "healthy", "timestamp": datetime.now().isoformat(), "version": "10.0", "environment": "Railway" if self.is_railway else "Local", "checks": {}}
        try:
            db_health = self.repo.debug_database()
            if db_health.get("connected"):
                status["checks"]["postgresql"] = {"status": "healthy", "message": f"Connected to {db_health.get('database', 'unknown')}", "rows": db_health.get("rows", 0)}
            else:
                status["checks"]["postgresql"] = {"status": "unhealthy", "message": "Connection failed"}
                status["status"] = "unhealthy"
        except Exception as e:
            status["status"] = "unhealthy"
            status["checks"]["postgresql"] = {"status": "unhealthy", "message": str(e)}
        return status
    
    def get_metrics(self) -> Dict[str, Any]:
        total = self.metrics["total_requests"]
        successful = self.metrics["successful_requests"]
        return {
            "total_requests": total, "successful_requests": successful, "failed_requests": self.metrics["failed_requests"],
            "success_rate": round((successful / max(total, 1)) * 100, 1),
            "avg_duration_ms": round(self.metrics["total_duration_ms"] / max(total, 1), 2),
            "cache_hit_rate": round((self.metrics["cache_hits"] / max(self.metrics["cache_hits"] + self.metrics["cache_misses"], 1)) * 100, 1),
            "postgresql_queries": self.metrics["postgresql_queries"],
            "dn_lookups": self.metrics["dn_lookups"], "dn_lookups_success": self.metrics["dn_lookups_success"],
            "dn_lookups_failure": self.metrics["dn_lookups_failure"],
            "dn_lookups_success_rate": round((self.metrics["dn_lookups_success"] / max(self.metrics["dn_lookups"], 1)) * 100, 1),
            "version": "10.0", "environment": "Railway" if self.is_railway else "Local"
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

_analytics_service = None

def get_analytics_service(use_redis: bool = False) -> AnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService(use_redis=use_redis)
    return _analytics_service

__all__ = [
    'AnalyticsService', 'AnalyticsResponse', 'get_analytics_service',
    'DEALER_NAME_FIELD', 'DEALER_CODE_FIELD', 'CUSTOMER_CODE_FIELD',
    'DN_NO_FIELD', 'DELIVERY_STATUS_FIELD', 'PGI_STATUS_FIELD', 'POD_STATUS_FIELD'
]
