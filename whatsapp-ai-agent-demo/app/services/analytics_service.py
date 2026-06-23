# ==========================================================
# FILE: app/services/analytics_service.py (v31.0 - ENTERPRISE)
# PURPOSE: PRODUCTION-GRADE ANALYTICS ENGINE - POSTGRESQL ONLY
# VERSION: 31.0 - Enterprise Refactoring - No Dummy Data
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
import re
import math
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, case, desc, asc, cast, String, text, distinct
from functools import lru_cache
import json
import hashlib
import threading

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection

# ==========================================================
# BLOCK 1.1: POSTGRESQL HEALTH ENGINE
# ==========================================================

class PostgreSQLHealthEngine:
    """
    Comprehensive PostgreSQL health validation.
    Validates table existence, column integrity, and data quality.
    """
    
    @staticmethod
    def validate_database() -> Dict[str, Any]:
        """
        Perform complete database health check.
        Returns detailed validation results.
        """
        result = {
            "healthy": False,
            "connected": False,
            "table_exists": False,
            "record_count": 0,
            "columns_validated": {},
            "statistics": {},
            "errors": [],
            "warnings": []
        }
        
        try:
            db = SessionLocal()
            
            # Check table exists
            try:
                total_records = db.query(DeliveryReport).count()
                result["record_count"] = total_records
                result["table_exists"] = True
                result["connected"] = True
                logger.info(f"✅ Table 'delivery_reports' exists with {total_records} records")
            except Exception as e:
                result["errors"].append(f"Table validation failed: {str(e)}")
                db.close()
                return result
            
            if total_records == 0:
                result["warnings"].append("No records found in delivery_reports table")
                db.close()
                result["healthy"] = False
                return result
            
            # Validate columns
            columns_to_validate = [
                "customer_name", "dealer_code", "customer_code",
                "warehouse", "ship_to_city", "dn_no", "dn_qty", "dn_amount"
            ]
            
            for col in columns_to_validate:
                try:
                    count = db.query(DeliveryReport).filter(
                        getattr(DeliveryReport, col).isnot(None)
                    ).count()
                    result["columns_validated"][col] = count > 0
                    if count > 0:
                        logger.info(f"✅ Column '{col}' validated ({count} non-null values)")
                    else:
                        result["warnings"].append(f"Column '{col}' has all NULL values")
                except Exception as e:
                    result["errors"].append(f"Column '{col}' validation failed: {str(e)}")
                    result["columns_validated"][col] = False
            
            # Get statistics
            try:
                result["statistics"] = {
                    "total_records": total_records,
                    "total_dns": db.query(func.count(distinct(DeliveryReport.dn_no))).scalar() or 0,
                    "total_dealers": db.query(func.count(distinct(DeliveryReport.customer_name))).scalar() or 0,
                    "total_warehouses": db.query(func.count(distinct(DeliveryReport.warehouse))).scalar() or 0,
                    "total_cities": db.query(func.count(distinct(DeliveryReport.ship_to_city))).scalar() or 0
                }
                logger.info(f"📊 Statistics: {result['statistics']}")
            except Exception as e:
                result["errors"].append(f"Statistics query failed: {str(e)}")
            
            db.close()
            
            # Determine health
            result["healthy"] = (
                result["connected"] and
                result["table_exists"] and
                result["record_count"] > 0 and
                len([v for v in result["columns_validated"].values() if v]) >= 5 and
                len(result["errors"]) == 0
            )
            
            return result
            
        except Exception as e:
            result["errors"].append(f"Database connection failed: {str(e)}")
            result["healthy"] = False
            return result

# ==========================================================
# BLOCK 1.2: POSTGRESQL HEALTH CHECK FUNCTION
# ==========================================================

def validate_postgresql_health() -> Dict[str, Any]:
    """Convenience function for PostgreSQL health check."""
    return PostgreSQLHealthEngine.validate_database()

# ==========================================================
# BLOCK 2: CONSTANTS
# ==========================================================

CACHE_TTL_SECONDS = 300
QUERY_TIMEOUT_SECONDS = 10
MAX_RETRY_ATTEMPTS = 3
MAX_RESPONSE_LENGTH = 2500
SEARCH_LIMIT = 20
TOP_LIMIT = 10

