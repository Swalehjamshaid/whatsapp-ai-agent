# ============================================================
# FILE: app/services/dashboard_service.py
# VERSION: 4.3 - REPOSITORY PATTERN (ALIGNED WITH DN_ANALYSIS)
# ============================================================
# PURPOSE: Central orchestration service for the Logistics Intelligence Dashboard.
#          Provides executive KPIs, warehouse/dealer/product/city intelligence,
#          AI-driven insights, and advanced filtering/search.
#
# ARCHITECTURE:
#   - DashboardRepository handles all database queries with proper session mgmt.
#   - DashboardService orchestrates data aggregation and formatting.
#   - All public methods and signatures preserved for backward compatibility.
#   - Uses the same session management pattern as dn_analysis.py.
# ============================================================

import asyncio
import datetime
import hashlib
import json
import logging
import os
import time
from typing import Optional, Dict, List, Any, Union, Tuple
from collections import defaultdict
from functools import wraps
from sqlalchemy import func, and_, extract, Integer, desc, case
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import DeliveryReport

# Optional external libraries (lazy loaded) – preserved
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.pdfgen import canvas
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    EXCEL_AVAILABLE = True
except ImportError:
    EXCEL_AVAILABLE = False

try:
    from pptx import Presentation
    from pptx.util import Inches, Pt
    PPTX_AVAILABLE = True
except ImportError:
    PPTX_AVAILABLE = False

logger = logging.getLogger(__name__)

# ============================================================
# UTILITY FUNCTIONS (mirroring dn_analysis.py)
# ============================================================

def _text(value: Any, default: str = "N/A") -> str:
    """Safely convert value to string."""
    if value is None:
        return default
    return str(value).strip() or default

def _format_currency(amount: float) -> str:
    """Format currency amount."""
    if amount is None:
        return "PKR 0.00"
    if amount >= 1_000_000:
        return f"PKR {amount/1_000_000:.2f}M"
    elif amount >= 1_000:
        return f"PKR {amount:,.0f}"
    return f"PKR {amount:,.0f}"

def _format_number(num: Union[int, float]) -> str:
    """Format number with commas."""
    if num is None:
        return "0"
    return f"{num:,}"

def _format_date(date_val: Any) -> str:
    """Format date to 'dd-MMM-yyyy' (e.g., 23-Jun-2026)"""
    if not date_val:
        return "N/A"
    try:
        if isinstance(date_val, str):
            for fmt in ['%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
                try:
                    dt = datetime.strptime(date_val, fmt)
                    break
                except ValueError:
                    continue
            else:
                return date_val
        elif isinstance(date_val, datetime):
            dt = date_val
        elif isinstance(date_val, date):
            dt = datetime.combine(date_val, datetime.min.time())
        else:
            return str(date_val)
        return dt.strftime("%d-%b-%Y")
    except Exception:
        return str(date_val)

# ============================================================
# CACHE DECORATOR (unchanged)
# ============================================================

class InMemoryCache:
    def __init__(self, ttl_seconds=5):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key):
        entry = self._cache.get(key)
        if entry and (time.time() - entry['timestamp'] < self._ttl):
            return entry['value']
        return None

    def set(self, key, value):
        self._cache[key] = {'value': value, 'timestamp': time.time()}

    def clear(self):
        self._cache.clear()

cache = InMemoryCache(ttl_seconds=5)

def cached(ttl=5):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{hashlib.md5(str(args).encode() + str(kwargs).encode()).hexdigest()}"
            cached_value = cache.get(key)
            if cached_value is not None:
                return cached_value
            result = await func(*args, **kwargs)
            cache.set(key, result)
            return result
        return wrapper
    return decorator

# ============================================================
# DASHBOARD CONTEXT (unchanged)
# ============================================================

class DashboardContext:
    def __init__(self, filters: Dict[str, Any], role: str):
        self.filters = filters
        self.role = role
        self.summary: Optional[Dict[str, Any]] = None
        self.warehouse_performance: Optional[List[Dict[str, Any]]] = None
        self.dealer_performance: Optional[List[Dict[str, Any]]] = None
        self.product_performance: Optional[List[Dict[str, Any]]] = None
        self.city_performance: Optional[List[Dict[str, Any]]] = None
        self.transport_data: Optional[Dict[str, Any]] = None
        self.monthly_trends: Optional[Dict[str, Any]] = None
        self.daily_trends: Optional[Dict[str, Any]] = None
        self.kpis: Optional[Dict[str, Any]] = None
        self.rankings: Optional[Dict[str, Any]] = None
        self.health: Optional[Dict[str, Any]] = None
        self.metadata: Optional[Dict[str, Any]] = None
        self.inventory: Optional[Dict[str, Any]] = None
        self.alerts: Optional[List[Dict[str, Any]]] = None
        self.recommendations: Optional[List[Dict[str, Any]]] = None
        self.loaded = False

# ============================================================
# DASHBOARD REPOSITORY (pattern aligned with dn_analysis.py)
# ============================================================

