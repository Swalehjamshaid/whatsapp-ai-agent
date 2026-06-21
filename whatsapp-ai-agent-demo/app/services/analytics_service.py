# ==========================================================
# FILE: app/services/analytics_service.py (v29.0 - PRODUCTION)
# ==========================================================
# PURPOSE: PRIMARY ANALYTICS ENGINE - PostgreSQL Only
# VERSION: 29.0 - Complete Production Analytics Engine
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

# ==========================================================
# BLOCK 1: POSTGRESQL IMPORTS - THE SOURCE OF TRUTH
# ==========================================================

from app.models import DeliveryReport
from app.database import SessionLocal, check_database_connection

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
    """Standard response contract for all analytics endpoints"""
    
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

# ==========================================================
# BLOCK 4: DATABASE HEALTH CHECKER
# ==========================================================

class DatabaseHealthChecker:
    """PostgreSQL health check and monitoring"""
    
    @staticmethod
    def test_connection() -> Dict[str, Any]:
        """Test PostgreSQL connection and return status"""
        try:
            db = SessionLocal()
            total_records = db.query(DeliveryReport).count()
            db.close()
            
            logger.info(f"✅ PostgreSQL connected! Found {total_records} records in delivery_reports")
            
            return {
                "connected": True,
                "total_records": total_records,
                "table_name": "delivery_reports",
                "status": "healthy",
                "message": f"Connected successfully. Total Records: {total_records}"
            }
        except Exception as e:
            logger.error(f"❌ Database connection test failed: {e}")
            return {
                "connected": False,
                "error": str(e),
                "status": "unhealthy",
                "message": f"Connection failed: {str(e)}"
            }
    
    @staticmethod
    def get_table_stats() -> Dict[str, Any]:
        """Get table statistics from PostgreSQL"""
        try:
            db = SessionLocal()
            
            total = db.query(DeliveryReport).count()
            dealers = db.query(func.distinct(DeliveryReport.customer_name)).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).count()
            warehouses = db.query(func.distinct(DeliveryReport.warehouse)).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.warehouse != ''
            ).count()
            cities = db.query(func.distinct(DeliveryReport.ship_to_city)).filter(
                DeliveryReport.ship_to_city.isnot(None),
                DeliveryReport.ship_to_city != ''
            ).count()
            
            db.close()
            
            return {
                "total_records": total,
                "unique_dealers": dealers,
                "unique_warehouses": warehouses,
                "unique_cities": cities,
                "status": "healthy"
            }
        except Exception as e:
            logger.error(f"Table stats error: {e}")
            return {"error": str(e), "status": "unhealthy"}

# ==========================================================
# BLOCK 5: DATE VALIDATION ENGINE
# ==========================================================
# ==========================================================
# BLOCK 5: DATE VALIDATION ENGINE (FIXED)
# ==========================================================

