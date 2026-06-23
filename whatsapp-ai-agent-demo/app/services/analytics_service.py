# ==========================================================
# FILE: app/services/analytics_service.py (v30.0 - PRODUCTION)
# ==========================================================
# PURPOSE: PRIMARY ANALYTICS ENGINE - PostgreSQL Only
# VERSION: 30.0 - Complete Production Analytics Engine
# ==========================================================
# ==========================================================
# ADD THIS IMPORT AT THE TOP OF THE FILE
# ==========================================================

from app.services.dealer_analytics_service import Dealer360Dashboard
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
# BLOCK 5: DATE VALIDATION ENGINE (PRODUCTION FIX v38.0)
# ==========================================================

from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger

class DateValidator:
    """
    PRODUCTION DATE VALIDATION ENGINE v38.0
    
    CRITICAL BUSINESS RULE:
    =======================
    ALL dates are interpreted as YEAR-DATE-MONTH (YYYY-DD-MM)
    
    Position 1: Year (YYYY)
    Position 2: Date/Day (DD) 
    Position 3: Month (MM)
    
    INTELLIGENT PARSING LOGIC:
    ===========================
    1. Try YYYY-DD-MM first (business format)
    2. If month > 12, auto-swap to YYYY-MM-DD (ISO format)
    3. This handles both formats automatically
    """
    
    @staticmethod
    @lru_cache(maxsize=1024)
    def parse_business_date(raw_value: Any) -> Optional[datetime]:
        """Parse date using INTELLIGENT format detection."""
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
                logger.warning(f"⚠️ Invalid date format (expected 3 parts): {raw_str}")
                return None
            
            year = int(parts[0])
            pos2 = int(parts[1])
            pos3 = int(parts[2])
            
            if not (1900 <= year <= 2100):
                logger.warning(f"⚠️ Year out of range: {year}")
                return None
            
            # Try YYYY-DD-MM first
            if 1 <= pos3 <= 12 and 1 <= pos2 <= 31:
                try:
                    result = datetime(year, pos3, pos2)
                    logger.debug(f"✅ Parsed as YYYY-DD-MM: {raw_str} → {result.strftime('%d-%b-%Y')}")
                    return result
                except ValueError:
                    pass
            
            # Try YYYY-MM-DD (auto-swap)
            if 1 <= pos2 <= 12 and 1 <= pos3 <= 31:
                try:
                    result = datetime(year, pos2, pos3)
                    logger.debug(f"✅ Parsed as YYYY-MM-DD (auto-swapped): {raw_str} → {result.strftime('%d-%b-%Y')}")
                    return result
                except ValueError:
                    pass
            
            logger.error(f"❌ Date parsing error: {raw_str} - All formats failed")
            return None
            
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Date parsing error: {raw_str} - {e}")
            return None
    
    @staticmethod
    def interpret_business_date(raw_date: Optional[datetime]) -> Optional[datetime]:
        """Alias for parse_business_date."""
        return DateValidator.parse_business_date(raw_date)
    
    @staticmethod
    def validate_date_sequence(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Tuple[bool, List[str], str]:
        """Validate chronological order with all 5 scenarios."""
        issues = []
        
        create_date = DateValidator.parse_business_date(create_date)
        pgi_date = DateValidator.parse_business_date(pgi_date)
        pod_date = DateValidator.parse_business_date(pod_date)
        
        create_exists = create_date is not None
        pgi_exists = pgi_date is not None
        pod_exists = pod_date is not None
        
        # SCENARIO 4: POD Exists, PGI Missing
        if pod_exists and not pgi_exists:
            issues.append("⚠️ POD Received without PGI Completion")
            logger.warning(f"SCENARIO_4: POD exists but PGI missing")
            return False, issues, "SCENARIO_4_POD_WITHOUT_PGI"
        
        # SCENARIO 5: POD < PGI
        if pod_exists and pgi_exists and pod_date < pgi_date:
            issues.append(f"⚠️ POD ({pod_date.strftime('%d-%b-%Y')}) occurs before PGI ({pgi_date.strftime('%d-%b-%Y')})")
            logger.warning(f"SCENARIO_5: POD before PGI")
            return False, issues, "SCENARIO_5_POD_BEFORE_PGI"
        
        # VALID SCENARIOS 1, 2, 3
        if create_exists:
            if pgi_exists and pgi_date < create_date:
                issues.append(f"⚠️ PGI ({pgi_date.strftime('%d-%b-%Y')}) before Create ({create_date.strftime('%d-%b-%Y')})")
                return False, issues, "INVALID_PGI_BEFORE_CREATE"
            
            if pod_exists and pod_date < create_date:
                issues.append(f"⚠️ POD ({pod_date.strftime('%d-%b-%Y')}) before Create ({create_date.strftime('%d-%b-%Y')})")
                return False, issues, "INVALID_POD_BEFORE_CREATE"
        
        if create_exists and pgi_exists and pod_exists:
            scenario = "SCENARIO_1_COMPLETE"
        elif create_exists and pgi_exists and not pod_exists:
            scenario = "SCENARIO_2_POD_PENDING"
        elif create_exists and not pgi_exists and not pod_exists:
            scenario = "SCENARIO_3_PGI_PENDING"
        else:
            issues.append("⚠️ Invalid combination: Missing create date")
            return False, issues, "UNKNOWN"
        
        return True, issues, scenario
    
    @staticmethod
    def calculate_aging(
        create_date: Optional[datetime],
        pgi_date: Optional[datetime],
        pod_date: Optional[datetime]
    ) -> Dict[str, Any]:
        """Calculate aging - ALWAYS calculate values."""
        
        create_date = DateValidator.parse_business_date(create_date)
        pgi_date = DateValidator.parse_business_date(pgi_date)
        pod_date = DateValidator.parse_business_date(pod_date)
        
        create_exists = create_date is not None
        pgi_exists = pgi_date is not None
        pod_exists = pod_date is not None
        
        today = datetime.now().date()
        
        result = {
            "delivery_aging": None,
            "pod_aging": None,
            "total_cycle": None,
            "delivery_aging_text": "N/A",
            "pod_aging_text": "N/A",
            "total_cycle_text": "N/A",
            "is_valid": True,
            "issues": [],
            "status": "valid",
            "pgi_completed": False,
            "pod_received": False,
            "delivery_completed": False,
            "scenario": "UNKNOWN",
        }
        
        # Calculate Delivery Aging
        if create_exists and pgi_exists:
            delivery_aging = max(0, (pgi_date.date() - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = DateValidator._format_aging(delivery_aging)
            result["pgi_completed"] = True
        elif create_exists and not pgi_exists:
            delivery_aging = max(0, (today - create_date.date()).days)
            result["delivery_aging"] = delivery_aging
            result["delivery_aging_text"] = f"{DateValidator._format_aging(delivery_aging)} (Pending PGI)"
        
        # Calculate POD Aging
        if pgi_exists and pod_exists:
            pod_aging = max(0, (pod_date.date() - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = DateValidator._format_aging(pod_aging)
            result["pod_received"] = True
        elif pgi_exists and not pod_exists:
            pod_aging = max(0, (today - pgi_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = f"{DateValidator._format_aging(pod_aging)} (Pending POD)"
        elif create_exists and pod_exists and not pgi_exists:
            pod_aging = max(0, (pod_date.date() - create_date.date()).days)
            result["pod_aging"] = pod_aging
            result["pod_aging_text"] = f"{DateValidator._format_aging(pod_aging)} (No PGI)"
            result["pod_received"] = True
        
        # Calculate Total Cycle
        if create_exists and pod_exists:
            total_cycle = max(0, (pod_date.date() - create_date.date()).days)
            result["total_cycle"] = total_cycle
            result["total_cycle_text"] = DateValidator._format_aging(total_cycle)
            result["delivery_completed"] = True
        elif create_exists and not pod_exists:
            if pgi_exists:
                result["total_cycle_text"] = "In Progress (POD Pending)"
            else:
                result["total_cycle_text"] = "In Progress (PGI Pending)"
        
        # Validate Scenarios
        is_valid, issues, scenario = DateValidator.validate_date_sequence(
            create_date, pgi_date, pod_date
        )
        
        result["is_valid"] = is_valid
        result["issues"] = issues
        result["scenario"] = scenario
        result["status"] = "valid" if is_valid else "invalid"
        
        if scenario == "SCENARIO_5_POD_BEFORE_PGI":
            issues.append(f"⚠️ POD occurs before PGI - Data quality issue")
        
        logger.info(f"📊 Aging: {scenario} | Delivery: {result['delivery_aging_text']} | POD: {result['pod_aging_text']} | Total: {result['total_cycle_text']}")
        
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
    
    @staticmethod
    def validate_dashboard_compatibility(result: Dict[str, Any]) -> bool:
        """Validate result is compatible with all dashboards."""
        required_fields = [
            "delivery_aging", "pod_aging", "total_cycle",
            "delivery_aging_text", "pod_aging_text", "total_cycle_text",
            "is_valid", "status", "pgi_completed", "pod_received",
            "delivery_completed", "scenario"
        ]
        
        for field in required_fields:
            if field not in result:
                logger.error(f"❌ Missing required field: {field}")
                return False
        
        if result["pod_received"]:
            if "(Pending)" in result["pod_aging_text"]:
                logger.error(f"❌ Contradiction: POD received but aging shows Pending")
                return False
            if result["total_cycle_text"] == "In Progress (POD Pending)":
                logger.error(f"❌ Contradiction: POD received but Total Cycle shows Pending")
                return False
            if result["scenario"] not in ["SCENARIO_1_COMPLETE", "SCENARIO_4_POD_WITHOUT_PGI"]:
                logger.error(f"❌ Contradiction: POD received but scenario is {result['scenario']}")
                return False
        
        return True

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
# BLOCK 8: ENTITY RESOLVER (FIXED - v3.0)
# ==========================================================

class EntityResolver:
    """Entity resolution engine using PostgreSQL"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def resolve_dealer(self, dealer_input: str) -> Optional[str]:
        """Resolve dealer name using PostgreSQL with 8 strategies."""
        if not dealer_input or not dealer_input.strip():
            return None
        
        dealer_input = dealer_input.strip()
        start_time = time.time()
        
        try:
            # STRATEGY 1: Exact match
            result = self.db.query(DeliveryReport.customer_name).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_input)
            ).first()
            if result:
                logger.info(f"✅ Dealer resolved (exact): {result[0]} in {time.time() - start_time:.3f}s")
                return result[0]
            
            # STRATEGY 2: ILIKE match
            result = self.db.query(DeliveryReport.customer_name).filter(
                DeliveryReport.customer_name.ilike(f"%{dealer_input}%")
            ).first()
            if result:
                logger.info(f"✅ Dealer resolved (ILIKE): {result[0]} in {time.time() - start_time:.3f}s")
                return result[0]
            
            # STRATEGY 3: Token-based matching
            tokens = dealer_input.split()
            for token in tokens:
                if len(token) > 2:
                    result = self.db.query(DeliveryReport.customer_name).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).first()
                    if result:
                        logger.info(f"✅ Dealer resolved (token '{token}'): {result[0]} in {time.time() - start_time:.3f}s")
                        return result[0]
            
            # STRATEGY 4: Fuzzy matching with LOWER threshold (0.3)
            dealers = self.db.query(
                func.distinct(DeliveryReport.customer_name)
            ).filter(
                DeliveryReport.customer_name.isnot(None),
                DeliveryReport.customer_name != ''
            ).limit(1000).all()
            
            best_match = None
            best_score = 0
            dealer_input_lower = dealer_input.lower()
            dealer_input_tokens = set(dealer_input_lower.split())
            
            for dealer in dealers:
                if not dealer[0]:
                    continue
                dealer_name = dealer[0]
                dealer_lower = dealer_name.lower()
                dealer_tokens = set(dealer_lower.split())
                
                scores = []
                
                # Token overlap score
                if dealer_input_tokens and dealer_tokens:
                    overlap = len(dealer_input_tokens & dealer_tokens)
                    token_score = overlap / max(len(dealer_input_tokens), len(dealer_tokens))
                    scores.append(token_score)
                
                # Character overlap score
                char_overlap = len(set(dealer_input_lower) & set(dealer_lower))
                char_score = char_overlap / max(len(dealer_input_lower), len(dealer_lower))
                scores.append(char_score)
                
                # Contains score
                if dealer_input_lower in dealer_lower or dealer_lower in dealer_input_lower:
                    scores.append(0.8)
                
                # Word match score
                for token in dealer_input_tokens:
                    if len(token) > 2 and token in dealer_lower:
                        scores.append(0.7)
                
                if scores:
                    score = max(scores)
                else:
                    score = 0
                
                if score > best_score and score > 0.3:
                    best_score = score
                    best_match = dealer_name
            
            if best_match:
                logger.info(f"✅ Dealer resolved (fuzzy, score={best_score:.2f}): {best_match} in {time.time() - start_time:.3f}s")
                return best_match
            
            # STRATEGY 5: Partial word matching
            for token in tokens:
                if len(token) > 2:
                    results = self.db.query(
                        func.distinct(DeliveryReport.customer_name)
                    ).filter(
                        or_(
                            DeliveryReport.customer_name.ilike(f"% {token} %"),
                            DeliveryReport.customer_name.ilike(f"{token} %"),
                            DeliveryReport.customer_name.ilike(f"% {token}")
                        )
                    ).limit(10).all()
                    
                    if results:
                        logger.info(f"✅ Dealer resolved (partial word '{token}'): {results[0][0]} in {time.time() - start_time:.3f}s")
                        return results[0][0]
            
            # STRATEGY 6: Remove common words
            common_words = ['electronics', 'trading', 'company', 'enterprises', 'store', 'shop', 'sons', 'brothers', 'ltd', 'pvt', 'limited', 'and']
            cleaned_input = dealer_input.lower()
            for word in common_words:
                cleaned_input = cleaned_input.replace(word, '').strip()
            
            if cleaned_input and len(cleaned_input) > 2:
                result = self.db.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.ilike(f"%{cleaned_input}%")
                ).first()
                if result:
                    logger.info(f"✅ Dealer resolved (cleaned '{cleaned_input}'): {result[0]} in {time.time() - start_time:.3f}s")
                    return result[0]
            
            # STRATEGY 7: First word only
            first_word = tokens[0] if tokens else ""
            if len(first_word) > 2:
                result = self.db.query(DeliveryReport.customer_name).filter(
                    DeliveryReport.customer_name.ilike(f"%{first_word}%")
                ).first()
                if result:
                    logger.info(f"✅ Dealer resolved (first word '{first_word}'): {result[0]} in {time.time() - start_time:.3f}s")
                    return result[0]
            
            # STRATEGY 8: Substring matching
            for token in tokens:
                if len(token) > 2:
                    results = self.db.query(
                        func.distinct(DeliveryReport.customer_name)
                    ).filter(
                        DeliveryReport.customer_name.ilike(f"%{token}%")
                    ).limit(10).all()
                    
                    if results:
                        logger.info(f"✅ Dealer resolved (substring '{token}'): {results[0][0]} in {time.time() - start_time:.3f}s")
                        return results[0][0]
            
            logger.warning(f"❌ Dealer not found: '{dealer_input}' after all strategies")
            return None
            
        except Exception as e:
            logger.error(f"❌ Dealer resolution error: {e}")
            import traceback
            logger.error(traceback.format_exc())
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
# BLOCK 9: ANALYTICS REPOSITORY (FIXED WITH VALIDATION)

# ==========================================================
# BLOCK 9: ANALYTICS REPOSITORY (FIXED)
# ==========================================================

class AnalyticsRepository:
    """PostgreSQL-driven analytics repository"""
    
    def __init__(self, db: Optional[Session] = None):
        self.db = db or SessionLocal()
        self._owned_db = db is None
        self.resolver = EntityResolver(self.db)
        self.search = SearchEngine(self.db)
        
        # ==========================================================
        # ✅ FIXED: Initialize Dealer 360 Dashboard with error handling
        # ==========================================================
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
            import traceback
            logger.warning(traceback.format_exc())
            self._dealer_360 = None
        
        # ==========================================================
        # ✅ FIXED: STARTUP VALIDATION - Check but DON'T crash
        # ==========================================================
        required_methods = [
            "get_dealer_dashboard",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard",
            "get_pgi_dashboard",
            "get_pod_dashboard",
            "get_delivery_dashboard",
            "get_executive_dashboard",
            "get_control_tower_dashboard",
            "get_revenue_dashboard",
            "get_ranking_dashboard",
            "get_aging_dashboard",
            "get_dn_dashboard"
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(self, method):
                missing_methods.append(method)
                logger.error(f"❌ Missing method: {method}")
        
        if missing_methods:
            logger.error(f"❌ Missing {len(missing_methods)} required methods: {missing_methods}")
            logger.warning("⚠️ Some methods are missing - check indentation! Will continue anyway.")
        else:
            logger.info("✅ AnalyticsRepository initialized with all required methods")
            logger.info("   - get_dealer_dashboard: AVAILABLE")
            logger.info("   - get_warehouse_dashboard: AVAILABLE")
            logger.info("   - get_city_dashboard: AVAILABLE")
            logger.info("   - get_product_dashboard: AVAILABLE")
            logger.info("   - get_pgi_dashboard: AVAILABLE")
            logger.info("   - get_pod_dashboard: AVAILABLE")
            logger.info("   - get_delivery_dashboard: AVAILABLE")
            logger.info("   - get_dn_dashboard: AVAILABLE")
            logger.info("   - get_executive_dashboard: AVAILABLE")
            logger.info("   - get_control_tower_dashboard: AVAILABLE")
            logger.info("   - get_revenue_dashboard: AVAILABLE")
            logger.info("   - get_ranking_dashboard: AVAILABLE")
            logger.info("   - get_aging_dashboard: AVAILABLE")
    
    def close(self):
        if self._owned_db and self.db:
            self.db.close()
    
    # ==========================================================
    # ✅ ADD THIS METHOD
    # ==========================================================
    def get_dealer_360_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete 360° dealer dashboard."""
        if self._dealer_360 is None:
            return {"error": "Dealer 360 dashboard service not available"}
        return self._dealer_360.get_dashboard(dealer_name)



# BLOCK 10: DN DASHBOARD (FIXED - v3.0)
# ==========================================================

    def get_dn_dashboard(self, dn_no: str) -> Dict[str, Any]:
        """
        Complete DN dashboard with production logging and validation.
        BLOCK 10 - FIXED v3.0
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"📄 Processing DN: '{dn_no}'")
            
            # ==========================================================
            # STEP 1: Resolve DN
            # ==========================================================
            normalized = self.resolver.resolve_dn(dn_no)
            if not normalized:
                logger.error(f"❌ DN {dn_no} not found in database")
                return {"error": f"DN {dn_no} not found"}
            
            logger.info(f"✅ DN resolved: '{normalized}'")
            
            # ==========================================================
            # STEP 2: Query the record
            # ==========================================================
            try:
                record = self.db.query(DeliveryReport).filter(
                    cast(DeliveryReport.dn_no, String) == normalized
                ).first()
            except Exception as e:
                logger.error(f"❌ Database query failed for DN {normalized}: {e}")
                return {"error": f"Database error: {str(e)}"}
            
            if not record:
                logger.error(f"❌ DN {normalized} not found in database")
                return {"error": f"DN {dn_no} not found"}
            
            logger.info(f"✅ DN record found for: '{normalized}'")
            
            # ==========================================================
            # STEP 3: Calculate aging with error handling
            # ==========================================================
            try:
                aging_result = DateValidator.calculate_aging(
                    record.dn_create_date,
                    record.good_issue_date,
                    record.pod_date
                )
                logger.info(f"✅ Aging calculated for DN {normalized}:")
                logger.info(f"   Scenario: {aging_result.get('scenario')}")
                logger.info(f"   Delivery Aging: {aging_result.get('delivery_aging_text')}")
                logger.info(f"   POD Aging: {aging_result.get('pod_aging_text')}")
                logger.info(f"   Total Cycle: {aging_result.get('total_cycle_text')}")
            except Exception as e:
                logger.error(f"❌ Date calculation failed for DN {normalized}: {e}")
                import traceback
                logger.error(traceback.format_exc())
                aging_result = {
                    "delivery_aging": None,
                    "pod_aging": None,
                    "total_cycle": None,
                    "delivery_aging_text": "Error",
                    "pod_aging_text": "Error",
                    "total_cycle_text": "Error",
                    "is_valid": False,
                    "issues": [f"Date calculation error: {str(e)}"],
                    "scenario": "ERROR",
                    "pod_received": False,
                    "pgi_completed": False,
                    "delivery_completed": False
                }
            
            # ==========================================================
            # STEP 4: Build Response
            # ==========================================================
            response = {
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
                "delivery_aging": aging_result.get("delivery_aging"),
                "pod_aging": aging_result.get("pod_aging"),
                "total_cycle": aging_result.get("total_cycle"),
                "delivery_aging_text": aging_result.get("delivery_aging_text"),
                "pod_aging_text": aging_result.get("pod_aging_text"),
                "total_cycle_text": aging_result.get("total_cycle_text"),
                "is_valid": aging_result.get("is_valid", True),
                "issues": aging_result.get("issues", []),
                "scenario": aging_result.get("scenario"),
                "pod_received": aging_result.get("pod_received"),
                "pgi_completed": aging_result.get("pgi_completed"),
                "delivery_completed": aging_result.get("delivery_completed"),
            }
            
            total_time = time.time() - start_time
            logger.info(f"✅ DN {normalized} dashboard built successfully (took {total_time:.3f}s)")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get DN dashboard failed for {dn_no}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"error": f"Failed to load DN {dn_no}: {str(e)[:100]}"}

# ==========================================================
# END OF BLOCK 10 - DN DASHBOARD
# ==========================================================    
    
    
    
    # ==========================================================
# BLOCK 11: DEALER DASHBOARD (PRODUCTION-GRADE v4.0 - FIXED)
#==========================================================

# ==========================================================
# BLOCK 11: DEALER DASHBOARD (PRODUCTION-GRADE v5.0 - FIXED)
# ==========================================================

    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Complete dealer dashboard - Supports 360° view with fallback.
        BLOCK 11 - FIXED: Removed MAX() from text fields, uses DISTINCT ON.
        """
        import time
        start_time = time.time()
        
        try:
            logger.info(f"🔍 Searching for dealer: '{dealer_name}'")
            
            # ==========================================================
            # STEP 1: Try 360 Dashboard first
            # ==========================================================
            if self._dealer_360 is not None:
                try:
                    logger.info(f"🔍 Using 360 dashboard for: '{dealer_name}'")
                    result = self._dealer_360.get_dashboard(dealer_name)
                    
                    if "error" in result:
                        return result
                    
                    # Mark as 360 dashboard
                    result['_dashboard_type'] = '360'
                    logger.info(f"✅ 360 dashboard built successfully for: {dealer_name}")
                    return result
                except Exception as e:
                    logger.warning(f"⚠️ 360 dashboard failed, falling back to legacy: {e}")
            
            # ==========================================================
            # STEP 2: Fallback to legacy implementation (FIXED)
            # ==========================================================
            logger.info(f"🔍 Using legacy dashboard for: '{dealer_name}'")
            
            # Resolve dealer
            resolved = self.resolver.resolve_dealer(dealer_name)
            
            if not resolved:
                # Try to find similar dealers for suggestions
                try:
                    similar = self.search.search_dealer(dealer_name, exact=False)
                    if similar and len(similar) > 0:
                        suggestions = [s['dealer_name'] for s in similar[:5]]
                        return {
                            "error": f"Dealer '{dealer_name}' not found",
                            "suggestions": suggestions,
                            "message": f"Did you mean: {', '.join(suggestions[:3])}?"
                        }
                except Exception as e:
                    logger.error(f"Search error: {e}")
                
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # ==========================================================
            # STEP 3: Get dealer profile using DISTINCT ON (FIXED)
            # ==========================================================
            # Get the latest record for this dealer
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
                DeliveryReport.customer_name,
                DeliveryReport.dn_create_date.desc()
            ).first()
            
            # ==========================================================
            # STEP 4: Query dealer metrics
            # ==========================================================
            metrics_result = self.db.query(
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
                func.lower(DeliveryReport.customer_name) == func.lower(resolved)
            ).first()
            
            if not metrics_result or metrics_result.total_dns == 0:
                return {"error": f"No data found for dealer '{resolved}'"}
            
            # ==========================================================
            # STEP 5: Build response with profile data
            # ==========================================================
            total_dns = metrics_result.total_dns or 1
            delivered_dns = metrics_result.delivered_dns or 0
            transit_dns = metrics_result.transit_dns or 0
            pod_completed = metrics_result.pod_completed_dns or 0
            
            delivery_rate = KPIEngine.calculate_delivery_rate(delivered_dns, total_dns)
            pgi_rate = KPIEngine.calculate_pgi_rate(delivered_dns, transit_dns, total_dns)
            pod_rate = KPIEngine.calculate_pod_rate(pod_completed, delivered_dns) if delivered_dns > 0 else 0
            
            risk_level, risk_score = KPIEngine.calculate_risk_level(
                delivery_rate,
                pod_rate,
                0
            )
            
            response = {
                "dealer_name": resolved,
                # Profile data from DISTINCT ON
                "dealer_code": profile_result[0] if profile_result else "",
                "customer_code": profile_result[1] if profile_result else "",
                "division": profile_result[2] if profile_result else "",
                "warehouse": profile_result[3] if profile_result else "",
                "city": profile_result[4] if profile_result else "",
                "sales_office": profile_result[5] if profile_result else "",
                "sales_manager": profile_result[6] if profile_result else "",
                # Metrics
                "total_dns": total_dns,
                "total_units": int(metrics_result.total_units or 0),
                "total_revenue": float(metrics_result.total_revenue or 0),
                "delivered_dns": delivered_dns,
                "pending_dns": metrics_result.pending_dns or 0,
                "transit_dns": transit_dns,
                "pod_completed_dns": pod_completed,
                "pending_pod_dns": metrics_result.pending_pod_dns or 0,
                "pending_pgi_dns": metrics_result.pending_pgi_dns or 0,
                "product_count": metrics_result.product_count or 0,
                "city_count": metrics_result.city_count or 0,
                # KPIs
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
            
            # ==========================================================
            # STEP 6: Add distance if available
            # ==========================================================
            try:
                if profile_result and profile_result[3] and profile_result[4]:
                    from app.services.distance_service import get_distance_service
                    distance_service = get_distance_service()
                    distance_info = distance_service.calculate_warehouse_distance(
                        profile_result[3],  # warehouse
                        profile_result[4]   # city
                    )
                    if distance_info and distance_info.get('success'):
                        response['distance_km'] = distance_info.get('distance_km')
                        response['distance_approx_hours'] = distance_info.get('approx_driving_hours')
                        response['distance_miles'] = distance_info.get('distance_miles')
                        response['approx_driving_minutes'] = distance_info.get('approx_driving_minutes')
            except Exception as e:
                logger.error(f"Distance calculation error: {e}")
            
            total_time = time.time() - start_time
            logger.info(f"✅ Legacy dealer dashboard built successfully for: {resolved} (took {total_time:.3f}s)")
            return response
            
        except Exception as e:
            logger.error(f"❌ Get dealer dashboard failed for '{dealer_name}': {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {"error": f"Failed to load dealer data: {str(e)[:100]}"}

    
    # ==========================================================
# BLOCK 12: WAREHOUSE DASHBOARD (FIXED)
# ==========================================================

    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Complete warehouse dashboard from PostgreSQL with suggestions."""
        try:
            logger.info(f"🔍 Searching for warehouse: '{warehouse_name}'")
            resolved = self.resolver.resolve_warehouse(warehouse_name)
            
            if not resolved:
                logger.warning(f"❌ Warehouse '{warehouse_name}' not found")
                similar = self.search.search_warehouse(warehouse_name)
                if similar:
                    suggestions = [s['warehouse'] for s in similar[:5]]
                    return {
                        "error": f"Warehouse '{warehouse_name}' not found",
                        "suggestions": suggestions,
                        "message": f"Did you mean: {', '.join(suggestions[:3])}?"
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
# BLOCK 13: CITY DASHBOARD (FIXED)
# ==========================================================

    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """Complete city dashboard from PostgreSQL with suggestions."""
        try:
            logger.info(f"🔍 Searching for city: '{city_name}'")
            resolved = self.resolver.resolve_city(city_name)
            
            if not resolved:
                logger.warning(f"❌ City '{city_name}' not found")
                similar = self.search.search_city(city_name)
                if similar:
                    suggestions = [s['city'] for s in similar[:5]]
                    return {
                        "error": f"City '{city_name}' not found",
                        "suggestions": suggestions,
                        "message": f"Did you mean: {', '.join(suggestions[:3])}?"
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
# BLOCK 14: PRODUCT DASHBOARD (FIXED)
# ==========================================================

    def get_product_dashboard(self, product_name: str) -> Dict[str, Any]:
        """Complete product dashboard from PostgreSQL with suggestions."""
        try:
            logger.info(f"🔍 Searching for product: '{product_name}'")
            resolved = self.resolver.resolve_product(product_name)
            
            if not resolved:
                logger.warning(f"❌ Product '{product_name}' not found")
                similar = self.search.search_product(product_name)
                if similar:
                    suggestions = [s['product'] for s in similar[:5]]
                    return {
                        "error": f"Product '{product_name}' not found",
                        "suggestions": suggestions,
                        "message": f"Did you mean: {', '.join(suggestions[:3])}?"
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
            
            if any(word in question_lower for word in ["pod", "proof of delivery"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            if any(word in question_lower for word in ["pgi", "goods issue"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            if any(word in question_lower for word in ["units", "quantity", "qty", "pieces"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "product_dashboard":
                    return self.get_product_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
            if any(word in question_lower for word in ["dn", "delivery note", "order"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
                elif last_intent == "city_dashboard":
                    return self.get_city_dashboard(last_entity)
            
            if any(word in question_lower for word in ["products", "product", "models"]):
                if last_intent == "dealer_dashboard":
                    return {"message": "Product list for this dealer is available"}
                elif last_intent == "city_dashboard":
                    return {"message": "Product list for this city is available"}
            
            if any(word in question_lower for word in ["rank", "ranking", "top", "best"]):
                if last_intent == "dealer_dashboard":
                    return self.get_ranking_dashboard(10)
            
            if any(word in question_lower for word in ["aging", "old", "delay", "overdue"]):
                if last_intent == "dealer_dashboard":
                    return self.get_aging_dashboard()
                elif last_intent == "dn_dashboard":
                    return self.get_dn_dashboard(last_entity)
            
            if any(word in question_lower for word in ["pending", "not completed", "waiting"]):
                if last_intent == "dealer_dashboard":
                    return self.get_dealer_dashboard(last_entity)
                elif last_intent == "warehouse_dashboard":
                    return self.get_warehouse_dashboard(last_entity)
            
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
        logger.info("✅ AnalyticsService v30.0 initialized - PostgreSQL Only")
    
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
# BLOCK 28: DASHBOARD METHODS (COMPLETE)
# ==========================================================

    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get complete 360° dealer dashboard.
        BLOCK 28 - NEW METHOD
        
        Returns:
            AnalyticsResponse with 360° dealer dashboard data
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer 360 Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            # Check if repo has the method
            if not hasattr(self.repo, 'get_dealer_360_dashboard'):
                error_msg = "Dealer 360 dashboard not available"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            # Get dashboard
            result = self.repo.get_dealer_360_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                logger.error(f"❌ Dealer 360 dashboard error for {dealer_name}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ Dealer 360 dashboard returned successfully for {dealer_name}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for {dealer_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get dealer 360 dashboard failed for {dealer_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load dealer: {str(e)[:100]}")

    def get_dn_dashboard(self, dn_no: str) -> AnalyticsResponse:
        """
        Get DN Dashboard - Production Grade.
        BLOCK 28 - FIXED
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 DN Dashboard request for: {dn_no}")
            
            if not dn_no or not str(dn_no).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="DN number is required")
            
            dn_clean = re.sub(r'[^0-9]', '', str(dn_no).strip())
            if len(dn_clean) < 8 or len(dn_clean) > 12:
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=f"Invalid DN format: {dn_no}. Must be 8-12 digits.")
            
            if not hasattr(self.repo, 'get_dn_dashboard'):
                error_msg = "AnalyticsRepository missing method: get_dn_dashboard"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            result = self.repo.get_dn_dashboard(dn_clean)
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                logger.error(f"❌ DN dashboard error for {dn_clean}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ DN dashboard returned successfully for {dn_clean}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for DN {dn_no}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get DN dashboard failed for {dn_no}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load DN: {str(e)[:100]}")

    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get Dealer Dashboard - Production Grade.
        BLOCK 28 - FIXED
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Dealer Dashboard request for: {dealer_name}")
            
            if not dealer_name or not str(dealer_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Dealer name is required")
            
            # Try 360 dashboard first
            if hasattr(self.repo, 'get_dealer_360_dashboard'):
                logger.info(f"🔍 Using 360 dashboard for: {dealer_name}")
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
                
                # Mark as 360 dashboard
                result['_dashboard_type'] = '360'
                self.metrics["successful_requests"] += 1
                return AnalyticsResponse(success=True, data=result)
            
            # Fallback to legacy
            if not hasattr(self.repo, 'get_dealer_dashboard'):
                error_msg = "AnalyticsRepository missing method: get_dealer_dashboard"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            result = self.repo.get_dealer_dashboard(dealer_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                logger.error(f"❌ Dealer dashboard error for {dealer_name}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ Dealer dashboard returned successfully for {dealer_name}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for {dealer_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get dealer dashboard failed for {dealer_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load dealer: {str(e)[:100]}")

    def get_warehouse_dashboard(self, warehouse_name: str) -> AnalyticsResponse:
        """
        Get Warehouse Dashboard - Production Grade.
        BLOCK 28 - FIXED
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Warehouse Dashboard request for: {warehouse_name}")
            
            if not warehouse_name or not str(warehouse_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Warehouse name is required")
            
            if not hasattr(self.repo, 'get_warehouse_dashboard'):
                error_msg = "AnalyticsRepository missing method: get_warehouse_dashboard"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            result = self.repo.get_warehouse_dashboard(warehouse_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                logger.error(f"❌ Warehouse dashboard error for {warehouse_name}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ Warehouse dashboard returned successfully for {warehouse_name}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for {warehouse_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get warehouse dashboard failed for {warehouse_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load warehouse: {str(e)[:100]}")

    def get_city_dashboard(self, city_name: str) -> AnalyticsResponse:
        """
        Get City Dashboard - Production Grade.
        BLOCK 28 - FIXED
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 City Dashboard request for: {city_name}")
            
            if not city_name or not str(city_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="City name is required")
            
            if not hasattr(self.repo, 'get_city_dashboard'):
                error_msg = "AnalyticsRepository missing method: get_city_dashboard"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            result = self.repo.get_city_dashboard(city_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                logger.error(f"❌ City dashboard error for {city_name}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ City dashboard returned successfully for {city_name}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for {city_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get city dashboard failed for {city_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load city: {str(e)[:100]}")

    def get_product_dashboard(self, product_name: str) -> AnalyticsResponse:
        """
        Get Product Dashboard - Production Grade.
        BLOCK 28 - FIXED
        """
        try:
            self.metrics["total_requests"] += 1
            logger.info(f"🔍 Product Dashboard request for: {product_name}")
            
            if not product_name or not str(product_name).strip():
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error="Product name is required")
            
            if not hasattr(self.repo, 'get_product_dashboard'):
                error_msg = "AnalyticsRepository missing method: get_product_dashboard"
                logger.error(f"❌ {error_msg}")
                self.metrics["failed_requests"] += 1
                return AnalyticsResponse(success=False, error=error_msg)
            
            result = self.repo.get_product_dashboard(product_name.strip())
            
            if "error" in result:
                self.metrics["failed_requests"] += 1
                if "suggestions" in result:
                    return AnalyticsResponse(
                        success=False, 
                        error=result["error"],
                        data={"suggestions": result.get("suggestions", [])}
                    )
                logger.error(f"❌ Product dashboard error for {product_name}: {result['error']}")
                return AnalyticsResponse(success=False, error=result["error"])
            
            self.metrics["successful_requests"] += 1
            logger.info(f"✅ Product dashboard returned successfully for {product_name}")
            return AnalyticsResponse(success=True, data=result)
            
        except AttributeError as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ AttributeError for {product_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Method not found: {str(e)}")
            
        except Exception as e:
            self.metrics["failed_requests"] += 1
            logger.error(f"❌ Get product dashboard failed for {product_name}: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return AnalyticsResponse(success=False, error=f"Failed to load product: {str(e)[:100]}")

    def get_pgi_dashboard(self) -> AnalyticsResponse:
        try:
            self.metrics["total_requests"] += 1
            if not hasattr(self.repo, 'get_pgi_dashboard'):
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
                return AnalyticsResponse(success=False, error="Method not available")
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
# END OF BLOCK 28
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
# ==========================================================
# BLOCK 30: FACTORY FUNCTION (FIXED v5.0 - WITH DIAGNOSTICS)
# ==========================================================

_analytics_service = None
_analytics_initialization_attempts = 0
_MAX_ANALYTICS_INIT_ATTEMPTS = 3

def get_analytics_service(db: Optional[Session] = None) -> AnalyticsService:
    """
    Get or create AnalyticsService singleton with diagnostics and retry.
    BLOCK 30 - FIXED v5.0 - WITH COMPLETE DIAGNOSTICS
    """
    global _analytics_service, _analytics_initialization_attempts
    
    logger.info("=" * 60)
    logger.info("🔍 ANALYTICS SERVICE FACTORY DIAGNOSTICS")
    logger.info("=" * 60)
    
    # ==========================================================
    # DIAGNOSTIC 1: Check if service already exists
    # ==========================================================
    if _analytics_service is not None:
        logger.info(f"✅ AnalyticsService already initialized (instance: {id(_analytics_service)})")
        logger.info("=" * 60)
        return _analytics_service
    
    # ==========================================================
    # DIAGNOSTIC 2: Check initialization attempts
    # ==========================================================
    if _analytics_initialization_attempts >= _MAX_ANALYTICS_INIT_ATTEMPTS:
        logger.error(f"❌ Max initialization attempts ({_MAX_ANALYTICS_INIT_ATTEMPTS}) reached")
        logger.warning("⚠️ Creating a new instance anyway to prevent None")
        logger.info("=" * 60)
        _analytics_service = AnalyticsService(db)
        return _analytics_service
    
    _analytics_initialization_attempts += 1
    logger.info(f"📌 Initialization attempt {_analytics_initialization_attempts}/{_MAX_ANALYTICS_INIT_ATTEMPTS}")
    
    # ==========================================================
    # DIAGNOSTIC 3: Test Database Connection
    # ==========================================================
    try:
        from app.database import SessionLocal
        test_db = SessionLocal()
        total_records = test_db.query(DeliveryReport).count()
        test_db.close()
        logger.info(f"📌 Database connected. Found {total_records} records in delivery_reports")
        
        if total_records == 0:
            logger.warning("⚠️ Database has ZERO records! Analytics will return zeros.")
            logger.warning("   💡 Insert data into delivery_reports table")
    except Exception as e:
        logger.error(f"❌ Database connection test failed: {e}")
        logger.warning("⚠️ Proceeding with analytics service creation anyway")
    
    # ==========================================================
    # DIAGNOSTIC 4: Check if db session is valid
    # ==========================================================
    try:
        if db is not None:
            logger.info(f"📌 External session provided: {type(db)}")
        else:
            logger.info("📌 No external session provided - using internal session")
    except Exception as e:
        logger.warning(f"⚠️ Session check failed: {e}")
    
    # ==========================================================
    # DIAGNOSTIC 5: Create Analytics Service
    # ==========================================================
    try:
        logger.info("🔄 Creating AnalyticsService...")
        service = AnalyticsService(db)
        logger.info(f"✅ AnalyticsService created successfully (instance: {id(service)})")
        logger.info(f"📊 Service type: {type(service)}")
        logger.info(f"📊 Service class: {service.__class__.__name__}")
        
        # Store in global
        _analytics_service = service
        _analytics_initialization_attempts = 0  # Reset on success
        
        # ==========================================================
        # DIAGNOSTIC 6: Verify Service Methods
        # ==========================================================
        required_methods = [
            "search_dealer",
            "search_dn",
            "search_warehouse",
            "search_city",
            "search_product",
            "verify_dealer_exists",
            "verify_dn_exists",
            "get_dealer_dashboard",
            "get_warehouse_dashboard",
            "get_city_dashboard",
            "get_product_dashboard",
            "get_dn_dashboard",
            "get_pgi_dashboard",
            "get_pod_dashboard",
            "get_delivery_dashboard",
            "get_executive_dashboard",
            "get_control_tower_dashboard",
            "get_revenue_dashboard",
            "get_ranking_dashboard",
            "get_aging_dashboard"
        ]
        
        logger.info("🔍 Verifying analytics methods:")
        missing_methods = []
        available_methods = []
        
        for method in required_methods:
            if hasattr(service, method):
                available_methods.append(method)
                if method in ["search_dealer", "get_dealer_dashboard", "get_dn_dashboard"]:
                    logger.info(f"   ✅ {method}: AVAILABLE (CRITICAL)")
                else:
                    logger.info(f"   ✅ {method}: AVAILABLE")
            else:
                missing_methods.append(method)
                logger.error(f"   ❌ {method}: MISSING")
        
        if missing_methods:
            logger.error(f"❌ Missing {len(missing_methods)} methods: {missing_methods}")
        else:
            logger.info(f"✅ All {len(available_methods)} required methods available")
        
        # ==========================================================
        # DIAGNOSTIC 7: Check repo initialization
        # ==========================================================
        if hasattr(service, 'repo'):
            logger.info(f"📊 Repository type: {type(service.repo)}")
            if hasattr(service.repo, 'search'):
                logger.info("✅ Repository has search engine")
            if hasattr(service.repo, 'resolver'):
                logger.info("✅ Repository has entity resolver")
        else:
            logger.warning("⚠️ Service has no 'repo' attribute")
        
        # ==========================================================
        # DIAGNOSTIC 8: Test a sample query
        # ==========================================================
        try:
            test_dealers = service.search_dealer("test", exact=False)
            if test_dealers and hasattr(test_dealers, 'success'):
                logger.info(f"✅ Dealer search test: success={test_dealers.success}")
                if test_dealers.success and test_dealers.data:
                    logger.info(f"   Found {len(test_dealers.data.get('results', []))} results")
            elif isinstance(test_dealers, dict):
                results_count = len(test_dealers.get('data', {}).get('results', []))
                logger.info(f"✅ Dealer search test: returned {results_count} results")
            else:
                logger.info(f"✅ Dealer search test: {type(test_dealers)}")
        except Exception as e:
            logger.warning(f"⚠️ Dealer search test failed: {e}")
        
        logger.info("=" * 60)
        logger.info("✅ AnalyticsService initialization complete")
        logger.info("=" * 60)
        
        return service
        
    except AttributeError as e:
        logger.error(f"❌ AttributeError during initialization: {e}")
        import traceback
        logger.error(traceback.format_exc())
        
        # Check if analytics service is the issue
        if "analytics" in str(e).lower() or "method" in str(e).lower():
            logger.warning("⚠️ Analytics service issue detected - will retry on next request")
        
        _analytics_service = None
        logger.info("=" * 60)
        return None
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize AnalyticsService: {e}")
        import traceback
        logger.error(traceback.format_exc())
        _analytics_service = None
        logger.info("=" * 60)
        return None

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
# END OF FILE - v30.0 PRODUCTION READY
# ==========================================================