class DashboardRepository:
    """Handles all database queries for the dashboard, with proper session management."""
    
    def __init__(self):
        self._session_local = SessionLocal
        logger.info("🗄️  DashboardRepository initialized")
    
    def _get_session(self) -> Optional[Session]:
        try:
            return self._session_local()
        except Exception as e:
            logger.error(f"❌ Database session error: {e}")
            return None
    
    def _close_session(self, session: Optional[Session]):
        if session:
            try:
                session.close()
            except Exception as e:
                logger.debug(f"Session close error: {e}")
    
    def get_summary(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Get summary KPIs (revenue, units, DNs, distinct counts, etc.)."""
        session = self._get_session()
        if not session:
            logger.error("❌ No database session available")
            return self._empty_summary()
        
        try:
            query = session.query(DeliveryReport)
            # Apply filters
            if "start_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            if "warehouse" in filters:
                query = query.filter(DeliveryReport.warehouse == filters["warehouse"])
            if "dealer" in filters:
                query = query.filter(DeliveryReport.dealer_code == filters["dealer"])
            if "product" in filters:
                query = query.filter(DeliveryReport.material_no == filters["product"])
            if "city" in filters:
                query = query.filter(DeliveryReport.ship_to_city == filters["city"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            
            total_revenue = query.with_entities(func.sum(DeliveryReport.dn_amount)).scalar() or 0.0
            total_units = query.with_entities(func.sum(DeliveryReport.dn_qty)).scalar() or 0
            total_dn = query.with_entities(func.count(DeliveryReport.dn_no)).scalar() or 0
            
            # Distinct counts
            dealers = session.query(DeliveryReport.dealer_code).filter(DeliveryReport.dealer_code.isnot(None)).distinct().count()
            warehouses = session.query(DeliveryReport.warehouse).filter(DeliveryReport.warehouse.isnot(None)).distinct().count()
            cities = session.query(DeliveryReport.ship_to_city).filter(DeliveryReport.ship_to_city.isnot(None)).distinct().count()
            products = session.query(DeliveryReport.material_no).filter(DeliveryReport.material_no.isnot(None)).distinct().count()
            
            # Average delivery days – handle NULL good_issue_date
            avg_delivery = query.with_entities(
                func.avg(
                    case(
                        (DeliveryReport.good_issue_date.isnot(None),
                         func.extract('day', func.age(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date))),
                        else_=0
                    )
                )
            ).scalar() or 0.0
            
            # POD completion rate
            pod_completed = query.filter(DeliveryReport.pod_status == 'Delivered').count()
            total_with_pod = query.filter(DeliveryReport.pod_status.isnot(None)).count()
            pod_rate = (pod_completed / (total_with_pod or 1)) * 100
            
            self._close_session(session)
            return {
                "total_revenue": total_revenue,
                "total_units": total_units,
                "total_delivery_notes": total_dn,
                "active_dealers": dealers,
                "active_warehouses": warehouses,
                "active_cities": cities,
                "active_products": products,
                "active_transporters": 0,
                "average_delivery_days": avg_delivery,
                "average_pod_days": 0.0,
                "average_pgi_days": 0.0,
                "delivery_achievement_rate": pod_rate,
                "pod_completion_rate": pod_rate,
                "otif_percentage": 0.0,
                "inventory_accuracy": 0.0,
                "dashboard_health_score": 70.0,
                "last_database_refresh": datetime.datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.exception("❌ Failed to get summary")
            self._close_session(session)
            return self._empty_summary()
    
    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "inventory_accuracy": 0.0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }
    
    def get_warehouse_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Get aggregated performance per warehouse."""
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(
                DeliveryReport.warehouse,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(DeliveryReport.dn_no).label('dn'),
                func.avg(
                    case(
                        (DeliveryReport.good_issue_date.isnot(None),
                         func.extract('day', func.age(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date))),
                        else_=0
                    )
                ).label('avg_delivery')
            ).filter(DeliveryReport.warehouse.isnot(None))
            
            if "start_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            if "dealer" in filters:
                query = query.filter(DeliveryReport.dealer_code == filters["dealer"])
            if "product" in filters:
                query = query.filter(DeliveryReport.material_no == filters["product"])
            if "city" in filters:
                query = query.filter(DeliveryReport.ship_to_city == filters["city"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            
            data = query.group_by(DeliveryReport.warehouse).all()
            self._close_session(session)
            result = []
            for row in data:
                avg_del = row.avg_delivery or 0.0
                # Use helper methods from the service (we'll pass them later)
                # For now, compute grade and risk inline
                grade = self._compute_grade(avg_del)
                risk = self._compute_risk(avg_del)
                result.append({
                    "warehouse_code": row.warehouse,
                    "warehouse_name": row.warehouse,
                    "revenue": row.revenue or 0.0,
                    "units": row.units or 0,
                    "delivery_notes": row.dn or 0,
                    "dealers": 0,
                    "products": 0,
                    "cities": 0,
                    "average_delivery_days": avg_del,
                    "average_pod_days": 0.0,
                    "average_pgi_days": 0.0,
                    "otif": 0.0,
                    "capacity": 0,
                    "utilization": 0,
                    "pending_deliveries": 0,
                    "late_deliveries": 0,
                    "performance_grade": grade,
                    "risk_level": risk,
                    "ai_recommendation": self._warehouse_recommendation(row.warehouse, grade, risk),
                })
            return result
        except Exception as e:
            logger.exception("❌ Failed to get warehouse performance")
            self._close_session(session)
            return []
    
    def get_dealer_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(
                DeliveryReport.dealer_code,
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(DeliveryReport.dn_no).label('dn'),
                func.avg(
                    case(
                        (DeliveryReport.good_issue_date.isnot(None),
                         func.extract('day', func.age(DeliveryReport.good_issue_date, DeliveryReport.dn_create_date))),
                        else_=0
                    )
                ).label('avg_delivery')
            ).filter(DeliveryReport.dealer_code.isnot(None))
            
            if "start_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            if "warehouse" in filters:
                query = query.filter(DeliveryReport.warehouse == filters["warehouse"])
            if "product" in filters:
                query = query.filter(DeliveryReport.material_no == filters["product"])
            if "city" in filters:
                query = query.filter(DeliveryReport.ship_to_city == filters["city"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            
            data = query.group_by(DeliveryReport.dealer_code, DeliveryReport.customer_name).all()
            self._close_session(session)
            result = []
            for row in data:
                revenue = row.revenue or 0.0
                units = row.units or 0
                avg_del = row.avg_delivery or 0.0
                score = self._compute_dealer_score(revenue, units, avg_del)
                result.append({
                    "dealer_name": row.customer_name or row.dealer_code,
                    "dealer_code": row.dealer_code,
                    "revenue": revenue,
                    "units": units,
                    "delivery_notes": row.dn or 0,
                    "products": 0,
                    "cities": 0,
                    "warehouses": 0,
                    "average_delivery_days": avg_del,
                    "average_pod_days": 0.0,
                    "average_pgi_days": 0.0,
                    "last_delivery": None,
                    "last_order": None,
                    "growth_percentage": 0.0,
                    "rank": 0,
                    "performance_score": score,
                    "ai_recommendation": self._dealer_recommendation(row.dealer_code, score, avg_del),
                })
            return result
        except Exception as e:
            logger.exception("❌ Failed to get dealer performance")
            self._close_session(session)
            return []
    
    def get_product_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(
                DeliveryReport.material_no,
                DeliveryReport.customer_model,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(DeliveryReport.dn_no).label('dn')
            ).filter(DeliveryReport.material_no.isnot(None))
            
            if "start_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            if "warehouse" in filters:
                query = query.filter(DeliveryReport.warehouse == filters["warehouse"])
            if "dealer" in filters:
                query = query.filter(DeliveryReport.dealer_code == filters["dealer"])
            if "city" in filters:
                query = query.filter(DeliveryReport.ship_to_city == filters["city"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            
            data = query.group_by(DeliveryReport.material_no, DeliveryReport.customer_model).all()
            self._close_session(session)
            result = []
            for row in data:
                units = row.units or 0
                is_slow = units < 100
                is_fast = units > 300
                result.append({
                    "product_name": row.customer_model or row.material_no,
                    "sku": row.material_no,
                    "revenue": row.revenue or 0.0,
                    "units": units,
                    "dealers": 0,
                    "warehouses": 0,
                    "cities": 0,
                    "monthly_trend": [],
                    "average_delivery_days": 0.0,
                    "slow_moving_flag": is_slow,
                    "fast_moving_flag": is_fast,
                    "growth_percentage": 0.0,
                    "ai_recommendation": self._product_recommendation(row.material_no, is_slow, is_fast),
                })
            return result
        except Exception as e:
            logger.exception("❌ Failed to get product performance")
            self._close_session(session)
            return []
    
    def get_city_performance(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        session = self._get_session()
        if not session:
            return []
        try:
            query = session.query(
                DeliveryReport.ship_to_city,
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(DeliveryReport.dn_no).label('dn')
            ).filter(DeliveryReport.ship_to_city.isnot(None))
            
            if "start_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                query = query.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            if "warehouse" in filters:
                query = query.filter(DeliveryReport.warehouse == filters["warehouse"])
            if "dealer" in filters:
                query = query.filter(DeliveryReport.dealer_code == filters["dealer"])
            if "product" in filters:
                query = query.filter(DeliveryReport.material_no == filters["product"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            
            data = query.group_by(DeliveryReport.ship_to_city).all()
            self._close_session(session)
            result = []
            for row in data:
                result.append({
                    "city": row.ship_to_city,
                    "revenue": row.revenue or 0.0,
                    "units": row.units or 0,
                    "dealers": 0,
                    "warehouses": 0,
                    "products": 0,
                    "average_distance": 0.0,
                    "average_delivery_days": 0.0,
                    "pending_deliveries": 0,
                    "late_deliveries": 0,
                    "delivery_target": 0.0,
                    "achievement_percentage": 0.0,
                    "risk_level": "Low",
                })
            return result
        except Exception as e:
            logger.exception("❌ Failed to get city performance")
            self._close_session(session)
            return []
    
    def get_monthly_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        session = self._get_session()
        if not session:
            return {"months": [], "revenue": [], "units": [], "delivery_notes": [], "pod_rate": []}
        try:
            month_label = func.to_char(DeliveryReport.dn_create_date, 'YYYY-MM')
            rev = session.query(
                month_label.label('month'),
                func.sum(DeliveryReport.dn_amount).label('value')
            ).filter(DeliveryReport.dn_create_date.isnot(None))
            if "start_date" in filters:
                rev = rev.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                rev = rev.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            rev = rev.group_by('month').order_by('month').all()
            months = [r.month for r in rev]
            revenue_data = [r.value or 0.0 for r in rev]
            
            units = session.query(
                month_label.label('month'),
                func.sum(DeliveryReport.dn_qty).label('value')
            ).filter(DeliveryReport.dn_create_date.isnot(None))
            if "start_date" in filters:
                units = units.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                units = units.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            units = units.group_by('month').order_by('month').all()
            units_data = [u.value or 0 for u in units]
            
            dn = session.query(
                month_label.label('month'),
                func.count(DeliveryReport.dn_no).label('value')
            ).filter(DeliveryReport.dn_create_date.isnot(None))
            if "start_date" in filters:
                dn = dn.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                dn = dn.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            dn = dn.group_by('month').order_by('month').all()
            dn_data = [d.value or 0 for d in dn]
            
            pod = session.query(
                month_label.label('month'),
                func.count(DeliveryReport.id).label('total'),
                func.sum(func.cast(DeliveryReport.pod_status == 'Delivered', Integer)).label('delivered')
            ).filter(DeliveryReport.dn_create_date.isnot(None))
            if "start_date" in filters:
                pod = pod.filter(DeliveryReport.dn_create_date >= filters["start_date"])
            if "end_date" in filters:
                pod = pod.filter(DeliveryReport.dn_create_date <= filters["end_date"])
            pod = pod.group_by('month').order_by('month').all()
            pod_data = [(p.delivered / (p.total or 1)) * 100 for p in pod] if pod else []
            
            self._close_session(session)
            return {
                "months": months,
                "revenue": revenue_data,
                "units": units_data,
                "delivery_notes": dn_data,
                "pod_rate": pod_data,
            }
        except Exception as e:
            logger.exception("❌ Failed to get monthly trends")
            self._close_session(session)
            return {"months": [], "revenue": [], "units": [], "delivery_notes": [], "pod_rate": []}
    
    def get_daily_trends(self, filters: Dict[str, Any]) -> Dict[str, List]:
        session = self._get_session()
        if not session:
            return {"dates": [], "revenue": [], "units": [], "delivery_notes": []}
        try:
            start_date = datetime.datetime.utcnow() - datetime.timedelta(days=30)
            query = session.query(
                DeliveryReport.dn_create_date.label('date'),
                func.sum(DeliveryReport.dn_amount).label('revenue'),
                func.sum(DeliveryReport.dn_qty).label('units'),
                func.count(DeliveryReport.dn_no).label('dn')
            ).filter(DeliveryReport.dn_create_date >= start_date)
            if "warehouse" in filters:
                query = query.filter(DeliveryReport.warehouse == filters["warehouse"])
            if "dealer" in filters:
                query = query.filter(DeliveryReport.dealer_code == filters["dealer"])
            if "product" in filters:
                query = query.filter(DeliveryReport.material_no == filters["product"])
            if "city" in filters:
                query = query.filter(DeliveryReport.ship_to_city == filters["city"])
            if "division" in filters:
                query = query.filter(DeliveryReport.division == filters["division"])
            if "status" in filters:
                query = query.filter(DeliveryReport.delivery_status == filters["status"])
            data = query.group_by(DeliveryReport.dn_create_date).order_by(DeliveryReport.dn_create_date).all()
            self._close_session(session)
            dates = [d.date.strftime('%Y-%m-%d') for d in data]
            revenue = [d.revenue or 0.0 for d in data]
            units = [d.units or 0 for d in data]
            dn = [d.dn or 0 for d in data]
            return {"dates": dates, "revenue": revenue, "units": units, "delivery_notes": dn}
        except Exception as e:
            logger.exception("❌ Failed to get daily trends")
            self._close_session(session)
            return {"dates": [], "revenue": [], "units": [], "delivery_notes": []}
    
    def get_health(self) -> Dict[str, Any]:
        session = self._get_session()
        if not session:
            return {"status": "unhealthy", "message": "No database connection"}
        try:
            count = session.query(func.count(DeliveryReport.id)).scalar() or 0
            self._close_session(session)
            return {"status": "healthy" if count > 0 else "unhealthy", "message": "Data available" if count > 0 else "No data", "record_count": count}
        except Exception as e:
            self._close_session(session)
            return {"status": "unhealthy", "message": str(e)}
    
    def get_record_count(self) -> int:
        session = self._get_session()
        if not session:
            return 0
        try:
            count = session.query(func.count(DeliveryReport.id)).scalar() or 0
            self._close_session(session)
            return count
        except Exception:
            self._close_session(session)
            return 0
    
    # Helper methods (mirroring those in DashboardService)
    @staticmethod
    def _compute_grade(avg_delivery: float) -> str:
        if avg_delivery <= 2:
            return "A"
        elif avg_delivery <= 4:
            return "B"
        else:
            return "C"
    
    @staticmethod
    def _compute_risk(avg_delivery: float) -> str:
        if avg_delivery <= 2:
            return "Low"
        elif avg_delivery <= 4:
            return "Medium"
        else:
            return "High"
    
    @staticmethod
    def _compute_dealer_score(revenue: float, units: int, avg_delivery: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        return min(score, 100)
    
    @staticmethod
    def _warehouse_recommendation(code: str, grade: str, risk: str) -> str:
        if grade in ("A", "B") and risk == "Low":
            return "Maintain current operations."
        elif grade == "C" or risk == "Medium":
            return "Review processes and improve OTIF."
        else:
            return "Urgent intervention required: capacity and delivery issues."
    
    @staticmethod
    def _dealer_recommendation(code: str, score: float, avg_delivery: float) -> str:
        if score >= 80:
            return "Top performer – consider loyalty rewards."
        elif score >= 60:
            return "Good performance – focus on reducing delivery days."
        else:
            return "Needs improvement – provide training and support."
    
    @staticmethod
    def _product_recommendation(code: str, slow: bool, fast: bool) -> str:
        if slow:
            return "Consider discounting or discontinuing this product."
        elif fast:
            return "Increase inventory levels and marketing."
        else:
            return "Monitor performance closely."

# ============================================================
# DASHBOARD SERVICE (orchestrates repository calls)
# ============================================================

class DashboardService:
    def __init__(self, analytics_repository=None, analytics_service=None):
        self.repo = analytics_repository
        self.service = analytics_service
        self.logger = logger.getChild(self.__class__.__name__)
        self._context_cache: Dict[str, DashboardContext] = {}
        self._db_repo = DashboardRepository()  # new repository for DB access

    @cached(ttl=5)
    async def get_dashboard_data(
        self,
        filters: Optional[Dict[str, Any]] = None,
        role: str = "viewer",
        limit: int = 100,
        offset: int = 0
    ) -> Dict[str, Any]:
        filters = filters or {}
        context = await self._get_or_load_context(filters, role, limit, offset)
        return {
            "executive": await self._build_executive_summary(context),
            "cards": await self._build_cards(context),
            "charts": await self._prepare_charts(context),
            "warehouse": context.warehouse_performance,
            "dealer": context.dealer_performance,
            "city": context.city_performance,
            "product": context.product_performance,
            "transport": context.transport_data,
            "inventory": await self._build_inventory(context),
            "ranking": context.rankings,
            "alerts": await self._generate_alerts(context),
            "recommendations": await self._generate_recommendations(context),
            "filters": filters,
            "exports": {
                "pdf": "/dashboard/export/pdf",
                "excel": "/dashboard/export/excel",
                "pptx": "/dashboard/export/pptx",
                "csv": "/dashboard/export/csv"
            },
            "metadata": context.metadata,
            "pagination": {"limit": limit, "offset": offset, "total": len(context.dealer_performance or [])}
        }

    async def _get_or_load_context(self, filters: Dict, role: str, limit: int, offset: int) -> DashboardContext:
        cache_key = hashlib.md5(json.dumps(filters, sort_keys=True).encode()).hexdigest()
        context = self._context_cache.get(cache_key)
        if not context:
            context = DashboardContext(filters, role)
            self._context_cache[cache_key] = context
        if not context.loaded:
            await self._load_dashboard_context(context, limit, offset)
            context.loaded = True
        return context

    async def _load_dashboard_context(self, context: DashboardContext, limit: int, offset: int) -> None:
        filters = context.filters
        try:
            (context.summary,
             context.warehouse_performance,
             context.dealer_performance,
             context.product_performance,
             context.city_performance,
             context.transport_data,
             context.monthly_trends,
             context.daily_trends,
             context.kpis,
             context.rankings,
             context.health,
             context.metadata,
             context.inventory) = await asyncio.gather(
                self._load_summary(filters),
                self._load_warehouse_performance(filters, limit, offset),
                self._load_dealer_performance(filters, limit, offset),
                self._load_product_performance(filters, limit, offset),
                self._load_city_performance(filters, limit, offset),
                self._load_transport_data(filters),
                self._load_monthly_trends(filters),
                self._load_daily_trends(filters),
                self._load_kpis(filters),
                self._load_rankings(filters, limit),
                self._load_health(filters),
                self._load_metadata(filters),
                self._load_inventory(filters)
            )
        except Exception as e:
            self.logger.exception("Failed to load dashboard context")
            raise

    # ----------------------------------------------------------------------
    # Individual loaders – now using the repository
    # ----------------------------------------------------------------------

    async def _load_summary(self, filters: Dict) -> Dict[str, Any]:
        # Run the repository method in a thread to avoid blocking
        return await asyncio.to_thread(self._db_repo.get_summary, filters)

    async def _load_warehouse_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        # Note: limit/offset are ignored in the repository (we return all)
        return await asyncio.to_thread(self._db_repo.get_warehouse_performance, filters)

    async def _load_dealer_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_dealer_performance, filters)

    async def _load_product_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_product_performance, filters)

    async def _load_city_performance(self, filters: Dict, limit: int, offset: int) -> List[Dict]:
        return await asyncio.to_thread(self._db_repo.get_city_performance, filters)

    async def _load_transport_data(self, filters: Dict) -> Dict[str, Any]:
        # No transport data in DeliveryReport – return placeholder
        return {"transport_breakdown": {}, "average_lead_time": 0.0, "vehicle_count": 0, "transporter_count": 0}

    async def _load_monthly_trends(self, filters: Dict) -> Dict[str, List]:
        return await asyncio.to_thread(self._db_repo.get_monthly_trends, filters)

    async def _load_daily_trends(self, filters: Dict) -> Dict[str, List]:
        return await asyncio.to_thread(self._db_repo.get_daily_trends, filters)

    async def _load_kpis(self, filters: Dict) -> Dict[str, Any]:
        summary = await self._load_summary(filters)
        # Compute growth from last 30 days vs previous 30
        today = datetime.datetime.utcnow().date()
        last_30_start = today - datetime.timedelta(days=30)
        prev_30_start = today - datetime.timedelta(days=60)
        
        def get_revenue(start_d, end_d):
            # Use repository method to get revenue for a date range
            # We'll create a temporary filter for the range
            range_filters = filters.copy()
            range_filters["start_date"] = start_d
            range_filters["end_date"] = end_d
            result = self._db_repo.get_summary(range_filters)
            return result.get("total_revenue", 0.0)
        
        current_rev = await asyncio.to_thread(get_revenue, last_30_start, today)
        prev_rev = await asyncio.to_thread(get_revenue, prev_30_start, last_30_start - timedelta(days=1))
        revenue_growth = ((current_rev - prev_rev) / (prev_rev or 1)) * 100
        
        return {
            "revenue": summary.get("total_revenue", 0.0),
            "units": summary.get("total_units", 0),
            "delivery_notes": summary.get("total_delivery_notes", 0),
            "dealers": summary.get("active_dealers", 0),
            "warehouses": summary.get("active_warehouses", 0),
            "cities": summary.get("active_cities", 0),
            "products": summary.get("active_products", 0),
            "average_delivery_days": summary.get("average_delivery_days", 0.0),
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "pod_percentage": summary.get("pod_completion_rate", 0.0),
            "pgi_percentage": 0.0,
            "delivery_achievement_percentage": summary.get("delivery_achievement_rate", 0.0),
            "late_deliveries": 0,
            "pending_deliveries": 0,
            "on_time_delivery_rate": 0.0,
            "damage_percentage": 0.0,
            "otif_percentage": 0.0,
            "fill_rate": 0.0,
            "warehouse_utilization": 0.0,
            "revenue_growth": revenue_growth,
            "unit_growth": 0.0,
            "dn_growth": 0.0,
            "top_warehouse": None,
            "top_dealer": None,
            "top_product": None,
            "top_city": None,
        }

    async def _load_rankings(self, filters: Dict, limit: int) -> Dict[str, List]:
        # Not implemented – return empty
        return {"warehouses": [], "dealers": [], "products": [], "cities": []}

    async def _load_health(self, filters: Dict) -> Dict[str, Any]:
        return await asyncio.to_thread(self._db_repo.get_health)

    async def _load_metadata(self, filters: Dict) -> Dict[str, Any]:
        record_count = await asyncio.to_thread(self._db_repo.get_record_count)
        return {
            "application_version": "4.3.0",
            "database_version": "PostgreSQL (repository)",
            "postgresql_status": "connected",
            "database_size": "N/A",
            "record_count": record_count,
            "last_refresh": datetime.datetime.utcnow().isoformat(),
            "last_etl_run": None,
            "generated_by": "DashboardService v4.3",
            "report_time": datetime.datetime.utcnow().isoformat(),
            "time_zone": "UTC",
            "environment": os.getenv("ENVIRONMENT", "production"),
            "ai_model": "Built-in",
            "execution_time_ms": 0
        }

    async def _load_inventory(self, filters: Dict) -> Dict[str, Any]:
        return {"total_products": 0, "total_units": 0, "warehouse_stock": [], "slow_moving": [], "fast_moving": []}

    # ----------------------------------------------------------------------
    # Builders (unchanged)
    # ----------------------------------------------------------------------
    async def _build_executive_summary(self, context: DashboardContext) -> Dict[str, Any]:
        summary = context.summary or {}
        return {
            "total_revenue": summary.get("total_revenue", 0.0),
            "total_units": summary.get("total_units", 0),
            "total_delivery_notes": summary.get("total_delivery_notes", 0),
            "active_dealers": summary.get("active_dealers", 0),
            "active_warehouses": summary.get("active_warehouses", 0),
            "active_cities": summary.get("active_cities", 0),
            "active_products": summary.get("active_products", 0),
            "active_transporters": summary.get("active_transporters", 0),
            "otif": summary.get("otif_percentage", 0.0),
            "pod_rate": summary.get("pod_completion_rate", 0.0),
            "delivery_achievement": summary.get("delivery_achievement_rate", 0.0),
            "health_score": summary.get("dashboard_health_score", 0),
            "last_refresh": summary.get("last_database_refresh"),
        }

    async def _build_cards(self, context: DashboardContext) -> Dict[str, Any]:
        summary = context.summary or {}
        kpis = context.kpis or {}
        cards = {
            "revenue": {
                "value": summary.get("total_revenue", 0.0),
                "target": 150000000,
                "trend": kpis.get("revenue_growth", 0.0),
                "progress": min(summary.get("total_revenue", 0) / 150000000 * 100, 100),
                "icon": "fa-chart-line",
                "color": "primary"
            },
            "units": {
                "value": summary.get("total_units", 0),
                "target": 10000,
                "trend": kpis.get("unit_growth", 0.0),
                "progress": min(summary.get("total_units", 0) / 10000 * 100, 100),
                "icon": "fa-box",
                "color": "success"
            },
            "delivery_notes": {
                "value": summary.get("total_delivery_notes", 0),
                "target": 5000,
                "trend": kpis.get("dn_growth", 0.0),
                "progress": min(summary.get("total_delivery_notes", 0) / 5000 * 100, 100),
                "icon": "fa-file-invoice",
                "color": "info"
            },
            "dealers": {
                "value": summary.get("active_dealers", 0),
                "target": 200,
                "trend": 0.0,
                "progress": min(summary.get("active_dealers", 0) / 200 * 100, 100),
                "icon": "fa-users",
                "color": "warning"
            },
            "warehouses": {
                "value": summary.get("active_warehouses", 0),
                "target": 50,
                "trend": 0.0,
                "progress": min(summary.get("active_warehouses", 0) / 50 * 100, 100),
                "icon": "fa-warehouse",
                "color": "danger"
            },
            "cities": {
                "value": summary.get("active_cities", 0),
                "target": 100,
                "trend": 0.0,
                "progress": min(summary.get("active_cities", 0) / 100 * 100, 100),
                "icon": "fa-city",
                "color": "secondary"
            },
            "otif": {
                "value": summary.get("otif_percentage", 0.0),
                "target": 95.0,
                "trend": 0.0,
                "progress": min(summary.get("otif_percentage", 0) / 95 * 100, 100),
                "icon": "fa-check-circle",
                "color": "success"
            },
            "pod_rate": {
                "value": summary.get("pod_completion_rate", 0.0),
                "target": 90.0,
                "trend": 0.0,
                "progress": min(summary.get("pod_completion_rate", 0) / 90 * 100, 100),
                "icon": "fa-truck",
                "color": "info"
            }
        }
        return cards

    async def _prepare_charts(self, context: DashboardContext) -> Dict[str, Any]:
        monthly = context.monthly_trends or {}
        daily = context.daily_trends or {}
        return {
            "revenue_trend": {"labels": monthly.get("months", []), "data": monthly.get("revenue", [])},
            "units_trend": {"labels": monthly.get("months", []), "data": monthly.get("units", [])},
            "dn_trend": {"labels": monthly.get("months", []), "data": monthly.get("delivery_notes", [])},
            "pod_trend": {"labels": monthly.get("months", []), "data": monthly.get("pod_rate", [])},
            "daily_trend": {"labels": daily.get("dates", []), "data": daily.get("revenue", [])},
            "warehouse_ranking": context.rankings.get("warehouses", []) if context.rankings else [],
            "dealer_ranking": context.rankings.get("dealers", []) if context.rankings else [],
            "product_ranking": context.rankings.get("products", []) if context.rankings else [],
            "city_ranking": context.rankings.get("cities", []) if context.rankings else []
        }

    async def _build_inventory(self, context: DashboardContext) -> Dict[str, Any]:
        return {"total_products": 0, "total_units": 0, "warehouse_stock": []}

    # ----------------------------------------------------------------------
    # Alerts and recommendations (unchanged)
    # ----------------------------------------------------------------------
    async def _generate_alerts(self, context: DashboardContext) -> List[Dict[str, Any]]:
        alerts = []
        kpis = context.kpis or {}
        summary = context.summary or {}
        if kpis.get("late_deliveries", 0) > 10:
            alerts.append({
                "level": "critical",
                "message": f"{kpis.get('late_deliveries', 0)} late deliveries detected. Immediate action required.",
                "action": "Review logistics routes and dispatch schedules."
            })
        if kpis.get("pending_deliveries", 0) > 20:
            alerts.append({
                "level": "warning",
                "message": f"{kpis.get('pending_deliveries', 0)} pending deliveries need processing.",
                "action": "Prioritize shipment processing."
            })
        if summary.get("pod_completion_rate", 100) < 80:
            alerts.append({
                "level": "warning",
                "message": f"POD completion rate is {summary.get('pod_completion_rate', 0):.1f}% below target (80%).",
                "action": "Investigate proof of delivery bottlenecks."
            })
        if summary.get("otif_percentage", 100) < 85:
            alerts.append({
                "level": "warning",
                "message": f"OTIF is {summary.get('otif_percentage', 0):.1f}% below target (85%).",
                "action": "Improve on-time delivery performance."
            })
        if kpis.get("revenue_growth", 0) > 5:
            alerts.append({
                "level": "normal",
                "message": f"Revenue growth is {kpis.get('revenue_growth', 0):.1f}% – positive trend.",
                "action": "Maintain current strategies."
            })
        return alerts

    async def _generate_recommendations(self, context: DashboardContext) -> List[Dict[str, Any]]:
        recommendations = []
        for wh in context.warehouse_performance or []:
            if wh.get("risk_level") == "High":
                recommendations.append({
                    "entity": wh.get("warehouse_name"),
                    "type": "warehouse",
                    "risk": "High",
                    "recommendation": wh.get("ai_recommendation", "Review operations immediately."),
                    "priority": "Critical"
                })
            elif wh.get("performance_grade") == "D":
                recommendations.append({
                    "entity": wh.get("warehouse_name"),
                    "type": "warehouse",
                    "risk": "Medium",
                    "recommendation": "Improve OTIF and reduce delivery days.",
                    "priority": "High"
                })
        for dlr in context.dealer_performance or []:
            if dlr.get("performance_score", 100) < 50:
                recommendations.append({
                    "entity": dlr.get("dealer_name"),
                    "type": "dealer",
                    "risk": "High",
                    "recommendation": "Provide additional support and training.",
                    "priority": "High"
                })
        for prod in context.product_performance or []:
            if prod.get("slow_moving_flag", False):
                recommendations.append({
                    "entity": prod.get("product_name"),
                    "type": "product",
                    "risk": "Low",
                    "recommendation": "Consider discounting or discontinuing.",
                    "priority": "Medium"
                })
            if prod.get("fast_moving_flag", False):
                recommendations.append({
                    "entity": prod.get("product_name"),
                    "type": "product",
                    "risk": "Low",
                    "recommendation": "Increase inventory and promote sales.",
                    "priority": "Low"
                })
        return recommendations

    # ----------------------------------------------------------------------
    # Helper methods (preserved)
    # ----------------------------------------------------------------------
    def _compute_performance_grade(self, otif: float, avg_delivery: float, utilization: float) -> str:
        if otif >= 95 and avg_delivery <= 2 and utilization <= 85:
            return "A"
        elif otif >= 85 and avg_delivery <= 4 and utilization <= 90:
            return "B"
        elif otif >= 70:
            return "C"
        else:
            return "D"

    def _compute_risk_level(self, pending: int, late: int, avg_delivery: float) -> str:
        if late > 10 or pending > 20 or avg_delivery > 5:
            return "High"
        elif late > 5 or pending > 10 or avg_delivery > 3:
            return "Medium"
        else:
            return "Low"

    def _compute_dealer_score(self, revenue: float, units: int, avg_delivery: float, growth: float) -> float:
        score = 0.0
        if revenue > 0:
            score += min(revenue / 1000000, 1) * 40
        if units > 0:
            score += min(units / 1000, 1) * 30
        if avg_delivery > 0:
            score += max(0, (5 - avg_delivery) / 5) * 20
        score += min(max(growth / 10, 0), 1) * 10
        return min(score, 100)

    def _generate_warehouse_recommendation(self, code: str, grade: str, risk: str) -> str:
        if grade in ("A", "B") and risk == "Low":
            return "Maintain current operations."
        elif grade == "C" or risk == "Medium":
            return "Review processes and improve OTIF."
        else:
            return "Urgent intervention required: capacity and delivery issues."

    def _generate_dealer_recommendation(self, code: str, score: float, avg_delivery: float) -> str:
        if score >= 80:
            return "Top performer – consider loyalty rewards."
        elif score >= 60:
            return "Good performance – focus on reducing delivery days."
        else:
            return "Needs improvement – provide training and support."

    def _generate_product_recommendation(self, code: str, slow: bool, fast: bool, growth: float) -> str:
        if slow:
            return "Consider discounting or discontinuing this product."
        elif fast:
            return "Increase inventory levels and marketing."
        elif growth > 5:
            return "Product gaining traction – invest more."
        else:
            return "Monitor performance closely."

    async def _compute_dashboard_health(self, filters: Dict) -> float:
        return 70.0

    def _empty_summary(self) -> Dict[str, Any]:
        return {
            "total_revenue": 0.0,
            "total_units": 0,
            "total_delivery_notes": 0,
            "active_dealers": 0,
            "active_warehouses": 0,
            "active_cities": 0,
            "active_products": 0,
            "active_transporters": 0,
            "average_delivery_days": 0.0,
            "average_pod_days": 0.0,
            "average_pgi_days": 0.0,
            "delivery_achievement_rate": 0.0,
            "pod_completion_rate": 0.0,
            "otif_percentage": 0.0,
            "inventory_accuracy": 0.0,
            "dashboard_health_score": 0.0,
            "last_database_refresh": None,
        }

    # ----------------------------------------------------------------------
    # Individual getters (backward compatibility)
    # ----------------------------------------------------------------------
    async def get_dashboard_summary(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        return await self._load_summary(filters or {})

    async def get_dashboard_cards(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        context = DashboardContext(filters or {}, role)
        await self._load_dashboard_context(context, 100, 0)
        return await self._build_cards(context)

    async def get_kpi_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        return await self._load_kpis(filters or {})

    async def get_warehouse_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        warehouses = await self._load_warehouse_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_warehouse_metrics(warehouses)
        return {"warehouses": warehouses, "ranking": ranking, "summary": summary}

    async def get_dealer_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        dealers = await self._load_dealer_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_dealer_metrics(dealers)
        return {"dealers": dealers, "ranking": ranking, "summary": summary}

    async def get_product_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        products = await self._load_product_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_product_metrics(products)
        return {"products": products, "ranking": ranking, "summary": summary}

    async def get_city_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer", limit: int = 100, offset: int = 0) -> Dict:
        cities = await self._load_city_performance(filters or {}, limit, offset)
        ranking = []
        summary = await self._aggregate_city_metrics(cities)
        return {"cities": cities, "ranking": ranking, "summary": summary}

    async def get_transport_dashboard(self, filters: Optional[Dict] = None, role: str = "viewer") -> Dict:
        return await self._load_transport_data(filters or {})

    async def get_dashboard_statistics(self, filters: Optional[Dict] = None) -> Dict:
        return await self._load_statistics(filters or {})

    async def get_dashboard_health(self) -> Dict:
        return await self._load_health({})

    async def get_last_refresh(self) -> Dict:
        return {"last_refresh": datetime.datetime.utcnow().isoformat()}

    async def get_growth_statistics(self, filters: Optional[Dict] = None) -> Dict[str, float]:
        return {"revenue_growth": 0.0, "units_growth": 0.0, "delivery_notes_growth": 0.0}

    # ----------------------------------------------------------------------
    # Aggregation helpers (preserved)
    # ----------------------------------------------------------------------
    async def _aggregate_warehouse_metrics(self, warehouses: List[Dict]) -> Dict:
        if not warehouses:
            return {}
        total_revenue = sum(w.get("revenue", 0) for w in warehouses)
        total_units = sum(w.get("units", 0) for w in warehouses)
        total_dn = sum(w.get("delivery_notes", 0) for w in warehouses)
        avg_delivery = sum(w.get("average_delivery_days", 0) for w in warehouses) / len(warehouses)
        avg_util = sum(w.get("utilization", 0) for w in warehouses) / len(warehouses)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "total_delivery_notes": total_dn,
            "average_delivery_days": avg_delivery,
            "average_utilization": avg_util,
            "warehouse_count": len(warehouses),
        }

    async def _aggregate_dealer_metrics(self, dealers: List[Dict]) -> Dict:
        if not dealers:
            return {}
        total_revenue = sum(d.get("revenue", 0) for d in dealers)
        total_units = sum(d.get("units", 0) for d in dealers)
        total_dn = sum(d.get("delivery_notes", 0) for d in dealers)
        avg_score = sum(d.get("performance_score", 0) for d in dealers) / len(dealers)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "total_delivery_notes": total_dn,
            "average_performance_score": avg_score,
            "dealer_count": len(dealers),
        }

    async def _aggregate_product_metrics(self, products: List[Dict]) -> Dict:
        if not products:
            return {}
        total_revenue = sum(p.get("revenue", 0) for p in products)
        total_units = sum(p.get("units", 0) for p in products)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "product_count": len(products),
        }

    async def _aggregate_city_metrics(self, cities: List[Dict]) -> Dict:
        if not cities:
            return {}
        total_revenue = sum(c.get("revenue", 0) for c in cities)
        total_units = sum(c.get("units", 0) for c in cities)
        return {
            "total_revenue": total_revenue,
            "total_units": total_units,
            "city_count": len(cities),
        }