# ==========================================================
# BLOCK 3: RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    """
    Standard response contract for all analytics endpoints.
    Compatible with ai_provider_service.py validation logic.
    """
    
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, error: str = None, error_id: str = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "error_id": self.error_id,
            "timestamp": self.timestamp
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# ==========================================================
# BLOCK 4: DATE VALIDATION ENGINE
# ==========================================================

class DateValidator:
    """
    PRODUCTION DATE VALIDATION ENGINE.
    
    CRITICAL BUSINESS RULE:
    ALL dates are interpreted as YEAR-DATE-MONTH (YYYY-DD-MM)
    """
    
    @staticmethod
    @lru_cache(maxsize=1024)
    def parse_business_date(raw_value: Any) -> Optional[datetime]:
        """Parse date using intelligent format detection."""
        if raw_value is None:
            return None
        
        if isinstance(raw_value, datetime):
            return raw_value
        
        raw_str = str(raw_value).strip()
        if not raw_str:
            return None
        
        try:
            parts = raw_str.split("-")
            if len(parts) != 3:
                return None
            
            year = int(parts[0])
            pos2 = int(parts[1])
            pos3 = int(parts[2])
            
            if not (1900 <= year <= 2100):
                return None
            
            # Try YYYY-DD-MM first
            if 1 <= pos3 <= 12 and 1 <= pos2 <= 31:
                try:
                    return datetime(year, pos3, pos2)
                except ValueError:
                    pass
            
            # Try YYYY-MM-DD (auto-swap)
            if 1 <= pos2 <= 12 and 1 <= pos3 <= 31:
                try:
                    return datetime(year, pos2, pos3)
                except ValueError:
                    pass
            
            return None
            
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def calculate_aging(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        """Calculate aging metrics."""
        create_date = DateValidator.parse_business_date(create_date)
        pgi_date = DateValidator.parse_business_date(pgi_date)
        pod_date = DateValidator.parse_business_date(pod_date)
        
        today = datetime.now().date()
        
        result = {
            "delivery_aging": None,
            "pod_aging": None,
            "total_cycle": None,
            "delivery_aging_text": "N/A",
            "pod_aging_text": "N/A",
            "total_cycle_text": "N/A",
            "pgi_completed": False,
            "pod_received": False,
            "delivery_completed": False
        }
        
        # Calculate Delivery Aging
        if create_date and pgi_date:
            delivery_aging = max(0, (pgi_date.date() - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = DateValidator._format_aging(delivery_aging)
            result["pgi_completed"] = True
        elif create_date and not pgi_date:
            delivery_aging = max(0, (today - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = f"{DateValidator._format_aging(delivery_aging)} (Pending PGI)"
        
        # Calculate POD Aging
        if pgi_date and pod_date:
            pod_aging = max(0, (pod_date.date() - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = DateValidator._format_aging(pod_aging)
            result["pod_received"] = True
        elif pgi_date and not pod_date:
            pod_aging = max(0, (today - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = f"{DateValidator._format_aging(pod_aging)} (Pending POD)"
        
        # Calculate Total Cycle
        if create_date and pod_date:
            total_cycle = max(0, (pod_date.date() - create_date.date()).days)
            result["total_cycle"] = total_cycle
            result["total_cycle_text"] = DateValidator._format_aging(total_cycle)
            result["delivery_completed"] = True
        elif create_date and not pod_date:
            if pgi_date:
                result["total_cycle_text"] = "In Progress (POD Pending)"
            else:
                result["total_cycle_text"] = "In Progress (PGI Pending)"
        
        return result
    
    @staticmethod
    def _format_aging(days: int) -> str:
        """Format aging for display."""
        if days is None:
            return "N/A"
        if days == 0:
            return "Same Day"
        elif days == 1:
            return "1 Day"
        else:
            return f"{days} Days"

# ==========================================================
# BLOCK 5: KPI ENGINE
# ==========================================================

class KPIEngine:
    """Business KPI calculation engine - PostgreSQL only."""
    
    @staticmethod
    def calculate_delivery_rate(delivered_dns: int, total_dns: int) -> float:
        if total_dns == 0:
            return 0.0
        return round((delivered_dns / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pgi_rate(pgi_completed: int, in_transit: int, total_dns: int) -> float:
        if total_dns == 0:
            return 0.0
        return round(((pgi_completed + in_transit) / total_dns) * 100, 1)
    
    @staticmethod
    def calculate_pod_rate(pod_completed: int, delivered_dns: int) -> float:
        if delivered_dns == 0:
            return 0.0
        return round((pod_completed / delivered_dns) * 100, 1)
    
    @staticmethod
    def calculate_health_score(metrics: Dict[str, float]) -> int:
        delivery_rate = metrics.get("delivery_rate", 0)
        pod_rate = metrics.get("pod_rate", 0)
        avg_aging = metrics.get("avg_aging", 0)
        revenue = metrics.get("revenue", 0)
        
        score = (
            (delivery_rate / 100 * 40) +
            (pod_rate / 100 * 30) +
            ((100 - min(avg_aging / 30 * 100, 100)) / 100 * 20) +
            (min(revenue / 1000000 * 100, 100) / 100 * 10)
        )
        return min(int(score), 100)
    
    @staticmethod
    def calculate_risk_level(delivery_rate: float, pod_rate: float, avg_aging: float) -> Tuple[str, int]:
        delivery_risk = 0 if delivery_rate >= 90 else 50 if delivery_rate >= 70 else 100
        pod_risk = 0 if pod_rate >= 90 else 50 if pod_rate >= 70 else 100
        aging_risk = 0 if avg_aging <= 3 else 50 if avg_aging <= 7 else 100
        
        risk_score = (delivery_risk + pod_risk + aging_risk) // 3
        
        if risk_score <= 25:
            return "Low", risk_score
        elif risk_score <= 50:
            return "Medium", risk_score
        elif risk_score <= 75:
            return "High", risk_score
        else:
            return "Critical", risk_score

# ==========================================================
# BLOCK 6: SEARCH ENGINE
# ==========================================================

class SearchEngine:
    """Universal PostgreSQL Search Engine."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def search_dn(self, query: str, exact: bool = False) -> List[Dict[str, Any]]:
        """Search for DNs in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = re.sub(r'[^0-9]', '', str(query).strip())
        if len(query_clean) < 8 or len(query_clean) > 12:
            return []
        
        try:
            if exact:
                results = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == query_clean
                ).limit(SEARCH_LIMIT).all()
            else:
                results = self.db.query(DeliveryReport).filter(
                    DeliveryReport.dn_no.like(f"%{query_clean}%")
                ).limit(SEARCH_LIMIT).all()
            
            return [{
                "dn_no": r.dn_no,
                "customer_name": r.customer_name,
                "warehouse": r.warehouse,
                "ship_to_city": r.ship_to_city,
                "dn_amount": r.dn_amount,
                "dn_qty": r.dn_qty,
                "delivery_status": r.delivery_status
            } for r in results]
            
        except Exception as e:
            logger.error(f"DN search error: {e}")
            return []
    
    def search_dealer(self, query: str, exact: bool = False) -> List[Dict[str, Any]]:
        """Search for dealers in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            if exact:
                results = self.db.query(
                    func.distinct(DeliveryReport.customer_name)
                ).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(query_clean)
                ).limit(SEARCH_LIMIT).all()
            else:
                results = self.db.query(
                    func.distinct(DeliveryReport.customer_name)
                ).filter(
                    DeliveryReport.customer_name.ilike(f"%{query_clean}%")
                ).limit(SEARCH_LIMIT).all()
            
            if not results and not exact:
                tokens = query_clean.split()
                for token in tokens:
                    if len(token) > 2:
                        token_results = self.db.query(
                            func.distinct(DeliveryReport.customer_name)
                        ).filter(
                            DeliveryReport.customer_name.ilike(f"%{token}%")
                        ).limit(SEARCH_LIMIT).all()
                        if token_results:
                            results = token_results
                            break
            
            return [{"dealer_name": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Dealer search error: {e}")
            return []
    
    def search_warehouse(self, query: str) -> List[Dict[str, Any]]:
        """Search for warehouses in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.warehouse)
            ).filter(
                DeliveryReport.warehouse.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"warehouse": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Warehouse search error: {e}")
            return []
    
    def search_city(self, query: str) -> List[Dict[str, Any]]:
        """Search for cities in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.ship_to_city)
            ).filter(
                DeliveryReport.ship_to_city.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"city": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"City search error: {e}")
            return []
    
    def search_product(self, query: str) -> List[Dict[str, Any]]:
        """Search for products in PostgreSQL."""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.customer_model)
            ).filter(
                DeliveryReport.customer_model.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.customer_model.isnot(None),
                DeliveryReport.customer_model != ''
            ).limit(SEARCH_LIMIT).all()
            
            if not results:
                results = self.db.query(
                    func.distinct(DeliveryReport.material_no)
                ).filter(
                    DeliveryReport.material_no.ilike(f"%{query_clean}%")
                ).filter(
                    DeliveryReport.material_no.isnot(None),
                    DeliveryReport.material_no != ''
                ).limit(SEARCH_LIMIT).all()
                return [{"product": r[0]} for r in results if r[0]]
            
            return [{"product": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Product search error: {e}")
            return []
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """Verify if a DN exists in PostgreSQL."""
        query_clean = re.sub(r'[^0-9]', '', str(dn_no).strip())
        if len(query_clean) < 8 or len(query_clean) > 12:
            return {"dn": dn_no, "found": False, "error": "Invalid DN format"}
        
        try:
            exists = self.db.query(
                self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == query_clean
                ).exists()
            ).scalar()
            
            return {"dn": dn_no, "found": exists, "normalized": query_clean}
            
        except Exception as e:
            logger.error(f"DN verification error: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def verify_dealer_exists(self, dealer_name: str) -> bool:
        """Verify if a dealer exists in PostgreSQL."""
        if not dealer_name or not dealer_name.strip():
            return False
        
        try:
            exists = self.db.query(
                self.db.query(DeliveryReport).filter(
                    func.lower(DeliveryReport.customer_name) == func.lower(dealer_name.strip())
                ).exists()
            ).scalar()
            return exists
        except Exception as e:
            logger.error(f"Dealer verification error: {e}")
            return False

# ==========================================================
# BLOCK 7: ENTITY RESOLVER
# ==========================================================

class EntityResolver:
    """Entity resolution engine using PostgreSQL."""
    
    def __init__(self, db: Session):
        self.db = db
    
    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        """Resolve dealer name using PostgreSQL with multiple strategies."""
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        
        try:
            # STRATEGY 1: Exact match
            result = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if result:
                return result[0]
            
            # STRATEGY 2: ILIKE match
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if result:
                return result[0]
            
            # STRATEGY 3: Token-based matching
            tokens = dealer_input.split()
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        return result[0]
            
            # STRATEGY 4: Fuzzy matching
            dealers = self.db.query(
                func.distinct(DeliveryReport.customer_name)
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).limit(1000).all()
            
            best_match = None
            best_score = 0
            dealer_input_lower = dealer_input.lower()
            
            for dealer in dealers:
                if not dealer[0]:
                    continue
                dealer_name = dealer[0]
                dealer_lower = dealer_name.lower()
                
                # Simple similarity scoring
                if dealer_input_lower in dealer_lower or dealer_lower in dealer_input_lower:
                    score = 0.8
                else:
                    # Character overlap
                    overlap = len(set(dealer_input_lower) & set(dealer_lower))
                    score = overlap / max(len(dealer_input_lower), len(dealer_lower))
                
                if score > best_score and score > 0.3:
                    best_score = score
                    best_match = dealer_name
            
            return best_match
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return None
    
    def resolve_warehouse(self, warehouse_input: str) -> Optional[str]:
        """Resolve warehouse name using PostgreSQL."""
        if not warehouse_input or not warehouse_input.strip():
            return None
        
        warehouse_input = warehouse_input.strip()
        
        try:
            result = self.db.query(DeliveryReport.warehouse).filter(
                func.lower(DeliveryReport.warehouse) == func.lower(warehouse_input)
            ).first()
            if result:
                return result[0]
            
            result = self.db.query(DeliveryReport.warehouse).filter(
                DeliveryReport.warehouse.ilike(f"%{warehouse_input}%")
            ).first()
            if result:
                return result[0]
            
            tokens = warehouse_input.split()
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.warehouse).filter(
                        DeliveryReport.warehouse.ilike(f"%{token}%")
                    ).first()
                    if result:
                        return result[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Warehouse resolution error: {e}")
            return None
    
    def resolve_city(self, city_input: str) -> Optional[str]:
        """Resolve city name using PostgreSQL."""
        if not city_input or not city_input.strip():
            return None
        
        city_input = city_input.strip()
        
        try:
            result = self.db.query(DeliveryReport.ship_to_city).filter(
                func.lower(DeliveryReport.ship_to_city) == func.lower(city_input)
            ).first()
            if result:
                return result[0]
            
            result = self.db.query(DeliveryReport.ship_to_city).filter(
                DeliveryReport.ship_to_city.ilike(f"%{city_input}%")
            ).first()
            if result:
                return result[0]
            
            tokens = city_input.split()
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.ship_to_city).filter(
                        DeliveryReport.ship_to_city.ilike(f"%{token}%")
                    ).first()
                    if result:
                        return result[0]
            
            return None
            
        except Exception as e:
            logger.error(f"City resolution error: {e}")
            return None
    
    def resolve_product(self, product_input: str) -> Optional[str]:
        """Resolve product name using PostgreSQL."""
        if not product_input or not product_input.strip():
            return None
        
        product_input = product_input.strip()
        
        try:
            result = self.db.query(DeliveryReport.customer_model).filter(
                func.lower(DeliveryReport.customer_model) == func.lower(product_input)
            ).first()
            if result and result[0]:
                return result[0]
            
            result = self.db.query(DeliveryReport.material_no).filter(
                func.lower(DeliveryReport.material_no) == func.lower(product_input)
            ).first()
            if result and result[0]:
                return result[0]
            
            result = self.db.query(DeliveryReport.customer_model).filter(
                DeliveryReport.customer_model.ilike(f"%{product_input}%")
            ).first()
            if result and result[0]:
                return result[0]
            
            return None
            
        except Exception as e:
            logger.error(f"Product resolution error: {e}")
            return None
    
    def resolve_dn(self, dn_input: str) -> Optional[str]:
        """Resolve DN number using PostgreSQL."""
        if not dn_input or not dn_input.strip():
            return None
        
        normalized = re.sub(r'[^0-9]', '', str(dn_input).strip())
        if len(normalized) < 8 or len(normalized) > 12:
            return None
        
        try:
            result = self.db.query(DeliveryReport.dn_no).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            if result:
                return result[0]
            return None
            
        except Exception as e:
            logger.error(f"DN resolution error: {e}")
            return None

# ==========================================================
# BLOCK 8: ANALYTICS REPOSITORY
# ==========================================================

class AnalyticsRepository:
    """PostgreSQL-driven analytics repository."""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.resolver = EntityResolver(self.db)
        self.search = SearchEngine(self.db)
        
        # Initialize Dealer 360 Dashboard (lazy)
        self._dealer_360 = None
        try:
            from app.services.dealer_analytics_service import Dealer360Dashboard
            self._dealer_360 = Dealer360Dashboard(self.db, self.resolver, self.search)
            logger.info("✅ Dealer360Dashboard initialized")
        except ImportError as e:
            logger.warning(f"⚠️ Dealer360Dashboard import error: {e}")
            self._dealer_360 = None
        except Exception as e:
            logger.warning(f"⚠️ Dealer360Dashboard init error: {e}")
            self._dealer_360 = None
        
        # Validate required methods
        required_methods = [
            "get_dn_dashboard", "get_dealer_dashboard", "get_warehouse_dashboard",
            "get_city_dashboard", "get_product_dashboard", "get_pgi_dashboard",
            "get_pod_dashboard", "get_delivery_dashboard", "get_executive_dashboard",
            "get_control_tower_dashboard", "get_revenue_dashboard", "get_ranking_dashboard",
            "get_aging_dashboard"
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(self, method):
                missing_methods.append(method)
                logger.error(f"❌ Missing method: {method}")
        
        if missing_methods:
            logger.error(f"❌ Missing {len(missing_methods)} required methods")
        else:
            logger.info("✅ AnalyticsRepository initialized with all required methods")
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    # ==========================================================
    # BLOCK 9: DEALER 360 DASHBOARD
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete 360° dealer dashboard."""
        if self._dealer_360 is None:
            return {"error": "Dealer 360 dashboard service not available"}
        
        try:
            result = self._dealer_360.get_dashboard(dealer_name)
            if result and "error" not in result:
                result['_dashboard_type'] = '360'
            return result
        except Exception as e:
            logger.error(f"Dealer 360 dashboard error: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 10: DN DASHBOARD
# ==========================================================

    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Complete DN dashboard - PostgreSQL only.
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"📄 Processing DN: '{dn_no}'")
            
            # Validate DN format
            normalized = re.sub(r'[^0-9]', '', str(dn_no).strip())
            if len(normalized) < 8 or len(normalized) > 12:
                return {"error": f"Invalid DN format: {dn_no}. Must be 8-12 digits."}
            
            # Query the record
            try:
                record = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized
                ).first()
            except Exception as e:
                logger.error(f"❌ Database query failed: {e}")
                return {"error": f"Database error: {str(e)}"}
            
            if not record:
                # Try to find similar DNs
                suggestions = []
                try:
                    similar = self.db.query(DeliveryReport.dn_no).filter(
                        DeliveryReport.dn_no.like(f"%{normalized[-4:]}%")
                    ).limit(5).all()
                    if similar:
                        suggestions = [s[0] for s in similar]
                except Exception as e:
                    logger.warning(f"Could not find similar DNs: {e}")
                
                if suggestions:
                    return {
                        "error": f"DN {dn_no} not found",
                        "suggestions": suggestions
                    }
                
                return {"error": f"DN {dn_no} not found"}
            
            # Calculate aging
            aging_result = DateValidator.calculate_aging(
                record.dn_create_date,
                record.good_issue_date,
                record.pod_date
            )
            
            # Build response
            response = {
                "dn_number": record.dn_no,
                "customer_name": record.customer_name or "",
                "dealer_code": record.dealer_code or "",
                "customer_code": record.customer_code or "",
                "warehouse": record.warehouse or "",
                "warehouse_code": record.warehouse_code or "",
                "ship_to_city": record.ship_to_city or "",
                "sales_office": record.sales_office or "",
                "sales_manager": record.sales_manager or "",
                "division": record.division or "",
                "customer_model": record.customer_model or "",
                "material_no": record.material_no or "",
                "units": int(record.dn_qty) if record.dn_qty else 0,
                "amount": float(record.dn_amount) if record.dn_amount else 0,
                "dn_create_date": record.dn_create_date.isoformat() if record.dn_create_date else None,
                "good_issue_date": record.good_issue_date.isoformat() if record.good_issue_date else None,
                "pod_date": record.pod_date.isoformat() if record.pod_date else None,
                "delivery_status": record.delivery_status or "Unknown",
                "pgi_status": record.pgi_status or "",
                "pod_status": record.pod_status or "",
                "pending_flag": record.pending_flag if record.pending_flag is not None else False,
                "delivery_aging": aging_result.get("delivery_aging"),
                "pod_aging": aging_result.get("pod_aging"),
                "total_cycle": aging_result.get("total_cycle"),
                "delivery_aging_text": aging_result.get("delivery_aging_text", "N/A"),
                "pod_aging_text": aging_result.get("pod_aging_text", "N/A"),
                "total_cycle_text": aging_result.get("total_cycle_text", "N/A"),
                "pgi_completed": aging_result.get("pgi_completed", False),
                "pod_received": aging_result.get("pod_received", False),
                "delivery_completed": aging_result.get("delivery_completed", False)
            }
            
            total_time = time.time() - start_time
            logger.info(f"✅ DN {normalized} dashboard built in {total_time:.3f}s")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get DN dashboard failed: {e}")
            return {"error": f"Failed to load DN: {str(e)[:100]}"}

# ==========================================================
# BLOCK 11: DEALER DASHBOARD
# ==========================================================

    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Complete dealer dashboard - PostgreSQL only.
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"🔍 Searching for dealer: '{dealer_name}'")
            
            if not dealer_name or not dealer_name.strip():
                return {"error": "Dealer name is required"}
            
            # Resolve dealer
            resolved = self.resolver.resolve_dealer(dealer_name)
            
            if not resolved:
                similar = self.search.search_dealer(dealer_name, exact=False)
                if similar:
                    suggestions = [s['dealer_name'] for s in similar[:5]]
                    return {
                        "error": f"Dealer '{dealer_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Get dealer profile
            profile_result = self.db.query(
                DeliveryReport.dealer_code,
                DeliveryReport.customer_code,
                DeliveryReport.division,
                DeliveryReport.warehouse,
                DeliveryReport.ship_to_city,
                DeliveryReport.sales_office,
                DeliveryReport.sales_manager
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(resolved)
            ).order_by(
                DeliveryReport.dn_create_date.desc()
            ).first()
            
            # Query metrics
            metrics_result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns")
            ).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(resolved)
            ).first()
            
            if not metrics_result or metrics_result.total_dns == 0:
                return {"error": f"No data found for dealer '{resolved}'"}
            
            total_dns = metrics_result.total_dns or 0
            delivered_dns = metrics_result.delivered_dns or 0
            transit_dns = metrics_result.transit_dns or 0
            pod_completed = metrics_result.pod_completed_dns or 0
            
            delivery_rate = KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            pgi_rate = KPIEngine.calculate_pgi_rate(delivered_dns, transit_dns, total_dns)
            pod_rate = KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            
            risk_level, risk_score = KPIEngine.calculate_risk_level(delivery_rate, pod_rate, 0)
            
            response = {
                "dealer_name": resolved,
                "dealer_code": profile_result[0] if profile_result and profile_result[0] else "",
                "customer_code": profile_result[1] if profile_result and profile_result[1] else "",
                "division": profile_result[2] if profile_result and profile_result[2] else "",
                "warehouse": profile_result[3] if profile_result and profile_result[3] else "",
                "city": profile_result[4] if profile_result and profile_result[4] else "",
                "sales_office": profile_result[5] if profile_result and profile_result[5] else "",
                "sales_manager": profile_result[6] if profile_result and profile_result[6] else "",
                "total_dns": total_dns,
                "total_units": int(metrics_result.total_units or 0),
                "total_revenue": float(metrics_result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": metrics_result.pending_dns or 0,
                "transit_dns": transit_dns,
                "pod_completed_dns": pod_completed,
                "delivery_rate": delivery_rate,
                "pgi_rate": pgi_rate,
                "pod_rate": pod_rate,
                "health_score": KPIEngine.calculate_health_score({
                    "delivery_rate": delivery_rate,
                    "pod_rate": pod_rate,
                    "avg_aging": 0,
                    "revenue": float(metrics_result.total_revenue or 0)
                }),
                "risk_level": risk_level,
                "risk_score": risk_score
            }
            
            total_time = time.time() - start_time
            logger.info(f"✅ Dealer dashboard built for: {resolved} in {total_time:.3f}s")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get dealer dashboard failed: {e}")
            return {"error": f"Failed to load dealer data: {str(e)[:100]}"}

# ==========================================================
# BLOCK 12: WAREHOUSE DASHBOARD
# ==========================================================

    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Complete warehouse dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for warehouse: '{warehouse_name}'")
            
            resolved = self.resolver.resolve_warehouse(warehouse_name)
            
            if not resolved:
                similar = self.search.search_warehouse(warehouse_name)
                if similar:
                    suggestions = [s['warehouse'] for s in similar[:5]]
                    return {
                        "error": f"Warehouse '{warehouse_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            result = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("cities_served"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).filter(
                DeliveryReport.warehouse == resolved
            ).group_by(DeliveryReport.warehouse).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"No data found for warehouse '{resolved}'"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            return {
                "warehouse": resolved,
                "warehouse_code": result.warehouse_code or "",
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "cities_served": result.cities_served or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get warehouse dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 13: CITY DASHBOARD
# ==========================================================

    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Complete city dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for city: '{city_name}'")
            
            resolved = self.resolver.resolve_city(city_name)
            
            if not resolved:
                similar = self.search.search_city(city_name)
                if similar:
                    suggestions = [s['city'] for s in similar[:5]]
                    return {
                        "error": f"City '{city_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"City '{city_name}' not found"}
            
            result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city == resolved
            ).group_by(DeliveryReport.ship_to_city).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"No data found for city '{resolved}'"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            return {
                "city_name": resolved,
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "total_warehouses": result.total_warehouses or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get city dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 14: PRODUCT DASHBOARD
# ==========================================================

    def get_product_dashboard(self, product_name: str) -> Dict[str, Any]:
        """Complete product dashboard - PostgreSQL only."""
        try:
            logger.info(f"🔍 Searching for product: '{product_name}'")
            
            resolved = self.resolver.resolve_product(product_name)
            
            if not resolved:
                similar = self.search.search_product(product_name)
                if similar:
                    suggestions = [s['product'] for s in similar[:5]]
                    return {
                        "error": f"Product '{product_name}' not found",
                        "suggestions": suggestions
                    }
                return {"error": f"Product '{product_name}' not found"}
            
            result = self.db.query(
                func.coalesce(DeliveryReport.customer_model, DeliveryReport.material_no).label("product"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                func.count(distinct(DeliveryReport.customer_name)).label("dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("cities"),
                func.count(distinct(DeliveryReport.warehouse)).label("warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered")
            ).filter(
                or_(
                    DeliveryReport.customer_model == resolved,
                    DeliveryReport.material_no == resolved
                )
            ).group_by(
                DeliveryReport.customer_model,
                DeliveryReport.material_no
            ).first()
            
            if not result or result.dns == 0:
                return {"error": f"No data found for product '{resolved}'"}
            
            total_dns = result.dns or 1
            
            return {
                "product": resolved,
                "revenue": float(result.revenue or 0),
                "units": int(result.units or 0),
                "dns": total_dns,
                "dealers": result.dealers or 0,
                "cities": result.cities or 0,
                "warehouses": result.warehouses or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(result.delivered or 0, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get product dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 15: PGI DASHBOARD
# ==========================================================

    def get_pgi_dashboard(self) -> Dict[str, Any]:
        """PGI dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.isnot(None), DeliveryReport.dn_no), else_=None))).label("pgi_completed"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pgi_pending"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("in_transit")
            ).first()
            
            total = result.total_dns or 1
            pgi_completed = result.pgi_completed or 0
            in_transit = result.in_transit or 0
            
            return {
                "total_dns": total,
                "pgi_completed": pgi_completed,
                "pgi_pending": result.pgi_pending or 0,
                "in_transit": in_transit,
                "pgi_rate": KPIEngine.calculate_pgi_rate(pgi_completed, in_transit, total)
            }
            
        except Exception as e:
            logger.error(f"Get PGI dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 16: POD DASHBOARD
# ==========================================================

    def get_pod_dashboard(self) -> Dict[str, Any]:
        """POD dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pod_pending"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns")
            ).first()
            
            total = result.total_dns or 1
            pod_completed = result.pod_completed or 0
            delivered_dns = result.delivered_dns or 0
            
            return {
                "total_dns": total,
                "pod_completed": pod_completed,
                "pod_pending": result.pod_pending or 0,
                "delivered_dns": delivered_dns,
                "pod_rate": KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"Get POD dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 17: DELIVERY DASHBOARD
# ==========================================================

    def get_delivery_dashboard(self) -> Dict[str, Any]:
        """Delivery dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("in_transit"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi")
            ).first()
            
            total = result.total_dns or 1
            delivered = result.delivered or 0
            in_transit = result.in_transit or 0
            
            return {
                "total_dns": total,
                "delivered": delivered,
                "in_transit": in_transit,
                "pending_pgi": result.pending_pgi or 0,
                "pending": result.pending or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered, total),
                "pgi_rate": KPIEngine.calculate_pgi_rate(delivered, in_transit, total)
            }
            
        except Exception as e:
            logger.error(f"Get delivery dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 18: EXECUTIVE DASHBOARD
# ==========================================================

    def get_executive_dashboard(self) -> Dict[str, Any]:
        """Executive dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("total_cities"),
                func.count(distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns")
            ).first()
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            
            return {
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "total_dealers": result.total_dealers or 0,
                "total_cities": result.total_cities or 0,
                "total_warehouses": result.total_warehouses or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get executive dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 19: CONTROL TOWER DASHBOARD
# ==========================================================

    def get_control_tower_dashboard(self) -> Dict[str, Any]:
        """Control tower dashboard - PostgreSQL only."""
        try:
            pgi_alerts = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                func.date_part('day', func.now() - DeliveryReport.dn_create_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.is_(None),
                DeliveryReport.dn_create_date.isnot(None),
                func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 7
            ).order_by(desc("days_old")).limit(10).all()
            
            pod_alerts = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                func.date_part('day', func.now() - DeliveryReport.good_issue_date).label("days_old")
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.pod_date.is_(None),
                func.date_part('day', func.now() - DeliveryReport.good_issue_date) > 7
            ).order_by(desc("days_old")).limit(10).all()
            
            alerts = []
            for r in pgi_alerts:
                days = int(r.days_old or 0)
                alerts.append({
                    "type": "Pending PGI",
                    "severity": "high" if days > 15 else "medium",
                    "description": f"DN {r.dn_no} for {r.customer_name} pending PGI for {days} days"
                })
            
            for r in pod_alerts:
                days = int(r.days_old or 0)
                alerts.append({
                    "type": "Pending POD",
                    "severity": "critical" if days > 30 else "high" if days > 15 else "medium",
                    "description": f"DN {r.dn_no} for {r.customer_name} pending POD for {days} days"
                })
            
            return {
                "alerts": alerts[:20],
                "critical_count": sum(1 for a in alerts if a.get("severity") == "critical"),
                "high_count": sum(1 for a in alerts if a.get("severity") == "high"),
                "total_alerts": len(alerts)
            }
            
        except Exception as e:
            logger.error(f"Get control tower dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 20: REVENUE DASHBOARD
# ==========================================================

    def get_revenue_dashboard(self) -> Dict[str, Any]:
        """Revenue dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns")
            ).first()
            
            by_dealer = self.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.sum(DeliveryReport.dn_amount).label("revenue")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("revenue")).limit(10).all()
            
            dealer_revenue = []
            for r in by_dealer:
                dealer_revenue.append({
                    "dealer": r.dealer or "Unknown",
                    "revenue": float(r.revenue or 0)
                })
            
            return {
                "total_revenue": float(result.total_revenue or 0),
                "total_units": int(result.total_units or 0),
                "total_dns": result.total_dns or 0,
                "top_dealers": dealer_revenue
            }
            
        except Exception as e:
            logger.error(f"Get revenue dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 21: RANKING DASHBOARD
# ==========================================================

    def get_ranking_dashboard(self, limit: int = 10) -> Dict[str, Any]:
        """Dealer ranking - PostgreSQL only."""
        try:
            results = self.db.query(
                DeliveryReport.customer_name.label("dealer"),
                func.sum(DeliveryReport.dn_amount).label("revenue"),
                func.sum(DeliveryReport.dn_qty).label("units"),
                func.count(distinct(DeliveryReport.dn_no)).label("dns"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered")
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(desc("revenue")).limit(limit).all()
            
            ranking = []
            for r in results:
                total_dns = r.dns or 1
                ranking.append({
                    "dealer": r.dealer or "Unknown",
                    "revenue": float(r.revenue or 0),
                    "units": int(r.units or 0),
                    "dns": total_dns,
                    "delivery_rate": KPIEngine.calculate_delivery_rate(r.delivered or 0, total_dns)
                })
            
            return {"ranking": ranking, "total": len(ranking)}
            
        except Exception as e:
            logger.error(f"Get ranking dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 22: AGING DASHBOARD
# ==========================================================

    def get_aging_dashboard(self) -> Dict[str, Any]:
        """Aging dashboard - PostgreSQL only."""
        try:
            result = self.db.query(
                func.count(distinct(case((func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 7, DeliveryReport.dn_no), else_=None))).label("days_0_7"),
                func.count(distinct(case((and_(func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 7, func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 14), DeliveryReport.dn_no), else_=None))).label("days_8_14"),
                func.count(distinct(case((and_(func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 14, func.date_part('day', func.now() - DeliveryReport.dn_create_date) <= 30), DeliveryReport.dn_no), else_=None))).label("days_15_30"),
                func.count(distinct(case((func.date_part('day', func.now() - DeliveryReport.dn_create_date) > 30, DeliveryReport.dn_no), else_=None))).label("days_30_plus")
            ).filter(
                DeliveryReport.dn_create_date.isnot(None),
                DeliveryReport.pending_flag == True
            ).first()
            
            return {
                "days_0_7": result.days_0_7 or 0,
                "days_8_14": result.days_8_14 or 0,
                "days_15_30": result.days_15_30 or 0,
                "days_30_plus": result.days_30_plus or 0,
                "total_pending": (result.days_0_7 or 0) + (result.days_8_14 or 0) + (result.days_15_30 or 0) + (result.days_30_plus or 0)
            }
            
        except Exception as e:
            logger.error(f"Get aging dashboard failed: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 23: ANALYTICS SERVICE CLASS
# ==========================================================

class AnalyticsService:
    """
    Main analytics service - PostgreSQL only.
    Enterprise Production Grade.
    """
    
    def __init__(self, db: Optional[Session] = None):
        self.repo = AnalyticsRepository(db)
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "avg_response_time": 0
        }
        logger.info("✅ AnalyticsService v31.0 initialized - PostgreSQL Only")
    
    def close(self):
        self.repo.close()
    
    # ==========================================================
    # BLOCK 24: SEARCH METHODS
    # ==========================================================
    
    def search_dn(self, query: str, exact: bool = False) -> AnalyticsResponse:
        """Search for DNs - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="DN number is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_dn(query, exact)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"DN {query} not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_dealer(self, query: str, exact: bool = False) -> AnalyticsResponse:
        """Search for dealers - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Dealer name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_dealer(query, exact)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Dealer '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_warehouse(self, query: str) -> AnalyticsResponse:
        """Search for warehouses - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Warehouse name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_warehouse(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Warehouse '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_city(self, query: str) -> AnalyticsResponse:
        """Search for cities - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="City name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_city(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"City '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_product(self, query: str) -> AnalyticsResponse:
        """Search for products - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            
            if not query or not query.strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error="Product name is required",
                    data={"results": [], "total": 0}
                )
            
            results = self.repo.search.search_product(query)
            
            if not results:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(
                    success=False,
                    error=f"Product '{query}' not found",
                    data={"results": [], "total": 0}
                )
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(
                success=True,
                data={"results": results, "total": len(results)}
            )
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 25: VERIFICATION METHODS
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> AnalyticsResponse:
        """Verify DN exists - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_dn_exists(dn_no)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def verify_dealer_exists(self, dealer_name: str) -> AnalyticsResponse:
        """Verify dealer exists - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_dealer_exists(dealer_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 26: ENTITY RESOLUTION
    # ==========================================================
    
    def resolve_dealer(self, dealer_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_dealer(dealer_name)
    
    def resolve_warehouse(self, warehouse_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_warehouse(warehouse_name)
    
    def resolve_city(self, city_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_city(city_name)
    
    def resolve_product(self, product_name: str) -> Optional[str]:
        return self.repo.resolver.resolve_product(product_name)
    
    def resolve_dn(self, dn_no: str) -> Optional[str]:
        return self.repo.resolver.resolve_dn(dn_no)
    
    # ==========================================================
    # BLOCK 27: DASHBOARD METHODS
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get complete 360° dealer dashboard."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer 360 Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            if not hasattr(self.repo, 'get_dealer_360_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer 360 dashboard not available")
            
            result = self.repo.get_dealer_360_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer 360 dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_dn_dashboard(self, dn_no: str) -> AnalyticsResponse:
        """Get DN Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 DN Dashboard request for: {dn_no}")
            
            if not dn_no or not str(dn_no).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="DN number is required")
            
            if not hasattr(self.repo, 'get_dn_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="DN dashboard not available")
            
            result = self.repo.get_dn_dashboard(dn_no.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get Dealer Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            # Try 360 dashboard first
            if hasattr(self.repo, 'get_dealer_360_dashboard'):
                try:
                    result = self.repo.get_dealer_360_dashboard(dealer_name.strip())
                    if "error" not in result:
                        result['_dashboard_type'] = '360'
                        self.metrics["successful_requests"] += 1
                        return AnalyticsResponse(success=True, data=result)
                except Exception as e:
                    logger.warning(f"360 dashboard failed, falling back: {e}")
            
            # Fallback to legacy
            if not hasattr(self.repo, 'get_dealer_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer dashboard not available")
            
            result = self.repo.get_dealer_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        """Get Warehouse Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Warehouse Dashboard request for: {warehouse_name}")
            
            if not warehouse_name or not str(warehouse_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Warehouse name is required")
            
            if not hasattr(self.repo, 'get_warehouse_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Warehouse dashboard not available")
            
            result = self.repo.get_warehouse_dashboard(warehouse_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        """Get City Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 City Dashboard request for: {city_name}")
            
            if not city_name or not str(city_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="City name is required")
            
            if not hasattr(self.repo, 'get_city_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="City dashboard not available")
            
            result = self.repo.get_city_dashboard(city_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_dashboard(self, product_name: str) -> AnalyticsResponse:
        """Get Product Dashboard - PostgreSQL only."""
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Product Dashboard request for: {product_name}")
            
            if not product_name or not str(product_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Product name is required")
            
            if not hasattr(self.repo, 'get_product_dashboard'):
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Product dashboard not available")
            
            result = self.repo.get_product_dashboard(product_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pgi_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_pgi_dashboard'):
                return AnalyticsResponse(success=False, error="PGI dashboard not available")
            result = self.repo.get_pgi_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"PGI dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pod_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_pod_dashboard'):
                return AnalyticsResponse(success=False, error="POD dashboard not available")
            result = self.repo.get_pod_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"POD dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_delivery_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_delivery_dashboard'):
                return AnalyticsResponse(success=False, error="Delivery dashboard not available")
            result = self.repo.get_delivery_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Delivery dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_executive_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_executive_dashboard'):
                return AnalyticsResponse(success=False, error="Executive dashboard not available")
            result = self.repo.get_executive_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Executive dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_control_tower_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_control_tower_dashboard'):
                return AnalyticsResponse(success=False, error="Control tower not available")
            result = self.repo.get_control_tower_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Control tower error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_revenue_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_revenue_dashboard'):
                return AnalyticsResponse(success=False, error="Revenue dashboard not available")
            result = self.repo.get_revenue_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Revenue dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_ranking_dashboard(self, limit: int = 10) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_ranking_dashboard'):
                return AnalyticsResponse(success=False, error="Ranking dashboard not available")
            result = self.repo.get_ranking_dashboard(limit)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Ranking dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_aging_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_aging_dashboard'):
                return AnalyticsResponse(success=False, error="Aging dashboard not available")
            result = self.repo.get_aging_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Aging dashboard error: {e}")
            return AnalyticsResponse(success=False, error=str(e))

# ==========================================================
# BLOCK 28: FACTORY FUNCTION (FIXED v8.0)
# ==========================================================

_analytics_service = None
_analytics_initialization_attempts = 0
_MAX_ANALYTICS_INIT_ATTEMPTS = 3

def get_analytics_service(db: Optional[Session] = None) -> AnalyticsService:
    """
    Get or create AnalyticsService singleton.
    ALWAYS returns AnalyticsService, NEVER None.
    """
    global _analytics_service, _analytics_initialization_attempts
    
    logger.info("=" * 60)
    logger.info("🔍 ANALYTICS SERVICE INITIALIZATION")
    logger.info("=" * 60)
    
    # If already initialized, return it
    if _analytics_service is not None:
        logger.info(f"✅ AnalyticsService already initialized")
        return _analytics_service
    
    # Check max attempts
    if _analytics_initialization_attempts >= _MAX_ANALYTICS_INIT_ATTEMPTS:
        logger.error(f"❌ Max attempts ({_MAX_ANALYTICS_INIT_ATTEMPTS}) reached")
        logger.warning("⚠️ Creating emergency instance")
        try:
            _analytics_service = AnalyticsService(db)
            return _analytics_service
        except Exception as e:
            logger.error(f"❌ Emergency creation failed: {e}")
            _analytics_service = AnalyticsService()
            return _analytics_service
    
    _analytics_initialization_attempts += 1
    logger.info(f"📌 Attempt {_analytics_initialization_attempts}/{_MAX_ANALYTICS_INIT_ATTEMPTS}")
    
    # ==========================================================
    # VALIDATION: Test Database Connection
    # ==========================================================
    try:
        from app.database import SessionLocal
        from app.models import DeliveryReport
        from sqlalchemy import func
        
        test_db = SessionLocal()
        total_records = test_db.query(DeliveryReport).count()
        total_dns = test_db.query(func.count(distinct(DeliveryReport.dn_no))).scalar()
        total_dealers = test_db.query(func.count(distinct(DeliveryReport.customer_name))).scalar()
        test_db.close()
        
        logger.info("✅ PostgreSQL Validation: SUCCESS")
        logger.info(f"   📊 Total Records: {total_records}")
        logger.info(f"   📦 Total DNs: {total_dns}")
        logger.info(f"   🏪 Total Dealers: {total_dealers}")
        
        if total_records == 0:
            logger.warning("⚠️ Database has ZERO records")
            logger.warning("   💡 Insert data into delivery_reports table")
            
    except Exception as e:
        logger.error(f"❌ Database validation failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
    
    # ==========================================================
    # Create Analytics Service - NEVER RETURN NONE
    # ==========================================================
    try:
        logger.info("🔄 Creating AnalyticsService...")
        _analytics_service = AnalyticsService(db)
        logger.info(f"✅ AnalyticsService created successfully")
        logger.info(f"📊 Service type: {type(_analytics_service)}")
        logger.info(f"📊 Service class: {_analytics_service.__class__.__name__}")
        
        # ==========================================================
        # Verify Critical Methods
        # ==========================================================
        critical_methods = [
            "get_dealer_360_dashboard",
            "get_dealer_dashboard",
            "get_dn_dashboard",
            "search_dealer",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard"
        ]
        
        logger.info("🔍 Verifying critical methods:")
        for method in critical_methods:
            if hasattr(_analytics_service, method):
                logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                logger.error(f"   ❌ {method}: MISSING")
        
        logger.info("=" * 60)
        logger.info("✅ AnalyticsService initialized successfully")
        logger.info("   Service is ready to serve REAL PostgreSQL data")
        logger.info("=" * 60)
        
        _analytics_initialization_attempts = 0
        return _analytics_service
        
    except Exception as e:
        logger.error(f"❌ Failed to create AnalyticsService: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # ==========================================================
        # CRITICAL FIX: ALWAYS return an instance, never None
        # ==========================================================
        logger.warning("⚠️ Creating emergency AnalyticsService instance...")
        try:
            _analytics_service = AnalyticsService(db)
            logger.info(f"✅ Emergency AnalyticsService created")
            return _analytics_service
        except Exception as e2:
            logger.error(f"❌ Emergency creation failed: {e2}")
            # ABSOLUTE LAST RESORT
            _analytics_service = AnalyticsService()
            logger.info("✅ Absolute last resort AnalyticsService created")
            return _analytics_service

# ==========================================================
# BLOCK 29: EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'AnalyticsRepository',
    'KPIEngine',
    'DateValidator',
    'SearchEngine',
    'EntityResolver',
    'PostgreSQLHealthEngine',
    'validate_postgresql_health',
    'get_analytics_service',
]

# ==========================================================
# END OF FILE - v31.0 ENTERPRISE PRODUCTION
# ==========================================================
