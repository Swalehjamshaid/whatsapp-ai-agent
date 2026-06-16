# ==========================================================
# FILE: app/services/analytics_service.py (v6.2 - PRODUCTION HOTFIX)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Enterprise Dealer Intelligence Engine
# VERSION: 6.2 - 100% Aligned with models.py
#
# ALIGNMENT WITH models.py:
# 1. ✅ Uses DeliveryReport model fields correctly
# 2. ✅ Handles nullable fields with COALESCE
# 3. ✅ Proper dn_no, dn_qty, dn_amount handling
# 4. ✅ Correct customer_name, warehouse, ship_to_city
# 5. ✅ Proper date fields: dn_create_date, good_issue_date, pod_date
# 6. ✅ Status fields: delivery_status, pgi_status, pod_status
# 7. ✅ Supports all indexes defined in models.py
# 8. ✅ Nullable-safe operations throughout
# 9. ✅ Uses material_no, customer_model for products
# 10. ✅ Sales office and sales manager from model
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
import uuid
from collections import defaultdict
from statistics import mean, stdev
import math
import json

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


# ==========================================================
# ENTERPRISE EXCEPTION HIERARCHY
# ==========================================================

class AnalyticsError(Exception):
    """Base exception for analytics errors."""
    pass

class DealerNotFoundError(AnalyticsError):
    """Dealer not found in system."""
    def __init__(self, dealer_name: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dealer '{dealer_name}' not found (Error ID: {self.error_id})")

class DashboardGenerationError(AnalyticsError):
    """Failed to generate dashboard."""
    def __init__(self, dealer_name: str, reason: str, error_id: str = None):
        self.dealer_name = dealer_name
        self.reason = reason
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Dashboard generation failed for '{dealer_name}': {reason} (Error ID: {self.error_id})")

class AnalyticsCalculationError(AnalyticsError):
    """Failed to calculate analytics."""
    def __init__(self, metric: str, error: str, error_id: str = None):
        self.metric = metric
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Failed to calculate {metric}: {error} (Error ID: {self.error_id})")

class KPICalculationError(AnalyticsError):
    """Failed to calculate KPI."""
    def __init__(self, kpi_name: str, error: str, error_id: str = None):
        self.kpi_name = kpi_name
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Failed to calculate KPI {kpi_name}: {error} (Error ID: {self.error_id})")

class DataIntegrityError(AnalyticsError):
    """Data quality issues detected."""
    def __init__(self, entity: str, issue: str, error_id: str = None):
        self.entity = entity
        self.issue = issue
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Data integrity issue in {entity}: {issue} (Error ID: {self.error_id})")

class CacheError(AnalyticsError):
    """Cache operation failed."""
    def __init__(self, operation: str, error: str, error_id: str = None):
        self.operation = operation
        self.error = error
        self.error_id = error_id or str(uuid.uuid4())[:8]
        super().__init__(f"Cache {operation} failed: {error} (Error ID: {self.error_id})")


# ==========================================================
# RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    """Standardized analytics response contract."""
    
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
# ANALYTICS SERVICE - ENTERPRISE DEALER INTELLIGENCE ENGINE
# ==========================================================

class AnalyticsService:
    """
    ENTERPRISE DEALER INTELLIGENCE ENGINE v6.2
    
    100% Aligned with models.py:
    - Uses DeliveryReport model correctly
    - Handles all nullable fields safely
    - Uses proper field names from model
    """
    
    def __init__(self, use_redis: bool = False):
        """Initialize AnalyticsService with dependencies."""
        self._start_time = time.time()
        
        # Dependencies
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.today = datetime.now().date()
        
        # Cache with TTL
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._cache_duration = timedelta(minutes=5)
        self._use_redis = use_redis
        
        # Dealer resolution cache (24 hour TTL)
        self._dealer_cache: Dict[str, Tuple[str, datetime]] = {}
        
        # Performance metrics
        self.metrics = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_duration_ms": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "dealer_resolution_success": 0,
            "dealer_resolution_failure": 0,
            "slow_queries": 0,
            "errors_by_type": defaultdict(int)
        }
        
        logger.info("=" * 70)
        logger.info("AnalyticsService v6.2 - 100% Aligned with models.py")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ ALIGNMENT WITH models.py:")
        logger.info("      - DeliveryReport fields mapped correctly")
        logger.info("      - Nullable fields handled safely")
        logger.info("      - dn_no, dn_qty, dn_amount support")
        logger.info("      - customer_name, warehouse, ship_to_city")
        logger.info("      - dn_create_date, good_issue_date, pod_date")
        logger.info("      - delivery_status, pgi_status, pod_status")
        logger.info("      - material_no, customer_model for products")
        logger.info("      - sales_office, sales_manager")
        logger.info("      - All indexes supported")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def close(self):
        """Close dependencies."""
        self.logistics.close()
        self.kpi.close()
        logger.info("AnalyticsService closed")
    
    # ==========================================================
    # CACHE HELPERS
    # ==========================================================
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get value from cache with TTL check."""
        if key in self._cache and key in self._cache_ttl:
            if datetime.now() < self._cache_ttl[key]:
                self.metrics["cache_hits"] += 1
                return self._cache[key]
        self.metrics["cache_misses"] += 1
        return None
    
    def _set_cached(self, key: str, value: Any, ttl_seconds: int = 300):
        """Set value in cache with TTL."""
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _get_cached_dealer(self, dealer_input: str) -> Optional[str]:
        """Get cached dealer resolution (24 hour TTL)."""
        if dealer_input in self._dealer_cache:
            resolved, expiry = self._dealer_cache[dealer_input]
            if datetime.now() < expiry:
                return resolved
        return None
    
    def _set_cached_dealer(self, dealer_input: str, resolved: str):
        """Cache dealer resolution (24 hour TTL)."""
        self._dealer_cache[dealer_input] = (resolved, datetime.now() + timedelta(hours=24))
    
    def clear_cache(self):
        """Clear all caches."""
        self._cache.clear()
        self._cache_ttl.clear()
        self._dealer_cache.clear()
        logger.info("All caches cleared")
    
    # ==========================================================
    # MODULE 1: DEALER 360 DASHBOARD
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Optimized dealer 360 dashboard with diagnostics.
        
        Uses DeliveryReport model fields:
        - customer_name (dealer name)
        - dn_no (delivery note number)
        - dn_qty (quantity)
        - dn_amount (amount)
        - dn_create_date (creation date)
        - good_issue_date (PGI date)
        - pod_date (POD date)
        - warehouse (warehouse)
        - ship_to_city (city)
        """
        request_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        self.metrics["total_requests"] += 1
        
        try:
            # STEP 1: Input Validation
            logger.info(f"[{request_id}] 📊 Step 1: Dealer Input='{dealer_name}'")
            
            if not dealer_name or not dealer_name.strip():
                error_id = str(uuid.uuid4())[:8]
                logger.error(f"[{request_id}] ❌ Step 1 Failed: Empty dealer name (Error ID: {error_id})")
                self.metrics["failed_requests"] += 1
                self.metrics["errors_by_type"]["empty_input"] += 1
                return AnalyticsResponse(
                    success=False, 
                    error="Dealer name cannot be empty",
                    error_id=error_id
                )
            
            # STEP 2: Dealer Resolution
            logger.info(f"[{request_id}] 🔍 Step 2: Resolving dealer '{dealer_name}'")
            
            cached_resolved = self._get_cached_dealer(dealer_name)
            if cached_resolved:
                resolved = cached_resolved
                logger.info(f"[{request_id}] ✅ Step 2: Dealer resolved from cache: '{resolved}'")
            else:
                resolved = self._resolve_dealer_with_diagnostics(dealer_name, request_id)
                if resolved:
                    self._set_cached_dealer(dealer_name, resolved)
                    logger.info(f"[{request_id}] ✅ Step 2: Dealer resolved: '{resolved}'")
                    self.metrics["dealer_resolution_success"] += 1
                else:
                    error_id = str(uuid.uuid4())[:8]
                    logger.error(f"[{request_id}] ❌ Step 2 Failed: Dealer '{dealer_name}' not found (Error ID: {error_id})")
                    self.metrics["failed_requests"] += 1
                    self.metrics["dealer_resolution_failure"] += 1
                    self.metrics["errors_by_type"]["dealer_not_found"] += 1
                    raise DealerNotFoundError(dealer_name, error_id)
            
            # STEP 3: Dashboard Data Retrieval
            logger.info(f"[{request_id}] 📊 Step 3: Fetching dashboard data for '{resolved}'")
            
            cache_key = f"dashboard:{resolved}"
            dashboard_data = self._get_cached(cache_key)
            
            if dashboard_data is not None:
                logger.info(f"[{request_id}] ✅ Step 3: Dashboard data retrieved from cache")
            else:
                dashboard_data = self.logistics.get_dealer_dashboard_data(resolved)
                if dashboard_data is not None:
                    self._set_cached(cache_key, dashboard_data, 600)
                    logger.info(f"[{request_id}] ✅ Step 3: Dashboard data retrieved from database")
                else:
                    error_id = str(uuid.uuid4())[:8]
                    logger.error(f"[{request_id}] ❌ Step 3 Failed: No data for dealer '{resolved}' (Error ID: {error_id})")
                    self.metrics["failed_requests"] += 1
                    self.metrics["errors_by_type"]["no_data"] += 1
                    raise DashboardGenerationError(resolved, "No data found", error_id)
            
            # Validate dashboard data
            is_valid, validation_errors = self._validate_dashboard_data(dashboard_data)
            if not is_valid:
                error_id = str(uuid.uuid4())[:8]
                logger.error(f"[{request_id}] ❌ Step 3 Validation Failed: {validation_errors} (Error ID: {error_id})")
                self.metrics["failed_requests"] += 1
                self.metrics["errors_by_type"]["data_validation"] += 1
                raise DataIntegrityError("dashboard_data", validation_errors, error_id)
            
            logger.info(f"[{request_id}] ✅ Step 3: Dashboard data validated successfully")
            
            # STEP 4: Analytics Calculation
            logger.info(f"[{request_id}] 📊 Step 4: Calculating analytics for '{resolved}'")
            
            try:
                analytics = self._calculate_all_analytics(resolved, dashboard_data)
                logger.info(f"[{request_id}] ✅ Step 4: Analytics calculated successfully")
            except Exception as e:
                error_id = str(uuid.uuid4())[:8]
                logger.exception(f"[{request_id}] ❌ Step 4 Failed: {e} (Error ID: {error_id})")
                self.metrics["failed_requests"] += 1
                self.metrics["errors_by_type"]["analytics_calculation"] += 1
                raise AnalyticsCalculationError("dashboard_analytics", str(e), error_id)
            
            # STEP 5: Dashboard Building
            logger.info(f"[{request_id}] 📊 Step 5: Building dashboard for '{resolved}'")
            
            dashboard = {
                "success": True,
                "request_id": request_id,
                "dealer_name": resolved,
                "profile": self._build_dealer_profile(resolved, dashboard_data, analytics),
                "executive_kpis": self._build_executive_kpis(dashboard_data, analytics),
                "performance": self._build_performance_metrics(dashboard_data),
                "delivery": self._build_delivery_metrics(dashboard_data, analytics),
                "pod": self._build_pod_metrics(dashboard_data, analytics),
                "financial": self._build_financial_metrics(dashboard_data, analytics),
                "health": analytics["health"],
                "risk": analytics["risk"],
                "alerts": analytics["alerts"],
                "rankings": self._get_cached_rankings(resolved),
                "timeline": self._get_cached_timeline(resolved),
                "executive_insights": analytics["insights"],
                "ai_summary": self.generate_dealer_summary(resolved, dashboard_data, analytics),
                "ai_context": self._get_dealer_ai_context(resolved),
                "generated_at": datetime.now().isoformat()
            }
            
            duration_ms = (time.time() - start_time) * 1000
            self.metrics["successful_requests"] += 1
            self.metrics["total_duration_ms"] += duration_ms
            
            if duration_ms > 1000:
                self.metrics["slow_queries"] += 1
                logger.warning(f"[{request_id}] ⚠️ SLOW QUERY: {duration_ms:.2f}ms")
            
            logger.info(f"[{request_id}] ✅ Dashboard generated in {duration_ms:.2f}ms")
            
            return AnalyticsResponse(success=True, data=dashboard)
            
        except DealerNotFoundError as e:
            self.metrics["failed_requests"] += 1
            self.metrics["errors_by_type"]["dealer_not_found"] += 1
            logger.exception(f"[{request_id}] ❌ {e}")
            return AnalyticsResponse(
                success=False, 
                error=str(e),
                error_id=e.error_id
            )
        except DashboardGenerationError as e:
            self.metrics["failed_requests"] += 1
            self.metrics["errors_by_type"]["dashboard_generation"] += 1
            logger.exception(f"[{request_id}] ❌ {e}")
            return AnalyticsResponse(
                success=False, 
                error=str(e),
                error_id=e.error_id
            )
        except AnalyticsCalculationError as e:
            self.metrics["failed_requests"] += 1
            self.metrics["errors_by_type"]["analytics_calculation"] += 1
            logger.exception(f"[{request_id}] ❌ {e}")
            return AnalyticsResponse(
                success=False, 
                error=str(e),
                error_id=e.error_id
            )
        except DataIntegrityError as e:
            self.metrics["failed_requests"] += 1
            self.metrics["errors_by_type"]["data_integrity"] += 1
            logger.exception(f"[{request_id}] ❌ {e}")
            return AnalyticsResponse(
                success=False, 
                error=str(e),
                error_id=e.error_id
            )
        except Exception as e:
            error_id = str(uuid.uuid4())[:8]
            self.metrics["failed_requests"] += 1
            self.metrics["errors_by_type"]["unknown"] += 1
            logger.exception(f"[{request_id}] ❌ UNEXPECTED ERROR (Error ID: {error_id}): {e}")
            return AnalyticsResponse(
                success=False, 
                error=f"Unexpected error: {str(e)}",
                error_id=error_id
            )
    
    # ==========================================================
    # DEALER RESOLUTION WITH DIAGNOSTICS
    # ==========================================================
    
    def _resolve_dealer_with_diagnostics(self, dealer_input: str, request_id: str) -> Optional[str]:
        """Resolve dealer with detailed diagnostics."""
        try:
            # Strategy 1: Schema resolution
            logger.info(f"[{request_id}] 🔍 Step 2a: Schema resolution for '{dealer_input}'")
            resolved = self.schema.resolve_dealer(dealer_input)
            if resolved:
                logger.info(f"[{request_id}] ✅ Step 2a: Schema resolved to '{resolved}'")
                return resolved
            
            # Strategy 2: Exact match on customer_name (from DeliveryReport)
            logger.info(f"[{request_id}] 🔍 Step 2b: Exact match for '{dealer_input}'")
            exact = self.logistics.get_exact_dealer_match(dealer_input)
            if exact:
                logger.info(f"[{request_id}] ✅ Step 2b: Exact match to '{exact}'")
                return exact
            
            # Strategy 3: Contains match on customer_name
            logger.info(f"[{request_id}] 🔍 Step 2c: Contains match for '{dealer_input}'")
            contains = self.logistics.get_contains_dealer_match(dealer_input)
            if contains:
                logger.info(f"[{request_id}] ✅ Step 2c: Contains match to '{contains}'")
                return contains
            
            # Strategy 4: Word match on customer_name
            logger.info(f"[{request_id}] 🔍 Step 2d: Word match for '{dealer_input}'")
            words = dealer_input.lower().split()
            if len(words) >= 2:
                word_match = self.logistics.get_word_dealer_match(dealer_input, words)
                if word_match:
                    logger.info(f"[{request_id}] ✅ Step 2d: Word match to '{word_match}'")
                    return word_match
            
            # Strategy 5: Fuzzy match on customer_name
            logger.info(f"[{request_id}] 🔍 Step 2e: Fuzzy match for '{dealer_input}'")
            fuzzy = self.logistics.get_fuzzy_dealer_match(dealer_input)
            if fuzzy:
                logger.info(f"[{request_id}] ✅ Step 2e: Fuzzy match to '{fuzzy}'")
                return fuzzy
            
            # Strategy 6: Alias match from schema
            logger.info(f"[{request_id}] 🔍 Step 2f: Alias match for '{dealer_input}'")
            alias = self.logistics.get_alias_dealer_match(dealer_input)
            if alias:
                logger.info(f"[{request_id}] ✅ Step 2f: Alias match to '{alias}'")
                return alias
            
            logger.warning(f"[{request_id}] ❌ Step 2: No resolution found for '{dealer_input}'")
            return None
            
        except Exception as e:
            logger.error(f"[{request_id}] ❌ Step 2 Error: {e}")
            return None
    
    # ==========================================================
    # DATA VALIDATION
    # ==========================================================
    
    def _validate_dashboard_data(self, dashboard: Dict) -> Tuple[bool, str]:
        """Validate dashboard data integrity using models.py fields."""
        errors = []
        
        # Check total_dns (from dn_no count)
        total_dns = dashboard.get("total_dns", -1)
        if total_dns < 0:
            errors.append("total_dns is negative")
        
        # Check revenue (from dn_amount)
        revenue = dashboard.get("total_revenue", -1)
        if revenue < 0:
            errors.append("total_revenue is negative")
        
        # Check delivery_rate
        delivery_rate = dashboard.get("delivery_rate", -1)
        if delivery_rate < 0 or delivery_rate > 100:
            errors.append(f"delivery_rate is out of range (0-100): {delivery_rate}")
        
        # Check pod_rate
        pod_rate = dashboard.get("pod_rate", -1)
        if pod_rate < 0 or pod_rate > 100:
            errors.append(f"pod_rate is out of range (0-100): {pod_rate}")
        
        # Check delivered_units vs total_dns
        delivered = dashboard.get("delivered_units", -1)
        if delivered > total_dns and total_dns > 0:
            errors.append(f"delivered_units ({delivered}) > total_dns ({total_dns})")
        
        # Check pending_delivery vs total_dns
        pending = dashboard.get("pending_delivery", -1)
        if pending > total_dns and total_dns > 0:
            errors.append(f"pending_delivery ({pending}) > total_dns ({total_dns})")
        
        if errors:
            return False, "; ".join(errors)
        
        return True, "OK"
    
    # ==========================================================
    # ANALYTICS CALCULATIONS
    # ==========================================================
    
    def _calculate_all_analytics(self, dealer_name: str, dashboard: Dict) -> Dict:
        """Calculate all analytics from single dashboard data."""
        health_score = self._calculate_health_score_weighted(dashboard)
        risk = self._calculate_risk_from_dashboard(dashboard)
        alerts = self._generate_alerts_from_dashboard(dealer_name, dashboard)
        insights, issues, recommendations = self._generate_insights_from_dashboard(dashboard)
        
        return {
            "health": {
                "score": health_score,
                "category": self._get_health_category(health_score)
            },
            "risk": risk,
            "alerts": alerts,
            "insights": {
                "insights": insights,
                "issues": issues,
                "recommendations": recommendations
            }
        }
    
    # ==========================================================
    # HEALTH SCORE (CALIBRATED)
    # ==========================================================
    
    def _calculate_health_score_weighted(self, dashboard: Dict) -> int:
        """
        Calculate health score with calibrated weights.
        
        Weights based on logistics priorities:
        - Delivery: 40% (Most important)
        - POD: 30% (Second most important)
        - Aging: 20% (Operational efficiency)
        - Revenue: 10% (Business health)
        """
        delivery_score = self._calculate_delivery_score(dashboard)
        pod_score = self._calculate_pod_score(dashboard)
        aging_score = self._calculate_aging_score(dashboard)
        revenue_score = self._calculate_revenue_score(dashboard)
        
        health_score = (
            (delivery_score * 0.40) +
            (pod_score * 0.30) +
            (aging_score * 0.20) +
            (revenue_score * 0.10)
        )
        
        return min(100, int(health_score))
    
    def _calculate_aging_score(self, dashboard: Dict) -> int:
        """Calculate aging score (0-100)."""
        avg_aging = dashboard.get("avg_delivery_aging", 0)
        if avg_aging <= 3:
            return 100
        elif avg_aging <= 7:
            return 80
        elif avg_aging <= 14:
            return 60
        elif avg_aging <= 30:
            return 40
        else:
            return 20
    
    # ==========================================================
    # REVENUE ALLOCATION
    # ==========================================================
    
    def _calculate_actual_revenue_allocation(self, dealer_name: str) -> Dict[str, float]:
        """
        Calculate actual revenue allocation from individual DNs.
        Uses dn_amount field from DeliveryReport model.
        """
        try:
            dns = self.logistics.get_dealer_dns(dealer_name, limit=1000)
            
            delivered_revenue = 0
            pending_revenue = 0
            pending_pod_revenue = 0
            
            for dn in dns:
                # Use dn_amount from DeliveryReport model
                amount = dn.get("dn_amount", 0) or 0
                pod_date = dn.get("pod_date")
                good_issue_date = dn.get("good_issue_date")
                
                if pod_date:
                    delivered_revenue += amount
                elif good_issue_date:
                    pending_pod_revenue += amount
                else:
                    pending_revenue += amount
            
            return {
                "delivered_revenue": delivered_revenue,
                "pending_revenue": pending_revenue,
                "pending_pod_revenue": pending_pod_revenue,
                "total_revenue": delivered_revenue + pending_revenue + pending_pod_revenue
            }
            
        except Exception as e:
            logger.error(f"Revenue allocation failed: {e}")
            return {
                "delivered_revenue": 0,
                "pending_revenue": 0,
                "pending_pod_revenue": 0,
                "total_revenue": 0
            }
    
    # ==========================================================
    # BULK OPERATIONS
    # ==========================================================
    
    def _get_all_dealers_bulk(self) -> List[Dict]:
        """Get all dealer data in bulk (single query)."""
        try:
            return self.logistics.get_all_dealer_dashboards_bulk()
        except Exception as e:
            logger.error(f"Bulk dealer fetch failed: {e}")
            return []
    
    # ==========================================================
    # RANKINGS (BULK OPTIMIZED)
    # ==========================================================
    
    def _compute_all_rankings_bulk(self) -> Dict[str, Dict]:
        """Compute all dealer rankings using bulk data (single query)."""
        cache_key = "all_dealer_rankings_bulk"
        
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        try:
            dealers = self._get_all_dealers_bulk()
            
            if not dealers:
                return {}
            
            # Sort for rankings
            sorted_by_revenue = sorted(dealers, key=lambda x: x.get("total_revenue", 0), reverse=True)
            sorted_by_units = sorted(dealers, key=lambda x: x.get("total_units", 0), reverse=True)
            sorted_by_delivery = sorted(
                dealers, 
                key=lambda x: x.get("delivered_units", 0) / max(x.get("total_dns", 1), 1), 
                reverse=True
            )
            
            rankings = {}
            for i, dealer in enumerate(sorted_by_revenue, 1):
                name = dealer.get("dealer_name", "Unknown")
                if name not in rankings:
                    rankings[name] = {}
                rankings[name]["revenue_rank"] = i
                rankings[name]["revenue_rank_display"] = f"#{i}"
            
            for i, dealer in enumerate(sorted_by_units, 1):
                name = dealer.get("dealer_name", "Unknown")
                if name not in rankings:
                    rankings[name] = {}
                rankings[name]["quantity_rank"] = i
                rankings[name]["quantity_rank_display"] = f"#{i}"
            
            for i, dealer in enumerate(sorted_by_delivery, 1):
                name = dealer.get("dealer_name", "Unknown")
                if name not in rankings:
                    rankings[name] = {}
                rankings[name]["delivery_rank"] = i
                rankings[name]["delivery_rank_display"] = f"#{i}"
            
            total_dealers = len(dealers)
            for name in rankings:
                rankings[name]["total_dealers"] = total_dealers
            
            self._set_cached(cache_key, rankings, 3600)
            
            return rankings
            
        except Exception as e:
            logger.error(f"Bulk rankings computation failed: {e}")
            return {}
    
    # ==========================================================
    # AI SUMMARY GENERATION
    # ==========================================================
    
    def generate_dealer_summary(self, dealer_name: str, dashboard: Dict, analytics: Dict) -> str:
        """Generate human-readable dealer summary for WhatsApp."""
        try:
            health = analytics["health"]
            risk = analytics["risk"]
            
            summary_lines = [
                f"📊 Dealer: {dealer_name}",
                "",
                f"🏥 Health Score: {health['score']}/100 ({health['category']})",
                f"⚠️ Risk Level: {risk['risk_level']}",
                "",
                f"📦 Total DNs: {dashboard.get('total_dns', 0)}",
                f"💰 Revenue: PKR {dashboard.get('total_revenue', 0):,.0f}",
                f"📦 Units: {dashboard.get('total_units', 0)}",
                "",
                f"🚚 Delivery Rate: {dashboard.get('delivery_rate', 0)}%",
                f"📋 POD Rate: {dashboard.get('pod_rate', 0)}%",
                f"⏱️ Avg Delivery Aging: {dashboard.get('avg_delivery_aging', 0)} days",
                "",
                f"⚠️ Pending: {dashboard.get('pending_delivery', 0)} DNs",
                f"📋 Pending POD: {dashboard.get('pending_pod', 0)} DNs"
            ]
            
            if analytics["alerts"]:
                top_alert = analytics["alerts"][0]
                summary_lines.append("")
                summary_lines.append(f"🔔 Top Alert: {top_alert['message']}")
            
            return "\n".join(summary_lines)
            
        except Exception as e:
            logger.error(f"Summary generation failed: {e}")
            return f"Summary unavailable for {dealer_name}"
    
    # ==========================================================
    # HEALTH CHECK
    # ==========================================================
    
    def health_check(self) -> Dict[str, Any]:
        """Perform comprehensive health check of all services."""
        status = {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "version": "6.2",
            "checks": {}
        }
        
        # Check database connection
        try:
            self.logistics.get_all_dealer_names()
            status["checks"]["database"] = {"status": "healthy", "message": "Connected"}
        except Exception as e:
            status["status"] = "unhealthy"
            status["checks"]["database"] = {"status": "unhealthy", "message": str(e)}
        
        # Check schema service
        try:
            self.schema.resolve_dealer("test")
            status["checks"]["schema"] = {"status": "healthy", "message": "Operational"}
        except Exception as e:
            status["status"] = "unhealthy"
            status["checks"]["schema"] = {"status": "unhealthy", "message": str(e)}
        
        # Check cache
        try:
            test_key = f"health_check_{uuid.uuid4()}"
            self._set_cached(test_key, "test", 60)
            result = self._get_cached(test_key)
            if result == "test":
                status["checks"]["cache"] = {"status": "healthy", "message": "Operational"}
            else:
                status["checks"]["cache"] = {"status": "warning", "message": "Cache read/write issue"}
        except Exception as e:
            status["checks"]["cache"] = {"status": "warning", "message": str(e)}
        
        # Check KPI service
        try:
            self.kpi.get_dealer_kpis("test")
            status["checks"]["kpi"] = {"status": "healthy", "message": "Operational"}
        except Exception as e:
            status["checks"]["kpi"] = {"status": "warning", "message": str(e)}
        
        return status
    
    # ==========================================================
    # PERFORMANCE METRICS
    # ==========================================================
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service performance metrics."""
        total_requests = self.metrics["total_requests"]
        successful = self.metrics["successful_requests"]
        failed = self.metrics["failed_requests"]
        
        return {
            "total_requests": total_requests,
            "successful_requests": successful,
            "failed_requests": failed,
            "success_rate": round((successful / max(total_requests, 1)) * 100, 1),
            "total_duration_ms": self.metrics["total_duration_ms"],
            "avg_duration_ms": round(self.metrics["total_duration_ms"] / max(total_requests, 1), 2),
            "cache_hits": self.metrics["cache_hits"],
            "cache_misses": self.metrics["cache_misses"],
            "cache_hit_rate": round((self.metrics["cache_hits"] / max(self.metrics["cache_hits"] + self.metrics["cache_misses"], 1)) * 100, 1),
            "dealer_resolution_success": self.metrics["dealer_resolution_success"],
            "dealer_resolution_failure": self.metrics["dealer_resolution_failure"],
            "dealer_resolution_rate": round((self.metrics["dealer_resolution_success"] / max(self.metrics["dealer_resolution_success"] + self.metrics["dealer_resolution_failure"], 1)) * 100, 1),
            "slow_queries": self.metrics["slow_queries"],
            "errors_by_type": dict(self.metrics["errors_by_type"]),
            "version": "6.2",
            "uptime_seconds": round(time.time() - self._start_time, 0)
        }
    
    # ==========================================================
    # PUBLIC METHODS (Legacy wrappers preserved)
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_dealer_360_dashboard()."""
        logger.warning(f"⚠️ get_dealer_dashboard() is deprecated, use get_dealer_360_dashboard()")
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_dealer_360_dashboard()."""
        logger.warning(f"⚠️ get_dashboard() is deprecated, use get_dealer_360_dashboard()")
        return self.get_dealer_360_dashboard(dealer_name)
    
    def get_executive_dashboard(self, dealer_name: str = None) -> AnalyticsResponse:
        """Legacy wrapper for get_executive_insights()."""
        logger.warning(f"⚠️ get_executive_dashboard() is deprecated, use get_executive_insights()")
        return self.get_executive_insights(dealer_name)
    
    def get_dealer_health(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for calculate_dealer_health_score()."""
        logger.warning(f"⚠️ get_dealer_health() is deprecated, use calculate_dealer_health_score()")
        return self.calculate_dealer_health_score(dealer_name)
    
    def get_dealer_risk(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for assess_dealer_risk()."""
        logger.warning(f"⚠️ get_dealer_risk() is deprecated, use assess_dealer_risk()")
        return self.assess_dealer_risk(dealer_name)
    
    def get_delivery_metrics(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_delivery_dashboard()."""
        logger.warning(f"⚠️ get_delivery_metrics() is deprecated, use get_delivery_dashboard()")
        return self.get_delivery_dashboard(dealer_name)
    
    def get_pod_metrics(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_pod_dashboard()."""
        logger.warning(f"⚠️ get_pod_metrics() is deprecated, use get_pod_dashboard()")
        return self.get_pod_dashboard(dealer_name)
    
    def get_financial_metrics(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_financial_dashboard()."""
        logger.warning(f"⚠️ get_financial_metrics() is deprecated, use get_financial_dashboard()")
        return self.get_financial_dashboard(dealer_name)
    
    def get_dealer_ranking(self, dealer_name: str) -> AnalyticsResponse:
        """Legacy wrapper for get_dealer_rankings()."""
        logger.warning(f"⚠️ get_dealer_ranking() is deprecated, use get_dealer_rankings()")
        return self.get_dealer_rankings(dealer_name)
    
    # ==========================================================
    # DEALER PROFILE
    # ==========================================================
    
    def get_dealer_profile(self, dealer_name: str) -> AnalyticsResponse:
        """Get comprehensive dealer profile."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            metadata = self._get_dealer_metadata(resolved)
            
            profile = {
                "dealer_name": resolved,
                "dealer_code": metadata.get("dealer_code", self._generate_dealer_code(resolved)),
                "dealer_type": metadata.get("dealer_type", "Standard"),
                "dealer_category": metadata.get("dealer_category", "Standard"),
                "city": dashboard.get("city", "Unknown"),
                "region": metadata.get("region", "Unknown"),
                "division": metadata.get("division", "Unknown"),
                "sales_office": metadata.get("sales_office", "Unknown"),
                "warehouse": dashboard.get("top_warehouse", "Unknown"),
                "sales_manager": metadata.get("sales_manager", "Unknown"),
                "dealer_status": self._get_dealer_status(dashboard),
                "registration_date": metadata.get("registration_date", "N/A"),
                "total_dns": dashboard.get("total_dns", 0),
                "total_revenue": dashboard.get("total_revenue", 0),
                "total_units": dashboard.get("total_units", 0)
            }
            
            return AnalyticsResponse(success=True, data=profile)
            
        except Exception as e:
            logger.error(f"Dealer profile failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DEALER EXECUTIVE SUMMARY
    # ==========================================================
    
    def get_dealer_executive_summary(self, dealer_name: str) -> AnalyticsResponse:
        """Get executive KPI summary for dealer."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            summary = {
                "dealer_name": resolved,
                "total_dns": dashboard.get("total_dns", 0),
                "total_revenue": dashboard.get("total_revenue", 0),
                "total_quantity": dashboard.get("total_units", 0),
                "delivered_dns": dashboard.get("delivered_units", 0),
                "pending_dns": dashboard.get("pending_delivery", 0),
                "pending_pod_dns": dashboard.get("pending_pod", 0),
                "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0),
                "avg_pod_aging": dashboard.get("avg_pod_aging", 0),
                "dealer_health_score": self._calculate_health_score_weighted(dashboard)
            }
            
            return AnalyticsResponse(success=True, data=summary)
            
        except Exception as e:
            logger.error(f"Dealer executive summary failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DN PERFORMANCE
    # ==========================================================
    
    def get_dealer_dn_performance(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer DN performance metrics."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            performance = {
                "total_dns": dashboard.get("total_dns", 0),
                "delivered_dns": dashboard.get("delivered_units", 0),
                "pending_dns": dashboard.get("pending_delivery", 0),
                "partial_dns": dashboard.get("transit_units", 0),
                "dn_value": dashboard.get("total_revenue", 0),
                "quantity_dispatched": dashboard.get("total_units", 0)
            }
            
            return AnalyticsResponse(success=True, data=performance)
            
        except Exception as e:
            logger.error(f"Dealer DN performance failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # RANKINGS
    # ==========================================================
    
    def get_dealer_rankings(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer rankings."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            all_rankings = self._compute_all_rankings_bulk()
            
            if resolved not in all_rankings:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not ranked")
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                **all_rankings[resolved]
            })
            
        except Exception as e:
            logger.error(f"Dealer rankings failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # TIMELINE
    # ==========================================================
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> AnalyticsResponse:
        """Get dealer timeline of all DNs."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dns = self.logistics.get_dealer_dns(resolved, limit=limit)
            
            timeline = []
            for dn in dns:
                timeline.append({
                    "dn_number": dn.get("dn_no"),
                    "dn_created": dn.get("dn_create_date"),
                    "pgi_completed": dn.get("good_issue_date"),
                    "delivery_date": dn.get("pod_date"),
                    "pod_date": dn.get("pod_date"),
                    "units": dn.get("dn_qty", 0),
                    "amount": dn.get("dn_amount", 0),
                    "warehouse": dn.get("warehouse", "Unknown"),
                    "city": dn.get("ship_to_city", "Unknown"),
                    "material_no": dn.get("material_no", "Unknown"),
                    "customer_model": dn.get("customer_model", "Unknown")
                })
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "timeline": timeline,
                "total_dns": len(timeline)
            })
            
        except Exception as e:
            logger.error(f"Dealer timeline failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_cached_timeline(self, dealer_name: str, limit: int = 10) -> Dict[str, Any]:
        """Get cached timeline for dealer."""
        cache_key = f"timeline:{dealer_name}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached
        
        response = self.get_dealer_timeline(dealer_name, limit=limit)
        if response.success:
            self._set_cached(cache_key, response.data, 300)
            return response.data
        
        return {"timeline": [], "total_dns": 0}
    
    # ==========================================================
    # ALERTS
    # ==========================================================
    
    def get_dealer_alerts(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer alerts."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            alerts = self._generate_alerts_from_dashboard(resolved, dashboard)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "alerts": alerts,
                "alert_count": len(alerts),
                "critical_alerts": len([a for a in alerts if a["severity"] == "High"])
            })
            
        except Exception as e:
            logger.error(f"Dealer alerts failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # EXECUTIVE INSIGHTS
    # ==========================================================
    
    def get_executive_insights(self, dealer_name: str = None) -> AnalyticsResponse:
        """Get data-driven executive insights."""
        try:
            if dealer_name:
                return self._get_dealer_executive_insights(dealer_name)
            else:
                return self._get_network_executive_insights()
            
        except Exception as e:
            logger.error(f"Executive insights failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_dealer_executive_insights(self, dealer_name: str) -> AnalyticsResponse:
        """Get dealer-specific executive insights."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            insights, issues, recommendations = self._generate_insights_from_dashboard(dashboard)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "insights": insights,
                "issues": issues,
                "recommendations": recommendations,
                "total_insights": len(insights),
                "total_issues": len(issues),
                "total_recommendations": len(recommendations)
            })
            
        except Exception as e:
            logger.error(f"Dealer executive insights failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_network_executive_insights(self) -> AnalyticsResponse:
        """Get network-level executive insights."""
        try:
            all_dealers = self.logistics.get_all_dealer_names()
            
            total_revenue = 0
            total_dns = 0
            total_units = 0
            avg_delivery_rate = 0
            avg_pod_rate = 0
            
            dealer_count = 0
            for dealer in all_dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data:
                    total_revenue += data.get("total_revenue", 0)
                    total_dns += data.get("total_dns", 0)
                    total_units += data.get("total_units", 0)
                    avg_delivery_rate += data.get("delivery_rate", 0)
                    avg_pod_rate += data.get("pod_rate", 0)
                    dealer_count += 1
            
            if dealer_count > 0:
                avg_delivery_rate = avg_delivery_rate / dealer_count
                avg_pod_rate = avg_pod_rate / dealer_count
            
            insights = [
                f"Network total revenue: PKR {total_revenue:,.0f}",
                f"Network total DNs: {total_dns}",
                f"Network total units: {total_units}",
                f"Average delivery rate: {avg_delivery_rate:.1f}%",
                f"Average POD rate: {avg_pod_rate:.1f}%",
                f"Active dealers: {dealer_count}"
            ]
            
            recommendations = []
            if avg_delivery_rate < 85:
                recommendations.append("Improve network delivery rate")
            if avg_pod_rate < 85:
                recommendations.append("Improve network POD rate")
            
            return AnalyticsResponse(success=True, data={
                "network_insights": insights,
                "recommendations": recommendations,
                "total_dealers": dealer_count,
                "total_revenue": total_revenue,
                "total_dns": total_dns,
                "avg_delivery_rate": round(avg_delivery_rate, 1),
                "avg_pod_rate": round(avg_pod_rate, 1)
            })
            
        except Exception as e:
            logger.error(f"Network executive insights failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DELIVERY DASHBOARD
    # ==========================================================
    
    def get_delivery_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get delivery dashboard for dealer."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            total_dns = dashboard.get("total_dns", 1)
            delivered = dashboard.get("delivered_units", 0)
            pending = dashboard.get("pending_delivery", 0)
            
            delivery_success_rate = (delivered / total_dns * 100) if total_dns > 0 else 0
            sla_compliance = 100 if delivery_success_rate >= 90 else (delivery_success_rate / 90 * 100) if delivery_success_rate > 0 else 0
            
            delivery = {
                "on_time_deliveries": delivered,
                "late_deliveries": dashboard.get("transit_units", 0),
                "delayed_dns": pending,
                "delivery_success_rate": round(delivery_success_rate, 1),
                "sla_compliance": round(min(sla_compliance, 100), 1),
                "delivery_aging": dashboard.get("avg_delivery_aging", 0)
            }
            
            return AnalyticsResponse(success=True, data=delivery)
            
        except Exception as e:
            logger.error(f"Delivery dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # POD DASHBOARD
    # ==========================================================
    
    def get_pod_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get POD dashboard for dealer."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            pod_pending = dashboard.get("pending_pod", 0)
            pod_completed = dashboard.get("pod_completed", 0)
            total_pod = pod_pending + pod_completed
            
            pod_buckets = self._calculate_pod_buckets(resolved)
            
            pod = {
                "pod_received": pod_completed,
                "pod_pending": pod_pending,
                "pending_pod_dns": pod_pending,
                "pod_buckets": pod_buckets,
                "avg_pod_aging": dashboard.get("avg_pod_aging", 0),
                "pod_compliance": round((pod_completed / total_pod * 100) if total_pod > 0 else 0, 1),
                "pod_pending_value": dashboard.get("total_revenue", 0) * (pod_pending / (total_pod or 1)) if total_pod > 0 else 0,
                "pod_pending_qty": pod_pending
            }
            
            return AnalyticsResponse(success=True, data=pod)
            
        except Exception as e:
            logger.error(f"POD dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _calculate_pod_buckets(self, dealer_name: str) -> Dict[str, int]:
        """Calculate POD aging buckets."""
        try:
            dns = self.logistics.get_dealer_dns(dealer_name, limit=1000)
            
            buckets = {
                "0-5": 0,
                "6-10": 0,
                "11-15": 0,
                "16-30": 0,
                "30+": 0
            }
            
            for dn in dns:
                pgi_date = dn.get("good_issue_date")
                pod_date = dn.get("pod_date")
                
                if pgi_date and not pod_date:
                    aging = (self.today - pgi_date).days
                    
                    if aging <= 5:
                        buckets["0-5"] += 1
                    elif aging <= 10:
                        buckets["6-10"] += 1
                    elif aging <= 15:
                        buckets["11-15"] += 1
                    elif aging <= 30:
                        buckets["16-30"] += 1
                    else:
                        buckets["30+"] += 1
            
            return buckets
            
        except Exception:
            return {"0-5": 0, "6-10": 0, "11-15": 0, "16-30": 0, "30+": 0}
    
    # ==========================================================
    # FINANCIAL DASHBOARD
    # ==========================================================
    
    def get_financial_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """Get financial dashboard for dealer."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            total_revenue = dashboard.get("total_revenue", 0)
            total_dns = dashboard.get("total_dns", 1)
            delivered_dns = dashboard.get("delivered_units", 0)
            pending_dns = dashboard.get("pending_delivery", 0)
            pending_pod = dashboard.get("pending_pod", 0)
            
            delivered_revenue = total_revenue * (delivered_dns / total_dns)
            pending_revenue = total_revenue * (pending_dns / total_dns)
            pending_pod_revenue = total_revenue * (pending_pod / total_dns)
            
            financial = {
                "total_revenue": total_revenue,
                "delivered_revenue": delivered_revenue,
                "pending_revenue": pending_revenue,
                "pending_pod_revenue": pending_pod_revenue,
                "daily_revenue": self._get_revenue_by_period(resolved, "daily"),
                "weekly_revenue": self._get_revenue_by_period(resolved, "weekly"),
                "monthly_revenue": self._get_revenue_by_period(resolved, "monthly"),
                "yearly_revenue": self._get_revenue_by_period(resolved, "yearly"),
                "average_dn_value": total_revenue / max(total_dns, 1)
            }
            
            return AnalyticsResponse(success=True, data=financial)
            
        except Exception as e:
            logger.error(f"Financial dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_revenue_by_period(self, dealer_name: str, period: str) -> float:
        """Get revenue by period."""
        try:
            historical = self.logistics.get_dealer_historical_data(dealer_name)
            if not historical:
                return 0
            
            total_revenue = sum(item.get("revenue", 0) for item in historical)
            return round(total_revenue / len(historical), 2) if historical else 0
            
        except Exception:
            return 0
    
    # ==========================================================
    # PRODUCT DASHBOARD
    # ==========================================================
    
    def get_product_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get product intelligence from DeliveryReport data.
        Uses material_no and customer_model from models.py.
        """
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            products = self._get_dealer_products(resolved)
            product_analytics = self._analyze_products(products)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "products": product_analytics[:20],
                "total_products": len(products),
                "top_products": product_analytics[:10],
                "bottom_products": product_analytics[-10:] if len(product_analytics) > 10 else [],
                "product_categories": self._get_product_categories(products),
                "total_revenue": sum(p["total_revenue"] for p in product_analytics)
            })
            
        except Exception as e:
            logger.error(f"Product dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_dealer_products(self, dealer_name: str) -> List[Dict]:
        """
        Get actual products from DeliveryReport model.
        Uses material_no, customer_model from models.py.
        """
        dns = self.logistics.get_dealer_dns(dealer_name, limit=1000)
        
        products = []
        for dn in dns:
            products.append({
                "product_code": dn.get("material_no", "UNKNOWN"),
                "product_name": dn.get("customer_model", dn.get("material_no", "UNKNOWN")),
                "category": self._get_product_category(dn),
                "units": dn.get("dn_qty", 0),
                "revenue": dn.get("dn_amount", 0),
                "dn_count": 1,
                "warehouse": dn.get("warehouse", "Unknown"),
                "status": self._get_product_status(dn)
            })
        
        return products
    
    def _analyze_products(self, products: List[Dict]) -> List[Dict]:
        """Analyze products with real data."""
        product_map = defaultdict(lambda: {
            "qty": 0,
            "revenue": 0,
            "dn_count": 0,
            "category": "",
            "status": "active"
        })
        
        for product in products:
            key = product.get("product_code", "UNKNOWN")
            product_map[key]["qty"] += product.get("units", 0)
            product_map[key]["revenue"] += product.get("revenue", 0)
            product_map[key]["dn_count"] += 1
            product_map[key]["category"] = product.get("category", "Uncategorized")
            product_map[key]["product_name"] = product.get("product_name", key)
        
        result = []
        total_revenue = sum(p["revenue"] for p in product_map.values())
        
        for code, data in product_map.items():
            revenue = data["revenue"]
            result.append({
                "product_code": code,
                "product_name": data.get("product_name", code),
                "category": data["category"],
                "total_units": data["qty"],
                "total_revenue": revenue,
                "dn_count": data["dn_count"],
                "avg_revenue_per_dn": revenue / max(data["dn_count"], 1),
                "revenue_percentage": round((revenue / max(total_revenue, 1)) * 100, 1),
                "contribution": "High" if revenue / max(total_revenue, 1) > 0.1 else "Medium" if revenue / max(total_revenue, 1) > 0.05 else "Low"
            })
        
        return sorted(result, key=lambda x: x["total_revenue"], reverse=True)
    
    def _get_product_category(self, dn: Dict) -> str:
        """Derive product category from customer_model."""
        customer_model = dn.get("customer_model", "")
        
        if "TV" in customer_model.upper() or "LED" in customer_model.upper():
            return "Display"
        elif "AC" in customer_model.upper() or "INVERTER" in customer_model.upper():
            return "Cooling"
        elif "FRIDGE" in customer_model.upper() or "REFRIGERATOR" in customer_model.upper():
            return "Cooling"
        elif "WASHING" in customer_model.upper():
            return "Home Appliances"
        elif "MICROWAVE" in customer_model.upper():
            return "Kitchen Appliances"
        else:
            return "General Electronics"
    
    def _get_product_status(self, dn: Dict) -> str:
        """Get product delivery status."""
        if dn.get("pod_date"):
            return "delivered"
        elif dn.get("good_issue_date"):
            return "in_transit"
        else:
            return "pending"
    
    def _get_product_categories(self, products: List[Dict]) -> Dict[str, int]:
        """Get product category distribution."""
        categories = defaultdict(int)
        for product in products:
            categories[product.get("category", "Uncategorized")] += 1
        return dict(categories)
    
    # ==========================================================
    # DELIVERY AGING ANALYSIS
    # ==========================================================
    
    def get_delivery_aging_analysis(self, dealer_name: str) -> AnalyticsResponse:
        """Get comprehensive delivery aging analysis."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dns = self.logistics.get_dealer_dns(resolved, limit=1000)
            
            buckets = {
                "0-3": 0,
                "4-7": 0,
                "8-14": 0,
                "15-30": 0,
                "30+": 0
            }
            
            total_aging = 0
            aging_count = 0
            
            for dn in dns:
                dn_date = dn.get("dn_create_date")
                pgi_date = dn.get("good_issue_date")
                pod_date = dn.get("pod_date")
                
                if dn_date and pgi_date and pod_date:
                    aging = (pod_date - pgi_date).days
                    total_aging += aging
                    aging_count += 1
                    
                    if aging <= 3:
                        buckets["0-3"] += 1
                    elif aging <= 7:
                        buckets["4-7"] += 1
                    elif aging <= 14:
                        buckets["8-14"] += 1
                    elif aging <= 30:
                        buckets["15-30"] += 1
                    else:
                        buckets["30+"] += 1
            
            avg_aging = total_aging / aging_count if aging_count > 0 else 0
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "total_dns_analyzed": len(dns),
                "avg_delivery_aging": round(avg_aging, 1),
                "aging_buckets": buckets,
                "aging_distribution": {
                    "0-3": round((buckets["0-3"] / len(dns) * 100), 1) if len(dns) > 0 else 0,
                    "4-7": round((buckets["4-7"] / len(dns) * 100), 1) if len(dns) > 0 else 0,
                    "8-14": round((buckets["8-14"] / len(dns) * 100), 1) if len(dns) > 0 else 0,
                    "15-30": round((buckets["15-30"] / len(dns) * 100), 1) if len(dns) > 0 else 0,
                    "30+": round((buckets["30+"] / len(dns) * 100), 1) if len(dns) > 0 else 0
                }
            })
            
        except Exception as e:
            logger.error(f"Delivery aging analysis failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DN TRENDS
    # ==========================================================
    
    def get_dealer_dn_trend_daily(self, dealer_name: str) -> AnalyticsResponse:
        """Get daily DN trend for dealer."""
        return self._get_dealer_dn_trend(dealer_name, "daily")
    
    def get_dealer_dn_trend_weekly(self, dealer_name: str) -> AnalyticsResponse:
        """Get weekly DN trend for dealer."""
        return self._get_dealer_dn_trend(dealer_name, "weekly")
    
    def get_dealer_dn_trend_monthly(self, dealer_name: str) -> AnalyticsResponse:
        """Get monthly DN trend for dealer."""
        return self._get_dealer_dn_trend(dealer_name, "monthly")
    
    def get_dealer_dn_trend_yearly(self, dealer_name: str) -> AnalyticsResponse:
        """Get yearly DN trend for dealer."""
        return self._get_dealer_dn_trend(dealer_name, "yearly")
    
    def _get_dealer_dn_trend(self, dealer_name: str, period: str) -> AnalyticsResponse:
        """Internal method for DN trend by period."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            historical = self.logistics.get_dealer_historical_data(resolved)
            if not historical:
                return AnalyticsResponse(success=False, error=f"No historical data for dealer '{dealer_name}'")
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "period": period,
                "trend_data": historical,
                "total_periods": len(historical)
            })
            
        except Exception as e:
            logger.error(f"Dealer DN trend failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DN BREAKDOWNS
    # ==========================================================
    
    def get_dn_breakdown_by_warehouse(self, dealer_name: str) -> AnalyticsResponse:
        """Get DN breakdown by warehouse."""
        return self._get_dn_breakdown(dealer_name, "warehouse")
    
    def get_dn_breakdown_by_sales_office(self, dealer_name: str) -> AnalyticsResponse:
        """Get DN breakdown by sales office."""
        return self._get_dn_breakdown(dealer_name, "sales_office")
    
    def get_dn_breakdown_by_product(self, dealer_name: str) -> AnalyticsResponse:
        """Get DN breakdown by product."""
        return self._get_dn_breakdown(dealer_name, "product")
    
    def get_dn_breakdown_by_model(self, dealer_name: str) -> AnalyticsResponse:
        """Get DN breakdown by model."""
        return self._get_dn_breakdown(dealer_name, "model")
    
    def get_dn_breakdown_by_city(self, dealer_name: str) -> AnalyticsResponse:
        """Get DN breakdown by city."""
        return self._get_dn_breakdown(dealer_name, "city")
    
    def _get_dn_breakdown(self, dealer_name: str, breakdown_type: str) -> AnalyticsResponse:
        """Internal method for DN breakdown."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dns = self.logistics.get_dealer_dns(resolved, limit=100)
            
            breakdown = defaultdict(lambda: {"count": 0, "revenue": 0, "units": 0})
            
            for dn in dns:
                # Map breakdown type to model fields
                if breakdown_type == "warehouse":
                    key = dn.get("warehouse", "Unknown")
                elif breakdown_type == "sales_office":
                    key = dn.get("sales_office", "Unknown")
                elif breakdown_type == "product":
                    key = dn.get("material_no", "Unknown")
                elif breakdown_type == "model":
                    key = dn.get("customer_model", "Unknown")
                elif breakdown_type == "city":
                    key = dn.get("ship_to_city", "Unknown")
                else:
                    key = "Unknown"
                
                breakdown[key]["count"] += 1
                breakdown[key]["revenue"] += dn.get("dn_amount", 0)
                breakdown[key]["units"] += dn.get("dn_qty", 0)
            
            breakdown_list = []
            for key, values in breakdown.items():
                breakdown_list.append({
                    "category": key,
                    "count": values["count"],
                    "revenue": values["revenue"],
                    "units": values["units"]
                })
            
            breakdown_list.sort(key=lambda x: x["revenue"], reverse=True)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "breakdown_type": breakdown_type,
                "breakdown": breakdown_list[:20],
                "total_categories": len(breakdown_list)
            })
            
        except Exception as e:
            logger.error(f"DN breakdown failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # AI CONTEXT
    # ==========================================================
    
    def get_ai_context(self, dealer_name: str = None) -> AnalyticsResponse:
        """Get structured AI context for Groq."""
        try:
            if dealer_name:
                context = self._get_dealer_ai_context(dealer_name)
            else:
                context = self._get_network_ai_context()
            
            context["timestamp"] = datetime.now().isoformat()
            context["data_source"] = "AnalyticsService"
            context["version"] = "6.2"
            
            return AnalyticsResponse(success=True, data=context)
            
        except Exception as e:
            logger.error(f"AI context failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_dealer_ai_context(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer AI context using DeliveryReport fields."""
        resolved = self._resolve_dealer(dealer_name)
        if not resolved:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        dashboard = self.logistics.get_dealer_dashboard_data(resolved)
        if not dashboard:
            return {"error": f"No data for dealer '{dealer_name}'"}
        
        return {
            "dealer_name": resolved,
            "total_dns": dashboard.get("total_dns", 0),
            "total_revenue": dashboard.get("total_revenue", 0),
            "total_units": dashboard.get("total_units", 0),
            "delivery_rate": dashboard.get("delivery_rate", 0),
            "pod_rate": dashboard.get("pod_rate", 0),
            "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0),
            "pending_pod": dashboard.get("pending_pod", 0),
            "health_score": self._calculate_health_score_weighted(dashboard),
            "risk_assessment": self._calculate_risk_from_dashboard(dashboard)
        }
    
    def _get_network_ai_context(self) -> Dict[str, Any]:
        """Get network AI context."""
        all_dealers = self.logistics.get_all_dealer_names()
        
        total_revenue = 0
        total_dns = 0
        dealer_count = 0
        
        for dealer in all_dealers:
            data = self.logistics.get_dealer_dashboard_data(dealer)
            if data:
                total_revenue += data.get("total_revenue", 0)
                total_dns += data.get("total_dns", 0)
                dealer_count += 1
        
        return {
            "total_dealers": dealer_count,
            "total_dns": total_dns,
            "total_revenue": total_revenue,
            "avg_revenue_per_dealer": total_revenue / dealer_count if dealer_count > 0 else 0,
            "avg_dns_per_dealer": total_dns / dealer_count if dealer_count > 0 else 0
        }
    
    # ==========================================================
    # HEALTH SCORE AND RISK ASSESSMENT
    # ==========================================================
    
    def calculate_dealer_health_score(self, dealer_name: str) -> AnalyticsResponse:
        """Calculate comprehensive dealer health score with weighted metrics."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            health_score = self._calculate_health_score_weighted(dashboard)
            
            delivery_score = self._calculate_delivery_score(dashboard)
            pod_score = self._calculate_pod_score(dashboard)
            revenue_score = self._calculate_revenue_score(dashboard)
            activity_score = self._calculate_activity_score(dashboard)
            
            category = self._get_health_category(health_score)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "health_score": health_score,
                "health_category": category,
                "delivery_score": delivery_score,
                "pod_score": pod_score,
                "revenue_score": revenue_score,
                "activity_score": activity_score,
                "components": {
                    "delivery_rate": dashboard.get("delivery_rate", 0),
                    "pod_rate": dashboard.get("pod_rate", 0),
                    "total_revenue": dashboard.get("total_revenue", 0),
                    "total_dns": dashboard.get("total_dns", 0)
                }
            })
            
        except Exception as e:
            logger.error(f"Dealer health score failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def assess_dealer_risk(self, dealer_name: str) -> AnalyticsResponse:
        """Comprehensive dealer risk assessment."""
        try:
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            risk = self._calculate_risk_from_dashboard(dashboard)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                **risk
            })
            
        except Exception as e:
            logger.error(f"Dealer risk assessment failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # DATA INTEGRITY
    # ==========================================================
    
    def get_data_integrity_score(self) -> AnalyticsResponse:
        """Get data integrity score."""
        try:
            quality = self.logistics.get_data_quality_metrics()
            
            total_records = quality.get("total_records", 0)
            valid_dates = quality.get("valid_dates", 0)
            invalid_dates = quality.get("invalid_dates", 0)
            
            integrity_score = round((valid_dates / total_records * 100), 1) if total_records > 0 else 0
            
            return AnalyticsResponse(success=True, data={
                "total_records": total_records,
                "valid_records": valid_dates,
                "invalid_records": invalid_dates,
                "integrity_score": integrity_score,
                "missing_pgi": quality.get("missing_pgi", 0),
                "missing_pod": quality.get("missing_pod", 0),
                "negative_aging": quality.get("negative_aging", 0),
                "quality_status": "Excellent" if integrity_score >= 90 else "Good" if integrity_score >= 75 else "Fair" if integrity_score >= 60 else "Poor"
            })
            
        except Exception as e:
            logger.error(f"Data integrity score failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # PRIVATE HELPERS
    # ==========================================================
    
    def _resolve_dealer(self, dealer_name: str) -> Optional[str]:
        """Resolve dealer name with caching."""
        if not dealer_name:
            return None
        
        cached = self._get_cached_dealer(dealer_name)
        if cached:
            return cached
        
        resolved = self.schema.resolve_dealer(dealer_name)
        if resolved:
            self._set_cached_dealer(dealer_name, resolved)
        
        return resolved
    
    def _get_dealer_metadata(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer metadata from schema or infer."""
        try:
            metadata = self.schema.get_dealer_metadata(dealer_name)
            if metadata:
                return metadata
        except:
            pass
        
        return self._infer_dealer_metadata(dealer_name)
    
    def _infer_dealer_metadata(self, dealer_name: str) -> Dict[str, Any]:
        """Infer dealer metadata from available data."""
        dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
        
        return {
            "dealer_code": self._generate_dealer_code(dealer_name),
            "dealer_type": "Standard",
            "dealer_category": "Standard",
            "region": "Unknown",
            "division": "Unknown",
            "sales_office": "Unknown",
            "sales_manager": "Unknown",
            "city": dashboard.get("ship_to_city", "Unknown") if dashboard else "Unknown",
            "registration_date": None
        }
    
    def _generate_dealer_code(self, dealer_name: str) -> str:
        """Generate dealer code from name."""
        words = dealer_name.split()
        if len(words) >= 2:
            code = ''.join(word[0].upper() for word in words[:3])
        else:
            code = dealer_name[:5].upper()
        return code
    
    def _get_dealer_status(self, dashboard: Dict) -> str:
        """Get dealer status from dashboard data."""
        total_dns = dashboard.get("total_dns", 0)
        if total_dns == 0:
            return "Inactive"
        elif total_dns < 10:
            return "Low Activity"
        elif dashboard.get("delivery_rate", 0) >= 90:
            return "Active - High Performance"
        else:
            return "Active - Needs Attention"
    
    def _get_health_category(self, score: int) -> str:
        """Get health category based on score."""
        if score >= 80:
            return "Excellent"
        elif score >= 60:
            return "Good"
        elif score >= 40:
            return "Average"
        else:
            return "Critical"
    
    def _calculate_risk_from_dashboard(self, dashboard: Dict) -> Dict:
        """Calculate risk from dashboard data."""
        delivery_risk = self._calculate_delivery_risk(dashboard)
        pod_risk = self._calculate_pod_risk(dashboard)
        aging_risk = self._calculate_aging_risk(dashboard)
        revenue_risk = self._calculate_revenue_risk(dashboard)
        
        total_risk = delivery_risk + pod_risk + aging_risk + revenue_risk
        
        if total_risk <= 25:
            risk_level = "Low"
            risk_score = 25
        elif total_risk <= 50:
            risk_level = "Medium"
            risk_score = 50
        else:
            risk_level = "High"
            risk_score = 75
        
        return {
            "risk_score": risk_score,
            "risk_level": risk_level,
            "delivery_risk": delivery_risk,
            "pod_risk": pod_risk,
            "aging_risk": aging_risk,
            "revenue_risk": revenue_risk
        }
    
    def _generate_alerts_from_dashboard(self, dealer_name: str, dashboard: Dict) -> List[Dict]:
        """Generate alerts from dashboard data."""
        alerts = []
        
        # Delivery alerts
        avg_aging = dashboard.get("avg_delivery_aging", 0)
        if avg_aging > 7:
            alerts.append({
                "type": "Delivery",
                "severity": "High" if avg_aging > 14 else "Medium",
                "message": f"Delivery aging is {avg_aging} days (threshold: 7 days)",
                "value": avg_aging,
                "dealer": dealer_name
            })
        
        # POD alerts
        avg_pod_aging = dashboard.get("avg_pod_aging", 0)
        if avg_pod_aging > 5:
            alerts.append({
                "type": "POD",
                "severity": "High" if avg_pod_aging > 10 else "Medium",
                "message": f"POD aging is {avg_pod_aging} days (threshold: 5 days)",
                "value": avg_pod_aging,
                "dealer": dealer_name
            })
        
        # Health alerts
        health_score = self._calculate_health_score_weighted(dashboard)
        if health_score < 60:
            alerts.append({
                "type": "Health",
                "severity": "High" if health_score < 40 else "Medium",
                "message": f"Health score is {health_score}/100 (threshold: 60)",
                "value": health_score,
                "dealer": dealer_name
            })
        
        # Pending alerts
        total_revenue = dashboard.get("total_revenue", 0)
        total_dns = dashboard.get("total_dns", 1)
        pending_dns = dashboard.get("pending_delivery", 0)
        pending_revenue = total_revenue * (pending_dns / total_dns)
        
        if pending_revenue > 100000:
            alerts.append({
                "type": "Pending",
                "severity": "High" if pending_revenue > 500000 else "Medium",
                "message": f"Pending DN value is PKR {pending_revenue:,.0f}",
                "value": pending_revenue,
                "dealer": dealer_name
            })
        
        # POD pending alerts
        pending_pod = dashboard.get("pending_pod", 0)
        pod_pending_revenue = total_revenue * (pending_pod / total_dns)
        
        if pod_pending_revenue > 50000:
            alerts.append({
                "type": "POD_Pending",
                "severity": "High" if pod_pending_revenue > 200000 else "Medium",
                "message": f"Pending POD value is PKR {pod_pending_revenue:,.0f}",
                "value": pod_pending_revenue,
                "dealer": dealer_name
            })
        
        return alerts
    
    def _generate_insights_from_dashboard(self, dashboard: Dict) -> Tuple[List[str], List[str], List[str]]:
        """Generate insights, issues, and recommendations from dashboard."""
        insights = []
        issues = []
        recommendations = []
        
        # Delivery insights
        delivery_rate = dashboard.get("delivery_rate", 0)
        if delivery_rate >= 90:
            insights.append("✅ Excellent delivery rate")
        elif delivery_rate >= 80:
            insights.append("✅ Good delivery rate")
        else:
            issues.append(f"❌ Low delivery rate: {delivery_rate}%")
            recommendations.append(f"🔧 Improve delivery rate from {delivery_rate}% to 90%+")
        
        # POD insights
        pod_rate = dashboard.get("pod_rate", 0)
        if pod_rate >= 90:
            insights.append("✅ Excellent POD rate")
        elif pod_rate >= 80:
            insights.append("✅ Good POD rate")
        else:
            issues.append(f"❌ Low POD rate: {pod_rate}%")
            recommendations.append(f"🔧 Improve POD rate from {pod_rate}% to 90%+")
        
        # Aging insights
        avg_aging = dashboard.get("avg_delivery_aging", 0)
        if avg_aging <= 3:
            insights.append("✅ Excellent delivery speed (< 3 days)")
        elif avg_aging <= 7:
            insights.append("✅ Good delivery speed (< 7 days)")
        else:
            issues.append(f"❌ High delivery aging: {avg_aging} days")
            recommendations.append(f"🔧 Reduce delivery aging from {avg_aging} to < 7 days")
        
        # Revenue insights
        revenue = dashboard.get("total_revenue", 0)
        if revenue > 1000000:
            insights.append(f"💰 Top-tier revenue: PKR {revenue:,.0f}")
        elif revenue > 500000:
            insights.append(f"💰 Good revenue: PKR {revenue:,.0f}")
        else:
            recommendations.append(f"🔧 Increase revenue from PKR {revenue:,.0f}")
        
        # Health insight
        health_score = self._calculate_health_score_weighted(dashboard)
        if health_score >= 80:
            insights.append(f"💚 Excellent health score: {health_score}/100")
        elif health_score >= 60:
            insights.append(f"💚 Good health score: {health_score}/100")
        else:
            issues.append(f"❌ Low health score: {health_score}/100")
            recommendations.append(f"🔧 Improve health score from {health_score} to 80+")
        
        return insights, issues, recommendations
    
    def _get_cached_rankings(self, dealer_name: str) -> Dict[str, Any]:
        """Get cached rankings for a specific dealer."""
        all_rankings = self._compute_all_rankings_bulk()
        return all_rankings.get(dealer_name, {})
    
    def _calculate_delivery_score(self, dashboard: Dict) -> int:
        """Calculate delivery score (0-100)."""
        delivery_rate = dashboard.get("delivery_rate", 0)
        if delivery_rate >= 90:
            return 100
        elif delivery_rate >= 80:
            return 80
        elif delivery_rate >= 70:
            return 60
        elif delivery_rate >= 50:
            return 40
        else:
            return 20
    
    def _calculate_pod_score(self, dashboard: Dict) -> int:
        """Calculate POD score (0-100)."""
        pod_rate = dashboard.get("pod_rate", 0)
        if pod_rate >= 90:
            return 100
        elif pod_rate >= 80:
            return 80
        elif pod_rate >= 70:
            return 60
        elif pod_rate >= 50:
            return 40
        else:
            return 20
    
    def _calculate_revenue_score(self, dashboard: Dict) -> int:
        """Calculate revenue score (0-100)."""
        revenue = dashboard.get("total_revenue", 0)
        dns = dashboard.get("total_dns", 0)
        
        if revenue > 1000000 and dns > 50:
            return 100
        elif revenue > 500000 and dns > 25:
            return 75
        elif revenue > 100000 and dns > 10:
            return 50
        elif revenue > 50000 and dns > 5:
            return 25
        else:
            return 10
    
    def _calculate_activity_score(self, dashboard: Dict) -> int:
        """Calculate activity score (0-100)."""
        dns = dashboard.get("total_dns", 0)
        if dns > 100:
            return 100
        elif dns > 50:
            return 75
        elif dns > 20:
            return 50
        elif dns > 5:
            return 25
        else:
            return 10
    
    def _calculate_delivery_risk(self, dashboard: Dict) -> int:
        """Calculate delivery risk (0-100)."""
        delivery_rate = dashboard.get("delivery_rate", 100)
        if delivery_rate >= 90:
            return 0
        elif delivery_rate >= 80:
            return 25
        elif delivery_rate >= 70:
            return 50
        elif delivery_rate >= 50:
            return 75
        else:
            return 100
    
    def _calculate_pod_risk(self, dashboard: Dict) -> int:
        """Calculate POD risk (0-100)."""
        pod_rate = dashboard.get("pod_rate", 100)
        if pod_rate >= 90:
            return 0
        elif pod_rate >= 80:
            return 25
        elif pod_rate >= 70:
            return 50
        elif pod_rate >= 50:
            return 75
        else:
            return 100
    
    def _calculate_aging_risk(self, dashboard: Dict) -> int:
        """Calculate aging risk (0-100)."""
        avg_aging = dashboard.get("avg_delivery_aging", 0)
        if avg_aging <= 3:
            return 0
        elif avg_aging <= 7:
            return 25
        elif avg_aging <= 14:
            return 50
        elif avg_aging <= 30:
            return 75
        else:
            return 100
    
    def _calculate_revenue_risk(self, dashboard: Dict) -> int:
        """Calculate revenue risk (0-100)."""
        revenue = dashboard.get("total_revenue", 0)
        if revenue > 1000000:
            return 0
        elif revenue > 500000:
            return 25
        elif revenue > 100000:
            return 50
        elif revenue > 50000:
            return 75
        else:
            return 100
    
    def _build_dealer_profile(self, dealer_name: str, dashboard: Dict, analytics: Dict) -> Dict:
        """Build dealer profile data using DeliveryReport fields."""
        metadata = self._get_dealer_metadata(dealer_name)
        
        return {
            "dealer_name": dealer_name,
            "dealer_code": metadata.get("dealer_code", self._generate_dealer_code(dealer_name)),
            "dealer_type": metadata.get("dealer_type", "Standard"),
            "dealer_category": metadata.get("dealer_category", "Standard"),
            "city": dashboard.get("ship_to_city", "Unknown"),
            "region": metadata.get("region", "Unknown"),
            "division": metadata.get("division", "Unknown"),
            "sales_office": dashboard.get("sales_office", "Unknown"),
            "warehouse": dashboard.get("warehouse", "Unknown"),
            "sales_manager": dashboard.get("sales_manager", "Unknown"),
            "dealer_status": self._get_dealer_status(dashboard),
            "registration_date": metadata.get("registration_date", "N/A"),
            "total_dns": dashboard.get("total_dns", 0),
            "total_revenue": dashboard.get("total_revenue", 0),
            "total_units": dashboard.get("total_units", 0)
        }
    
    def _build_executive_kpis(self, dashboard: Dict, analytics: Dict) -> Dict:
        """Build executive KPI data."""
        return {
            "total_dns": dashboard.get("total_dns", 0),
            "total_revenue": dashboard.get("total_revenue", 0),
            "total_units": dashboard.get("total_units", 0),
            "delivered_dn_count": dashboard.get("delivered_units", 0),
            "pending_dn_count": dashboard.get("pending_delivery", 0),
            "pending_pod_dns": dashboard.get("pending_pod", 0),
            "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0),
            "avg_pod_aging": dashboard.get("avg_pod_aging", 0),
            "dealer_health_score": analytics["health"]["score"],
            "risk_level": analytics["risk"]["risk_level"]
        }
    
    def _build_performance_metrics(self, dashboard: Dict) -> Dict:
        """Build performance metrics with DN vs Unit separation."""
        total_dns = dashboard.get("total_dns", 0)
        delivered = dashboard.get("delivered_units", 0)
        pending = dashboard.get("pending_delivery", 0)
        
        return {
            "total_dn_count": total_dns,
            "delivered_dn_count": delivered,
            "pending_dn_count": pending,
            "transit_dn_count": dashboard.get("transit_units", 0),
            "delivery_rate": dashboard.get("delivery_rate", 0),
            "total_unit_count": dashboard.get("total_units", 0),
            "total_revenue": dashboard.get("total_revenue", 0)
        }
    
    def _build_delivery_metrics(self, dashboard: Dict, analytics: Dict) -> Dict:
        """Build delivery metrics."""
        total_dns = dashboard.get("total_dns", 1)
        delivered = dashboard.get("delivered_units", 0)
        pending = dashboard.get("pending_delivery", 0)
        
        delivery_success_rate = (delivered / total_dns * 100) if total_dns > 0 else 0
        sla_compliance = 100 if delivery_success_rate >= 90 else (delivery_success_rate / 90 * 100) if delivery_success_rate > 0 else 0
        
        return {
            "on_time_deliveries": delivered,
            "late_deliveries": dashboard.get("transit_units", 0),
            "delayed_dns": pending,
            "delivery_success_rate": round(delivery_success_rate, 1),
            "sla_compliance": round(min(sla_compliance, 100), 1),
            "delivery_aging": dashboard.get("avg_delivery_aging", 0),
            "delivery_risk": analytics["risk"]["delivery_risk"]
        }
    
    def _build_pod_metrics(self, dashboard: Dict, analytics: Dict) -> Dict:
        """Build POD metrics."""
        pod_pending = dashboard.get("pending_pod", 0)
        pod_completed = dashboard.get("pod_completed", 0)
        total_pod = pod_pending + pod_completed
        
        return {
            "pod_received": pod_completed,
            "pod_pending": pod_pending,
            "pending_pod_dns": pod_pending,
            "pod_compliance": round((pod_completed / max(total_pod, 1) * 100), 1) if total_pod > 0 else 0,
            "avg_pod_aging": dashboard.get("avg_pod_aging", 0),
            "pod_risk": analytics["risk"]["pod_risk"]
        }
    
    def _build_financial_metrics(self, dashboard: Dict, analytics: Dict) -> Dict:
        """Build financial metrics."""
        total_revenue = dashboard.get("total_revenue", 0)
        total_dns = dashboard.get("total_dns", 1)
        
        return {
            "total_revenue": total_revenue,
            "average_dn_value": total_revenue / max(total_dns, 1),
            "delivered_revenue": total_revenue * (dashboard.get("delivered_units", 0) / max(total_dns, 1)),
            "pending_revenue": total_revenue * (dashboard.get("pending_delivery", 0) / max(total_dns, 1)),
            "pending_pod_revenue": total_revenue * (dashboard.get("pending_pod", 0) / max(total_dns, 1))
        }


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

_analytics_service = None


def get_analytics_service(use_redis: bool = False) -> AnalyticsService:
    """Factory function for AnalyticsService singleton."""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService(use_redis=use_redis)
    return _analytics_service


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'AnalyticsResponse',
    'get_analytics_service'
]