class DateValidator:
    """Validate date sequences for DN, PGI, POD"""
    
    @staticmethod
    def validate_date_sequence(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Tuple[bool, List[str]]:
        """
        Validate chronological order of dates.
        
        Rules:
        - PGI Date >= DN Create Date
        - POD Date >= PGI Date
        - POD Date >= DN Create Date
        """
        issues = []
        is_valid = True
        
        # Convert to datetime if needed
        if create_date and not isinstance(create_date, datetime):
            create_date = datetime.combine(create_date, datetime.min.time())
        if pgi_date and not isinstance(pgi_date, datetime):
            pgi_date = datetime.combine(pgi_date, datetime.min.time())
        if pod_date and not isinstance(pod_date, datetime):
            pod_date = datetime.combine(pod_date, datetime.min.time())
        
        if pgi_date and create_date and pgi_date < create_date:
            issues.append(f"PGI Date ({pgi_date.date()}) occurs before DN Create Date ({create_date.date()})")
            is_valid = False
        
        if pod_date and create_date and pod_date < create_date:
            issues.append(f"POD Date ({pod_date.date()}) occurs before DN Create Date ({create_date.date()})")
            is_valid = False
        
        if pod_date and pgi_date and pod_date < pgi_date:
            issues.append(f"POD Date ({pod_date.date()}) occurs before PGI Date ({pgi_date.date()})")
            is_valid = False
        
        return is_valid, issues
    
    @staticmethod
    def calculate_aging(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        """Calculate aging with validation - FIXED"""
        
        # Convert to datetime if needed
        if create_date and not isinstance(create_date, datetime):
            create_date = datetime.combine(create_date, datetime.min.time())
        if pgi_date and not isinstance(pgi_date, datetime):
            pgi_date = datetime.combine(pgi_date, datetime.min.time())
        if pod_date and not isinstance(pod_date, datetime):
            pod_date = datetime.combine(pod_date, datetime.min.time())
        
        is_valid, issues = DateValidator.validate_date_sequence(create_date, pgi_date, pod_date)
        
        result = {
            "dn_aging": None,
            "pgi_aging": None,
            "pod_aging": None,
            "is_valid": is_valid,
            "issues": issues,
            "status": "valid" if is_valid else "invalid"
        }
        
        if is_valid:
            today = datetime.now().date()
            
            # DN Aging
            if create_date:
                create_date_only = create_date.date() if isinstance(create_date, datetime) else create_date
                if pod_date:
                    pod_date_only = pod_date.date() if isinstance(pod_date, datetime) else pod_date
                    result["dn_aging"] = (pod_date_only - create_date_only).days
                else:
                    result["dn_aging"] = (today - create_date_only).days
            
            # PGI Aging
            if pgi_date and create_date and pgi_date >= create_date:
                pgi_date_only = pgi_date.date() if isinstance(pgi_date, datetime) else pgi_date
                create_date_only = create_date.date() if isinstance(create_date, datetime) else create_date
                result["pgi_aging"] = (pgi_date_only - create_date_only).days
            
            # POD Aging
            if pod_date and pgi_date and pod_date >= pgi_date:
                pod_date_only = pod_date.date() if isinstance(pod_date, datetime) else pod_date
                pgi_date_only = pgi_date.date() if isinstance(pgi_date, datetime) else pgi_date
                result["pod_aging"] = (pod_date_only - pgi_date_only).days
        
        return result
# ==========================================================
# BLOCK 6: KPI ENGINE
# ==========================================================

class KPIEngine:
    """Business KPI calculation engine"""
    
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
# BLOCK 7: SEARCH ENGINE
# ==========================================================

class SearchEngine:
    """Universal PostgreSQL Search Engine"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def search_dn(self, query: str, exact: bool = False) -> List[Dict[str, Any]]:
        """Search for DNs in PostgreSQL"""
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
        """Search for dealers in PostgreSQL"""
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
            
            # If no results and not exact, try token-based
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
        """Search for warehouses in PostgreSQL"""
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
        """Search for cities in PostgreSQL"""
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
        """Search for products in PostgreSQL"""
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
    
    def search_division(self, query: str) -> List[Dict[str, Any]]:
        """Search for divisions in PostgreSQL"""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.division)
            ).filter(
                DeliveryReport.division.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.division.isnot(None),
                DeliveryReport.division != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"division": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Division search error: {e}")
            return []
    
    def search_sales_office(self, query: str) -> List[Dict[str, Any]]:
        """Search for sales offices in PostgreSQL"""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.sales_office)
            ).filter(
                DeliveryReport.sales_office.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.sales_office.isnot(None),
                DeliveryReport.sales_office != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"sales_office": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Sales office search error: {e}")
            return []
    
    def search_sales_manager(self, query: str) -> List[Dict[str, Any]]:
        """Search for sales managers in PostgreSQL"""
        if not query or not query.strip():
            return []
        
        query_clean = query.strip()
        
        try:
            results = self.db.query(
                func.distinct(DeliveryReport.sales_manager)
            ).filter(
                DeliveryReport.sales_manager.ilike(f"%{query_clean}%")
            ).filter(
                DeliveryReport.sales_manager.isnot(None),
                DeliveryReport.sales_manager != ''
            ).limit(SEARCH_LIMIT).all()
            
            return [{"sales_manager": r[0]} for r in results if r[0]]
            
        except Exception as e:
            logger.error(f"Sales manager search error: {e}")
            return []
    
    def verify_dn_exists(self, dn_no: str) -> Dict[str, Any]:
        """Verify if a DN exists in PostgreSQL"""
        query_clean = re.sub(r'[^0-9]', '', str(dn_no).strip())
        if len(query_clean) < 8 or len(query_clean) > 12:
            return {"dn": dn_no, "found": False, "error": "Invalid DN format"}
        
        try:
            result = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == query_clean
            ).first()
            
            if result:
                return {
                    "dn": dn_no,
                    "found": True,
                    "normalized": query_clean,
                    "record": {
                        "dn_no": result.dn_no,
                        "customer_name": result.customer_name,
                        "warehouse": result.warehouse,
                        "ship_to_city": result.ship_to_city,
                        "dn_qty": result.dn_qty,
                        "dn_amount": result.dn_amount
                    }
                }
            
            return {"dn": dn_no, "found": False, "normalized": query_clean}
            
        except Exception as e:
            logger.error(f"DN verification error: {e}")
            return {"dn": dn_no, "found": False, "error": str(e)}
    
    def verify_dealer_exists(self, dealer_name: str) -> bool:
        """Verify if a dealer exists in PostgreSQL"""
        if not dealer_name or not dealer_name.strip():
            return False
        
        try:
            result = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name.strip())
            ).first()
            return result is not None
        except Exception as e:
            logger.error(f"Dealer verification error: {e}")
            return False
    
    def verify_city_exists(self, city_name: str) -> bool:
        """Verify if a city exists in PostgreSQL"""
        if not city_name or not city_name.strip():
            return False
        
        try:
            result = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.ship_to_city) == func.lower(city_name.strip())
            ).first()
            return result is not None
        except Exception as e:
            logger.error(f"City verification error: {e}")
            return False
    
    def verify_warehouse_exists(self, warehouse_name: str) -> bool:
        """Verify if a warehouse exists in PostgreSQL"""
        if not warehouse_name or not warehouse_name.strip():
            return False
        
        try:
            result = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.warehouse) == func.lower(warehouse_name.strip())
            ).first()
            return result is not None
        except Exception as e:
            logger.error(f"Warehouse verification error: {e}")
            return False
    
    def verify_product_exists(self, product_name: str) -> bool:
        """Verify if a product exists in PostgreSQL"""
        if not product_name or not product_name.strip():
            return False
        
        try:
            result = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_model) == func.lower(product_name.strip())
            ).first()
            return result is not None
        except Exception as e:
            logger.error(f"Product verification error: {e}")
            return False

# ==========================================================
# BLOCK 8: ENTITY RESOLVER
# ==========================================================

class EntityResolver:
    """Entity resolution engine using PostgreSQL"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        """Resolve dealer name using PostgreSQL only"""
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        start_time = time.time()
        
        try:
            # 1. Exact match
            result = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if result:
                logger.info(f"Dealer resolved (exact): {result[0]} in {time.time() - start_time:.3f}s")
                return result[0]
            
            # 2. ILIKE match
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if result:
                logger.info(f"Dealer resolved (ILIKE): {result[0]} in {time.time() - start_time:.3f}s")
                return result[0]
            
            # 3. Token-based matching
            tokens = dealer_input.split()
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        logger.info(f"Dealer resolved (token): {result[0]} in {time.time() - start_time:.3f}s")
                        return result[0]
            
            # 4. Fuzzy matching
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
                dealer_lower = dealer[0].lower()
                if dealer_input_lower in dealer_lower or dealer_lower in dealer_input_lower:
                    score = len(set(dealer_input_lower) & set(dealer_lower)) / max(len(dealer_input_lower), len(dealer_lower))
                    if score > best_score and score > 0.6:
                        best_score = score
                        best_match = dealer[0]
            
            if best_match:
                logger.info(f"Dealer resolved (fuzzy): {best_match} in {time.time() - start_time:.3f}s")
                return best_match
            
            logger.warning(f"Dealer not found: {dealer_input}")
            return None
            
        except Exception as e:
            logger.error(f"Dealer resolution error: {e}")
            return None
    
    def resolve_warehouse(self, warehouse_input: str) -> Optional[str]:
        """Resolve warehouse name using PostgreSQL only"""
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
        """Resolve city name using PostgreSQL only"""
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
        """Resolve product name using PostgreSQL only"""
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
        """Resolve DN number using PostgreSQL only"""
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
# BLOCK 9: ANALYTICS REPOSITORY
# ==========================================================

class AnalyticsRepository:
    """PostgreSQL-driven analytics repository"""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.resolver = EntityResolver(self.db)
        self.search = SearchEngine(self.db)
        logger.info("✅ AnalyticsRepository initialized with PostgreSQL")
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    # ==========================================================
    # BLOCK 10: DN DASHBOARD
    # ==========================================================
    
    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """Complete DN dashboard from PostgreSQL"""
        try:
            normalized = self.resolver.resolve_dn(dn_no)
            if not normalized:
                return {"error": f"DN {dn_no} not found"}
            
            record = self.db.query(DeliveryReport).filter(
                cast(DeliveryReport.dn_no, String) == normalized
            ).first()
            
            if not record:
                return {"error": f"DN {dn_no} not found"}
            
            aging_result = DateValidator.calculate_aging(
                record.dn_create_date,
                record.good_issue_date,
                record.pod_date
            )
            
            return {
                "dn_number": record.dn_no,
                "customer_name": record.customer_name,
                "dealer_code": record.dealer_code or "",
                "customer_code": record.customer_code or "",
                "warehouse": record.warehouse,
                "ship_to_city": record.ship_to_city,
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
                "delivery_status": record.delivery_status,
                "pgi_status": record.pgi_status,
                "pod_status": record.pod_status,
                "pending_flag": record.pending_flag,
                "aging": aging_result,
                "dn_aging": aging_result.get("dn_aging"),
                "pgi_aging": aging_result.get("pgi_aging"),
                "pod_aging": aging_result.get("pod_aging"),
                "aging_is_valid": aging_result.get("is_valid", True),
                "aging_issues": aging_result.get("issues", [])
            }
            
        except Exception as e:
            logger.error(f"Get DN dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # BLOCK 11: DEALER DASHBOARD
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Complete dealer dashboard from PostgreSQL"""
        try:
            resolved = self.resolver.resolve_dealer(dealer_name)
            if not resolved:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            result = self.db.query(
                DeliveryReport.customer_name.label("dealer_name"),
                func.max(DeliveryReport.dealer_code).label("dealer_code"),
                func.max(DeliveryReport.customer_code).label("customer_code"),
                func.max(DeliveryReport.division).label("division"),
                func.max(DeliveryReport.warehouse).label("warehouse"),
                func.max(DeliveryReport.ship_to_city).label("city"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.coalesce(func.sum(DeliveryReport.dn_qty), 0).label("total_units"),
                func.coalesce(func.sum(DeliveryReport.dn_amount), 0).label("total_revenue"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("transit_dns"),
                func.count(distinct(case((DeliveryReport.pod_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("pod_completed_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns"),
                func.count(distinct(case((DeliveryReport.good_issue_date.is_(None), DeliveryReport.dn_no), else_=None))).label("pending_pgi_dns"),
                func.count(distinct(DeliveryReport.customer_model)).label("product_count"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("city_count")
            ).filter(
                DeliveryReport.customer_name == resolved
            ).group_by(
                DeliveryReport.customer_name
            ).first()
            
            if not result or result.total_dns == 0:
                return {"error": f"No data found for {resolved}"}
            
            total_dns = result.total_dns or 1
            delivered_dns = result.delivered_dns or 0
            transit_dns = result.transit_dns or 0
            pod_completed = result.pod_completed_dns or 0
            
            delivery_rate = KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            pgi_rate = KPIEngine.calculate_pgi_rate(delivered_dns, transit_dns, total_dns)
            pod_rate = KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            
            risk_level, risk_score = KPIEngine.calculate_risk_level(
                delivery_rate,
                pod_rate,
                0
            )
            
            return {
                "dealer_name": resolved,
                "dealer_code": result.dealer_code or "",
                "customer_code": result.customer_code or "",
                "division": result.division or "",
                "warehouse": result.warehouse or "",
                "city": result.city or "",
                "total_dns": total_dns,
                "total_units": int(result.total_units or 0),
                "total_revenue": float(result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "transit_dns": transit_dns,
                "pod_completed_dns": pod_completed,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "pending_pgi_dns": result.pending_pgi_dns or 0,
                "product_count": result.product_count or 0,
                "city_count": result.city_count or 0,
                "delivery_rate": delivery_rate,
                "pgi_rate": pgi_rate,
                "pod_rate": pod_rate,
                "health_score": KPIEngine.calculate_health_score({
                    "delivery_rate": delivery_rate,
                    "pod_rate": pod_rate,
                    "avg_aging": 0,
                    "revenue": float(result.total_revenue or 0)
                }),
                "risk_level": risk_level,
                "risk_score": risk_score
            }
            
        except Exception as e:
            logger.error(f"Get dealer dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # BLOCK 12: WAREHOUSE DASHBOARD
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Complete warehouse dashboard from PostgreSQL"""
        try:
            resolved = self.resolver.resolve_warehouse(warehouse_name)
            if not resolved:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            result = self.db.query(
                DeliveryReport.warehouse.label("warehouse"),
                func.max(DeliveryReport.warehouse_code).label("warehouse_code"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.ship_to_city)).label("cities_served"),
                func.count(distinct(DeliveryReport.customer_model)).label("product_count"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((and_(DeliveryReport.delivery_status == 'Completed', DeliveryReport.pod_status != 'Completed'), DeliveryReport.dn_no), else_=None))).label("pending_pod_dns")
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
                "product_count": result.product_count or 0,
                "delivered_dns": delivered_dns,
                "pending_dns": result.pending_dns or 0,
                "pending_pod_dns": result.pending_pod_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get warehouse dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # BLOCK 13: CITY DASHBOARD
    # ==========================================================
    
    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Complete city dashboard from PostgreSQL"""
        try:
            resolved = self.resolver.resolve_city(city_name)
            if not resolved:
                return {"error": f"City '{city_name}' not found"}
            
            result = self.db.query(
                DeliveryReport.ship_to_city.label("city"),
                func.count(distinct(DeliveryReport.dn_no)).label("total_dns"),
                func.sum(DeliveryReport.dn_qty).label("total_units"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(distinct(DeliveryReport.customer_name)).label("total_dealers"),
                func.count(distinct(DeliveryReport.warehouse)).label("total_warehouses"),
                func.count(distinct(case((DeliveryReport.delivery_status == 'Completed', DeliveryReport.dn_no), else_=None))).label("delivered_dns"),
                func.count(distinct(case((DeliveryReport.pending_flag == True, DeliveryReport.dn_no), else_=None))).label("pending_dns"),
                func.count(distinct(case((DeliveryReport.pod_status != 'Completed', DeliveryReport.dn_no), else_=None))).label("pending_pod_dns")
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
                "pending_pod_dns": result.pending_pod_dns or 0,
                "delivery_rate": KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            }
            
        except Exception as e:
            logger.error(f"Get city dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # BLOCK 14: PRODUCT DASHBOARD
    # ==========================================================
    
    def get_product_dashboard(self, product_name: str) -> Dict[str, Any]:
        """Complete product dashboard from PostgreSQL"""
        try:
            resolved = self.resolver.resolve_product(product_name)
            if not resolved:
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
        """PGI dashboard from PostgreSQL"""
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
        """POD dashboard from PostgreSQL"""
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
        """Delivery dashboard from PostgreSQL"""
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
        """Executive dashboard from PostgreSQL"""
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
        """Control tower dashboard from PostgreSQL"""
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
        """Revenue dashboard from PostgreSQL"""
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
        """Dealer ranking from PostgreSQL"""
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
        """Aging dashboard from PostgreSQL"""
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
    # BLOCK 23: FOLLOW-UP SUPPORT
    # ==========================================================
    
    def get_followup_data(self, context: Dict[str, Any], question: str) -> Dict[str, Any]:
        """Handle follow-up questions using context"""
        try:
            last_entity = context.get("last_entity")
            last_intent = context.get("last_intent")
            
            if not last_entity:
                return {"error": "No previous entity found"}
            
            question_lower = question.lower()
            
            # Revenue follow-up
            if any(word in question_lower for word in ["revenue", "amount", "value", "worth"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
                elif last_intent == "city_dashboard":
                    return self.get_city_dashboard(last_entity)
                elif last_intent == "product_dashboard":
                    return self.get_product_dashboard(last_entity)
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
            
            # POD follow-up
            if any(word in question_lower for word in ["pod", "proof of delivery"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            # PGI follow-up
            if any(word in question_lower for word in ["pgi", "goods issue"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            # Units follow-up
            if any(word in question_lower for word in ["units", "quantity", "qty", "pieces"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "product_dashboard":
                    return self.get_product_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            # DN count follow-up
            if any(word in question_lower for word in ["dn", "delivery note", "order"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
                elif last_intent == "city_dashboard":
                    return self.get_city_dashboard(last_entity)
            
            # Products follow-up
            if any(word in question_lower for word in ["products", "product", "models"]):
                if last_intent == "dealer_dashboard":
                    return {"message": "Product list for this dealer is available"}
                elif last_intent == "city_dashboard":
                    return {"message": "Product list for this city is available"}
            
            # Ranking follow-up
            if any(word in question_lower for word in ["rank", "ranking", "top", "best"]):
                if last_intent == "dealer_dashboard":
                    return self.get_ranking_dashboard(10)
            
            # Aging follow-up
            if any(word in question_lower for word in ["aging", "old", "delay", "overdue"]):
                if last_intent == "dealer_dashboard":
                    return self.get_aging_dashboard()
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
            
            # Pending follow-up
            if any(word in question_lower for word in ["pending", "not completed", "waiting"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            # Performance follow-up
            if any(word in question_lower for word in ["performance", "status", "health"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
                elif last_intent == "city_dashboard":
                    return self.get_city_dashboard(last_entity)
            
            return {"error": "Follow-up question not recognized"}
            
        except Exception as e:
            logger.error(f"Follow-up data error: {e}")
            return {"error": str(e)}

# ==========================================================
# BLOCK 24: MAIN ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    """Main analytics service - PostgreSQL only"""
    
    def __init__(self, db: Optional[Session] = None):
        self.repo = AnalyticsRepository(db)
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0
        }
        logger.info("✅ AnalyticsService v29.0 initialized - PostgreSQL Only")
    
    def close(self):
        self.repo.close()
    
    # ==========================================================
    # BLOCK 25: SEARCH METHODS
    # ==========================================================
    
    def search_dn(self, query: str, exact: bool = False) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_dn(query, exact)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"DN search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_dealer(self, query: str, exact: bool = False) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_dealer(query, exact)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_warehouse(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_warehouse(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_city(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_city(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_product(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_product(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_division(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_division(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Division search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_sales_office(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_sales_office(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Sales office search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def search_sales_manager(self, query: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.search_sales_manager(query)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"results": result, "total": len(result)})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Sales manager search failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 26: VERIFICATION METHODS
    # ==========================================================
    
    def verify_dn_exists(self, dn_no: str) -> AnalyticsResponse:
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
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_dealer_exists(dealer_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Dealer verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def verify_city_exists(self, city_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_city_exists(city_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"City verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def verify_warehouse_exists(self, warehouse_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_warehouse_exists(warehouse_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Warehouse verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def verify_product_exists(self, product_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.search.verify_product_exists(product_name)
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data={"exists": result})
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Product verification failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 27: ENTITY RESOLUTION
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
    # BLOCK 28: DASHBOARD METHODS
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_dealer_dashboard(dealer_name)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get dealer dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_warehouse_dashboard(warehouse_name)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get warehouse dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_city_dashboard(city_name)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get city dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_product_dashboard(self, product_name: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_product_dashboard(product_name)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get product dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_dn_dashboard(self, dn_no: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_dn_dashboard(dn_no)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get DN dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pgi_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_pgi_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get PGI dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_pod_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_pod_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get POD dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_delivery_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_delivery_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get delivery dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_executive_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_executive_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get executive dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_control_tower_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_control_tower_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get control tower dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_revenue_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_revenue_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get revenue dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_ranking_dashboard(self, limit: int = 10) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_ranking_dashboard(limit)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get ranking dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def get_aging_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_aging_dashboard()
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get aging dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # BLOCK 29: FOLLOW-UP SUPPORT
    # ==========================================================
    
    def get_followup_data(self, context: Dict[str, Any], question: str) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            result = self.repo.get_followup_data(context, question)
            if "error" in result:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=result["error"])
            self.metrics["successful_requests"] += 1
            return AnalyticsResponse(success=True, data=result)
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"Get follow-up data failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))

# ==========================================================
# BLOCK 30: FACTORY FUNCTION
# ==========================================================

_analytics_service = None

def get_analytics_service(db: Optional[Session] = None) -> AnalyticsService:
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService(db)
    return _analytics_service

# ==========================================================
# BLOCK 31: EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'AnalyticsRepository',
    'KPIEngine',
    'DateValidator',
    'SearchEngine',
    'EntityResolver',
    'DatabaseHealthChecker',
    'get_analytics_service',
    'test_database_connection'
]

# ==========================================================
# END OF FILE - v29.0 PRODUCTION READY
# ==========================================================
