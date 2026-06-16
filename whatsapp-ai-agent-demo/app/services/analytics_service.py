# ==========================================================
# FILE: app/services/analytics_service.py (v2.0 - ENTERPRISE BI LAYER)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Aggregation, Calculation, Insight
# ARCHITECTURE: Pure analytics - no SQL, no AI, no routing
#
# RESPONSIBILITIES:
# - Dealer Analytics (dashboard, performance, benchmark, trend, DNS)
# - Warehouse Analytics (dashboard, performance, benchmark, trend)
# - City Analytics (dashboard, performance, benchmark, trend)
# - Network KPIs (aggregated metrics across entire network)
# - Risk Engine (scoring, status, factors)
# - Root Cause Analysis (data-driven issue identification)
# - Executive Intelligence (top risks, opportunities, recommendations)
# - Control Tower (critical deliveries, alerts, snapshots)
# - Ranking Engine (top/bottom dealers, warehouses, cities)
# - Data Quality Intelligence (validation, flagging)
# - Recommendation Engine (rule-based, deterministic)
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
from collections import defaultdict
from statistics import mean, stdev

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


# ==========================================================
# RESPONSE CLASSES
# ==========================================================

class AnalyticsResponse:
    """Standardized analytics response structure."""
    
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, errors: List[str] = None):
        self.success = success
        self.data = data or {}
        self.errors = errors or []
        self.generated_at = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "errors": self.errors,
            "generated_at": self.generated_at
        }


# ==========================================================
# ANALYTICS SERVICE
# ==========================================================

class AnalyticsService:
    """
    ENTERPRISE BUSINESS INTELLIGENCE LAYER
    
    This service provides pure analytics calculations, aggregations,
    and insights. It does NOT:
    - Execute SQL queries
    - Call external APIs
    - Contain AI prompts
    - Route requests
    - Format responses
    
    All methods return standardized AnalyticsResponse objects.
    """
    
    def __init__(self):
        """Initialize AnalyticsService with dependencies."""
        self._start_time = time.time()
        
        # Dependencies
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.today = datetime.now().date()
        
        # Cache
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._cache_duration = timedelta(minutes=5)
        
        logger.info("AnalyticsService v2.0 initialized")
        logger.info("  - Dealer Analytics: Enabled")
        logger.info("  - Warehouse Analytics: Enabled")
        logger.info("  - City Analytics: Enabled")
        logger.info("  - Network KPIs: Enabled")
        logger.info("  - Risk Engine: Enabled")
        logger.info("  - Root Cause Analysis: Enabled")
        logger.info("  - Executive Intelligence: Enabled")
        logger.info("  - Control Tower: Enabled")
        logger.info("  - Ranking Engine: Enabled")
        logger.info("  - Data Quality: Enabled")
        logger.info("  - Recommendation Engine: Enabled")
    
    def close(self):
        """Close dependencies."""
        self.logistics.close()
        self.kpi.close()
        logger.info("AnalyticsService closed")
    
    # ==========================================================
    # CACHE MANAGEMENT
    # ==========================================================
    
    def _get_cached(self, key: str) -> Optional[Any]:
        """Get cached value if valid."""
        if key in self._cache and key in self._cache_ttl:
            if datetime.now() < self._cache_ttl[key]:
                return self._cache[key]
        return None
    
    def _set_cache(self, key: str, value: Any, ttl_seconds: int = 300):
        """Set cache with TTL."""
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now() + timedelta(seconds=ttl_seconds)
    
    def _invalidate_cache(self):
        """Clear all cache."""
        self._cache.clear()
        self._cache_ttl.clear()
    
    # ==========================================================
    # 1. DEALER ANALYTICS
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get comprehensive dealer dashboard with analytics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with dashboard data and analytics
        """
        method_start = time.time()
        logger.info(f"Dealer dashboard requested: {dealer_name}")
        
        try:
            # Get raw data
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Add KPI summary
            kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
            if "error" not in kpi:
                dashboard["kpi_summary"] = kpi
            
            # Add aging details
            aging = self.logistics.get_dealer_aging_data(dealer_name)
            if aging:
                dashboard["aging_details"] = aging
            
            # Add benchmark
            dashboard["benchmark"] = self._get_dealer_benchmark(dealer_name, dashboard)
            
            # Add trend
            dashboard["trend"] = self._get_dealer_trend(dealer_name)
            
            # Add risk assessment
            dashboard["risk_assessment"] = self._assess_risk(dashboard)
            
            # Add recommendations
            dashboard["recommendations"] = self._generate_dealer_recommendations(dashboard)
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"Dealer dashboard completed in {duration:.2f}ms")
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Dealer dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer performance metrics with analytics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with performance data
        """
        method_start = time.time()
        logger.info(f"Dealer performance requested: {dealer_name}")
        
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Get KPI summary
            kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
            risk_status = kpi.get("risk_status", "unknown") if "error" not in kpi else "unknown"
            
            # Calculate performance score
            performance_score = self._calculate_performance_score(dashboard)
            
            # Get benchmark
            benchmark = self._get_dealer_benchmark(dealer_name, dashboard)
            
            return {
                "dealer_name": dealer_name,
                "summary": {
                    "revenue": dashboard.get("total_revenue", 0.0),
                    "units": dashboard.get("total_units", 0),
                    "dns": dashboard.get("total_dns", 0),
                    "delivery_rate": dashboard.get("delivery_rate", 0.0),
                    "pod_rate": dashboard.get("pod_rate", 0.0),
                    "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0.0),
                    "avg_pod_aging": dashboard.get("avg_pod_aging", 0.0)
                },
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score),
                "risk_status": risk_status,
                "risk_emoji": self.schema.get_risk_emoji(risk_status),
                "benchmark": benchmark,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Dealer performance failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_dns(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        """Get dealer DNS list."""
        return self.logistics.get_dealer_dns(dealer_name, limit)
    
    def get_dealer_benchmark(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer benchmark against network averages.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with benchmark data
        """
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            return self._get_dealer_benchmark(dealer_name, dashboard)
            
        except Exception as e:
            logger.error(f"Dealer benchmark failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_trend(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer trend analysis.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with trend data
        """
        try:
            return self._get_dealer_trend(dealer_name)
        except Exception as e:
            logger.error(f"Dealer trend failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 2. WAREHOUSE ANALYTICS
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """
        Get comprehensive warehouse dashboard with analytics.
        
        Args:
            warehouse_name: Full warehouse name
            
        Returns:
            Dict with dashboard data and analytics
        """
        method_start = time.time()
        logger.info(f"Warehouse dashboard requested: {warehouse_name}")
        
        try:
            dashboard = self.logistics.get_warehouse_dashboard_data(warehouse_name)
            if not dashboard:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            # Add KPI summary
            kpi = self.kpi.get_warehouse_kpi_summary(warehouse_name)
            if "error" not in kpi:
                dashboard["kpi_summary"] = kpi
            
            # Add benchmark
            dashboard["benchmark"] = self._get_warehouse_benchmark(warehouse_name)
            
            # Add trend
            dashboard["trend"] = self._get_warehouse_trend(warehouse_name)
            
            # Add risk assessment
            dashboard["risk_assessment"] = self._assess_warehouse_risk(dashboard)
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"Warehouse dashboard completed in {duration:.2f}ms")
            
            return dashboard
            
        except Exception as e:
            logger.error(f"Warehouse dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
        """
        Get warehouse performance metrics.
        
        Args:
            warehouse_name: Full warehouse name
            
        Returns:
            Dict with performance data
        """
        try:
            kpi = self.kpi.get_warehouse_kpi_summary(warehouse_name)
            if "error" in kpi:
                return {"error": kpi["error"]}
            
            # Calculate performance score
            performance_score = self._calculate_warehouse_performance_score(kpi)
            
            return {
                "warehouse_name": warehouse_name,
                "summary": {
                    "total_dns": kpi.get("total_dns", 0),
                    "total_units": kpi.get("total_units", 0),
                    "total_revenue": kpi.get("total_revenue", 0.0),
                    "pgi_rate": kpi.get("pgi_rate", 0.0),
                    "pod_rate": kpi.get("pod_rate", 0.0),
                    "pending_delivery": kpi.get("pending_delivery", 0),
                    "pending_pod": kpi.get("pending_pod", 0)
                },
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Warehouse performance failed: {e}")
            return {"error": str(e)}
    
    def get_warehouse_benchmark(self, warehouse_name: str) -> Dict[str, Any]:
        """
        Get warehouse benchmark against network averages.
        
        Args:
            warehouse_name: Full warehouse name
            
        Returns:
            Dict with benchmark data
        """
        try:
            return self._get_warehouse_benchmark(warehouse_name)
        except Exception as e:
            logger.error(f"Warehouse benchmark failed: {e}")
            return {"error": str(e)}
    
    def get_warehouse_trend(self, warehouse_name: str) -> Dict[str, Any]:
        """
        Get warehouse trend analysis.
        
        Args:
            warehouse_name: Full warehouse name
            
        Returns:
            Dict with trend data
        """
        try:
            return self._get_warehouse_trend(warehouse_name)
        except Exception as e:
            logger.error(f"Warehouse trend failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 3. CITY ANALYTICS
    # ==========================================================
    
    def get_city_dashboard(self, city_name: str) -> Dict[str, Any]:
        """
        Get comprehensive city dashboard with analytics.
        
        Args:
            city_name: Full city name
            
        Returns:
            Dict with dashboard data and analytics
        """
        method_start = time.time()
        logger.info(f"City dashboard requested: {city_name}")
        
        try:
            # Get city data from logistics service
            city_data = self.logistics.get_city_dashboard_data(city_name)
            if not city_data:
                return {"error": f"City '{city_name}' not found"}
            
            # Add benchmark
            city_data["benchmark"] = self._get_city_benchmark(city_name)
            
            # Add trend
            city_data["trend"] = self._get_city_trend(city_name)
            
            # Add risk assessment
            city_data["risk_assessment"] = self._assess_city_risk(city_data)
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"City dashboard completed in {duration:.2f}ms")
            
            return city_data
            
        except Exception as e:
            logger.error(f"City dashboard failed: {e}")
            return {"error": str(e)}
    
    def get_city_performance(self, city_name: str) -> Dict[str, Any]:
        """
        Get city performance metrics.
        
        Args:
            city_name: Full city name
            
        Returns:
            Dict with performance data
        """
        try:
            city_data = self.logistics.get_city_dashboard_data(city_name)
            if not city_data:
                return {"error": f"City '{city_name}' not found"}
            
            performance_score = self._calculate_city_performance_score(city_data)
            
            return {
                "city_name": city_name,
                "summary": {
                    "total_dns": city_data.get("total_dns", 0),
                    "total_revenue": city_data.get("total_revenue", 0.0),
                    "total_units": city_data.get("total_units", 0),
                    "delivery_rate": city_data.get("delivery_rate", 0.0),
                    "pod_rate": city_data.get("pod_rate", 0.0),
                    "pending_dns": city_data.get("pending_dns", 0)
                },
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"City performance failed: {e}")
            return {"error": str(e)}
    
    def get_city_benchmark(self, city_name: str) -> Dict[str, Any]:
        """
        Get city benchmark against network averages.
        
        Args:
            city_name: Full city name
            
        Returns:
            Dict with benchmark data
        """
        try:
            return self._get_city_benchmark(city_name)
        except Exception as e:
            logger.error(f"City benchmark failed: {e}")
            return {"error": str(e)}
    
    def get_city_trend(self, city_name: str) -> Dict[str, Any]:
        """
        Get city trend analysis.
        
        Args:
            city_name: Full city name
            
        Returns:
            Dict with trend data
        """
        try:
            return self._get_city_trend(city_name)
        except Exception as e:
            logger.error(f"City trend failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 4. NETWORK KPIs
    # ==========================================================
    
    def get_network_kpis(self) -> Dict[str, Any]:
        """
        Get aggregated network KPIs.
        
        Returns:
            Dict with network KPI data
        """
        method_start = time.time()
        logger.info("Network KPIs requested")
        
        try:
            cache_key = "network_kpis"
            cached = self._get_cached(cache_key)
            if cached:
                return cached
            
            # Get all KPIs from KPI service
            all_kpis = self.kpi.get_all_kpi_summary()
            
            if not all_kpis:
                return {"error": "Unable to retrieve network KPIs"}
            
            # Calculate network averages
            network_kpis = self._calculate_network_kpis(all_kpis)
            
            self._set_cache(cache_key, network_kpis)
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"Network KPIs completed in {duration:.2f}ms")
            
            return network_kpis
            
        except Exception as e:
            logger.error(f"Network KPIs failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 5. RISK ENGINE
    # ==========================================================
    
    def calculate_risk_score(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate risk score from data.
        
        Args:
            data: Dictionary with metrics
            
        Returns:
            Dict with risk score and assessment
        """
        try:
            risk_factors = []
            risk_score = 0
            
            # Check delivery rate
            delivery_rate = data.get("delivery_rate", 100)
            if delivery_rate < 70:
                risk_factors.append(f"Low delivery rate: {delivery_rate}%")
                risk_score += 30
            elif delivery_rate < 85:
                risk_factors.append(f"Moderate delivery rate: {delivery_rate}%")
                risk_score += 15
            
            # Check POD rate
            pod_rate = data.get("pod_rate", 100)
            if pod_rate < 70:
                risk_factors.append(f"Low POD rate: {pod_rate}%")
                risk_score += 30
            elif pod_rate < 85:
                risk_factors.append(f"Moderate POD rate: {pod_rate}%")
                risk_score += 15
            
            # Check aging
            avg_delivery_aging = data.get("avg_delivery_aging", 0)
            if avg_delivery_aging > 15:
                risk_factors.append(f"High delivery aging: {avg_delivery_aging} days")
                risk_score += 20
            elif avg_delivery_aging > 7:
                risk_factors.append(f"Moderate delivery aging: {avg_delivery_aging} days")
                risk_score += 10
            
            avg_pod_aging = data.get("avg_pod_aging", 0)
            if avg_pod_aging > 15:
                risk_factors.append(f"High POD aging: {avg_pod_aging} days")
                risk_score += 20
            elif avg_pod_aging > 7:
                risk_factors.append(f"Moderate POD aging: {avg_pod_aging} days")
                risk_score += 10
            
            # Cap risk score
            risk_score = min(risk_score, 100)
            
            # Get status and emoji
            risk_status = self.schema.get_risk_status(risk_score)
            risk_emoji = self.schema.get_risk_emoji(risk_status)
            
            return {
                "risk_score": risk_score,
                "risk_status": risk_status,
                "risk_emoji": risk_emoji,
                "risk_factors": risk_factors,
                "risk_count": len(risk_factors),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Risk calculation failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 6. ROOT CAUSE ANALYSIS
    # ==========================================================
    
    def analyze_root_cause(self) -> Dict[str, Any]:
        """
        Perform root cause analysis.
        
        Returns:
            Dict with root cause analysis
        """
        method_start = time.time()
        logger.info("Root cause analysis requested")
        
        try:
            # Get network KPIs
            network = self.get_network_kpis()
            if "error" in network:
                return {"error": "Unable to perform root cause analysis"}
            
            # Identify issues
            issues = []
            affected_dealers = []
            affected_warehouses = []
            affected_cities = []
            
            # Check pending PGI
            if network.get("pending_pgi", 0) > 50:
                issues.append({
                    "issue": "High PGI backlog",
                    "severity": "critical",
                    "details": f"{network['pending_pgi']} deliveries awaiting PGI"
                })
                affected_warehouses = self._get_warehouses_with_high_pending_pgi()
            
            # Check pending POD
            if network.get("pending_pod", 0) > 100:
                issues.append({
                    "issue": "High POD backlog",
                    "severity": "critical",
                    "details": f"{network['pending_pod']} deliveries awaiting POD"
                })
                affected_dealers = self._get_dealers_with_high_pending_pod()
                affected_cities = self._get_cities_with_high_pending_pod()
            
            # Check delivery aging
            if network.get("avg_delivery_aging", 0) > 10:
                issues.append({
                    "issue": "High delivery aging",
                    "severity": "high",
                    "details": f"Average delivery aging: {network['avg_delivery_aging']} days"
                })
                affected_dealers = self._get_dealers_with_high_aging()
                affected_warehouses = self._get_warehouses_with_high_aging()
            
            # Check POD aging
            if network.get("avg_pod_aging", 0) > 10:
                issues.append({
                    "issue": "High POD aging",
                    "severity": "high",
                    "details": f"Average POD aging: {network['avg_pod_aging']} days"
                })
                affected_dealers = self._get_dealers_with_high_pod_aging()
            
            # Determine primary issue
            primary_issue = max(issues, key=lambda x: 3 if x["severity"] == "critical" else 2 if x["severity"] == "high" else 1) if issues else None
            
            # Generate recommendations
            recommendations = self._generate_root_cause_recommendations(issues)
            
            result = {
                "primary_issue": primary_issue,
                "issues": issues,
                "affected_dealers": affected_dealers[:5] if affected_dealers else [],
                "affected_warehouses": affected_warehouses[:5] if affected_warehouses else [],
                "affected_cities": affected_cities[:5] if affected_cities else [],
                "root_causes": self._identify_root_causes(issues),
                "recommendations": recommendations,
                "severity": "critical" if any(i["severity"] == "critical" for i in issues) else "high" if issues else "low",
                "generated_at": datetime.now().isoformat()
            }
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"Root cause analysis completed in {duration:.2f}ms")
            
            return result
            
        except Exception as e:
            logger.error(f"Root cause analysis failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 7. EXECUTIVE INTELLIGENCE
    # ==========================================================
    
    def get_executive_dashboard(self) -> Dict[str, Any]:
        """
        Get executive dashboard with comprehensive insights.
        
        Returns:
            Dict with executive insights
        """
        method_start = time.time()
        logger.info("Executive dashboard requested")
        
        try:
            # Get base insights
            insights = self.logistics.get_executive_insights_data()
            if not insights:
                return {"error": "Unable to retrieve executive insights"}
            
            # Add network KPIs
            network = self.get_network_kpis()
            if "error" not in network:
                insights["network_kpis"] = network
            
            # Add risk assessment
            insights["risk_assessment"] = self._assess_global_risk(insights)
            
            # Add executive insights
            insights["executive_insights"] = self._generate_executive_insights(insights)
            
            # Add recommendations
            insights["recommendations"] = self._generate_executive_recommendations(insights)
            
            duration = (time.time() - method_start) * 1000
            logger.info(f"Executive dashboard completed in {duration:.2f}ms")
            
            return insights
            
        except Exception as e:
            logger.error(f"Executive dashboard failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 8. CONTROL TOWER
    # ==========================================================
    
    def get_control_tower_snapshot(self, threshold_days: int = 15) -> Dict[str, Any]:
        """
        Get control tower snapshot with critical alerts.
        
        Args:
            threshold_days: Days threshold for critical deliveries
            
        Returns:
            Dict with control tower data
        """
        method_start = time.time()
        logger.info(f"Control tower snapshot requested (threshold: {threshold_days} days)")
        
        try:
            # Get critical deliveries
            critical = self.logistics.get_critical_deliveries(threshold_days)
            
            # Get network KPIs
            network = self.get_network_kpis()
            
            # Generate alerts
            alerts = self._generate_control_tower_alerts(network, critical)
            
            return {
                "critical_deliveries": critical[:5] if critical else [],
                "total_critical": len(critical) if critical else 0,
                "pending_pgi": network.get("pending_pgi", 0) if "error" not in network else 0,
                "pending_pod": network.get("pending_pod", 0) if "error" not in network else 0,
                "alerts": alerts,
                "threshold_days": threshold_days,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Control tower snapshot failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 9. RANKING ENGINE
    # ==========================================================
    
    def get_top_dealers(self, metric: str = "revenue", limit: int = 10) -> List[Dict]:
        """
        Get top dealers by metric.
        
        Args:
            metric: 'revenue', 'units', 'delivery_rate', 'pod_rate'
            limit: Number of dealers to return
            
        Returns:
            List of dealer rankings
        """
        try:
            if metric == "revenue":
                return self.logistics.get_top_dealers_by_revenue(limit)
            elif metric == "units":
                return self.logistics.get_top_dealers_by_units(limit)
            elif metric == "delivery_rate":
                return self._get_top_dealers_by_delivery_rate(limit)
            elif metric == "pod_rate":
                return self._get_top_dealers_by_pod_rate(limit)
            return []
            
        except Exception as e:
            logger.error(f"Top dealers failed: {e}")
            return []
    
    def get_top_warehouses(self, limit: int = 10) -> List[Dict]:
        """Get top warehouses by pending deliveries."""
        try:
            return self.logistics.get_top_warehouses_by_pending(limit)
        except Exception as e:
            logger.error(f"Top warehouses failed: {e}")
            return []
    
    def get_bottom_dealers(self, metric: str = "delivery_rate", limit: int = 10) -> List[Dict]:
        """
        Get bottom dealers by metric.
        
        Args:
            metric: 'delivery_rate', 'pod_rate', 'revenue', 'units'
            limit: Number of dealers to return
            
        Returns:
            List of dealer rankings
        """
        try:
            return self._get_bottom_dealers(metric, limit)
        except Exception as e:
            logger.error(f"Bottom dealers failed: {e}")
            return []
    
    def get_top_cities(self, metric: str = "revenue", limit: int = 10) -> List[Dict]:
        """
        Get top cities by metric.
        
        Args:
            metric: 'revenue', 'units', 'delivery_rate'
            limit: Number of cities to return
            
        Returns:
            List of city rankings
        """
        try:
            return self._get_top_cities(metric, limit)
        except Exception as e:
            logger.error(f"Top cities failed: {e}")
            return []
    
    def get_bottom_cities(self, metric: str = "delivery_rate", limit: int = 10) -> List[Dict]:
        """
        Get bottom cities by metric.
        
        Args:
            metric: 'delivery_rate', 'pod_rate', 'revenue'
            limit: Number of cities to return
            
        Returns:
            List of city rankings
        """
        try:
            return self._get_bottom_cities(metric, limit)
        except Exception as e:
            logger.error(f"Bottom cities failed: {e}")
            return []
    
    # ==========================================================
    # 10. DATA QUALITY INTELLIGENCE
    # ==========================================================
    
    def get_data_quality_report(self) -> Dict[str, Any]:
        """
        Get data quality report.
        
        Returns:
            Dict with data quality metrics
        """
        method_start = time.time()
        logger.info("Data quality report requested")
        
        try:
            # Get data quality from logistics service
            quality = self.logistics.get_data_quality_metrics()
            
            if not quality:
                return {"error": "Unable to retrieve data quality metrics"}
            
            return {
                "records_checked": quality.get("total_records", 0),
                "valid_dates": quality.get("valid_dates", 0),
                "invalid_dates": quality.get("invalid_dates", 0),
                "missing_pgi": quality.get("missing_pgi", 0),
                "missing_pod": quality.get("missing_pod", 0),
                "negative_aging": quality.get("negative_aging", 0),
                "data_quality_score": quality.get("quality_score", 100),
                "quality_status": self._get_quality_status(quality.get("quality_score", 100)),
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Data quality report failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # PRIVATE METHODS - BENCHMARKS
    # ==========================================================
    
    def _get_dealer_benchmark(self, dealer_name: str, dealer_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate dealer benchmark against network."""
        try:
            network = self.get_network_kpis()
            if "error" in network:
                return {}
            
            return {
                "dealer_revenue": dealer_data.get("total_revenue", 0),
                "network_avg_revenue": network.get("avg_revenue", 0),
                "revenue_gap": dealer_data.get("total_revenue", 0) - network.get("avg_revenue", 0),
                "dealer_units": dealer_data.get("total_units", 0),
                "network_avg_units": network.get("avg_units", 0),
                "units_gap": dealer_data.get("total_units", 0) - network.get("avg_units", 0),
                "dealer_delivery_rate": dealer_data.get("delivery_rate", 0),
                "network_delivery_rate": network.get("avg_delivery_rate", 0),
                "delivery_rate_gap": dealer_data.get("delivery_rate", 0) - network.get("avg_delivery_rate", 0),
                "performance_gap": self._calculate_performance_gap(dealer_data, network)
            }
            
        except Exception as e:
            logger.error(f"Dealer benchmark calculation failed: {e}")
            return {}
    
    def _get_warehouse_benchmark(self, warehouse_name: str) -> Dict[str, Any]:
        """Calculate warehouse benchmark against network."""
        try:
            network = self.get_network_kpis()
            if "error" in network:
                return {}
            
            warehouse_data = self.logistics.get_warehouse_dashboard_data(warehouse_name)
            if not warehouse_data:
                return {}
            
            return {
                "warehouse_pgi_rate": warehouse_data.get("pgi_rate", 0),
                "network_avg_pgi_rate": network.get("avg_pgi_rate", 0),
                "pgi_rate_gap": warehouse_data.get("pgi_rate", 0) - network.get("avg_pgi_rate", 0),
                "warehouse_pod_rate": warehouse_data.get("pod_rate", 0),
                "network_avg_pod_rate": network.get("avg_pod_rate", 0),
                "pod_rate_gap": warehouse_data.get("pod_rate", 0) - network.get("avg_pod_rate", 0)
            }
            
        except Exception as e:
            logger.error(f"Warehouse benchmark calculation failed: {e}")
            return {}
    
    def _get_city_benchmark(self, city_name: str) -> Dict[str, Any]:
        """Calculate city benchmark against network."""
        try:
            network = self.get_network_kpis()
            if "error" in network:
                return {}
            
            city_data = self.logistics.get_city_dashboard_data(city_name)
            if not city_data:
                return {}
            
            return {
                "city_delivery_rate": city_data.get("delivery_rate", 0),
                "network_avg_delivery_rate": network.get("avg_delivery_rate", 0),
                "delivery_rate_gap": city_data.get("delivery_rate", 0) - network.get("avg_delivery_rate", 0),
                "city_pod_rate": city_data.get("pod_rate", 0),
                "network_avg_pod_rate": network.get("avg_pod_rate", 0),
                "pod_rate_gap": city_data.get("pod_rate", 0) - network.get("avg_pod_rate", 0)
            }
            
        except Exception as e:
            logger.error(f"City benchmark calculation failed: {e}")
            return {}
    
    # ==========================================================
    # PRIVATE METHODS - TRENDS
    # ==========================================================
    
    def _get_dealer_trend(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer trend analysis."""
        try:
            # Get historical data from logistics
            historical = self.logistics.get_dealer_historical_data(dealer_name)
            if not historical:
                return {}
            
            current = historical[-1] if historical else {}
            previous = historical[-2] if len(historical) > 1 else {}
            
            return {
                "current_period": current,
                "previous_period": previous,
                "growth_percent": self._calculate_growth_percent(current, previous),
                "trend": self._determine_trend(current, previous),
                "data_points": len(historical)
            }
            
        except Exception as e:
            logger.error(f"Dealer trend calculation failed: {e}")
            return {}
    
    def _get_warehouse_trend(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse trend analysis."""
        try:
            historical = self.logistics.get_warehouse_historical_data(warehouse_name)
            if not historical:
                return {}
            
            current = historical[-1] if historical else {}
            previous = historical[-2] if len(historical) > 1 else {}
            
            return {
                "current_period": current,
                "previous_period": previous,
                "growth_percent": self._calculate_growth_percent(current, previous),
                "trend": self._determine_trend(current, previous),
                "data_points": len(historical)
            }
            
        except Exception as e:
            logger.error(f"Warehouse trend calculation failed: {e}")
            return {}
    
    def _get_city_trend(self, city_name: str) -> Dict[str, Any]:
        """Get city trend analysis."""
        try:
            historical = self.logistics.get_city_historical_data(city_name)
            if not historical:
                return {}
            
            current = historical[-1] if historical else {}
            previous = historical[-2] if len(historical) > 1 else {}
            
            return {
                "current_period": current,
                "previous_period": previous,
                "growth_percent": self._calculate_growth_percent(current, previous),
                "trend": self._determine_trend(current, previous),
                "data_points": len(historical)
            }
            
        except Exception as e:
            logger.error(f"City trend calculation failed: {e}")
            return {}
    
    # ==========================================================
    # PRIVATE METHODS - NETWORK KPIs
    # ==========================================================
    
    def _calculate_network_kpis(self, all_kpis: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate network KPIs from all KPI data."""
        try:
            totals = {
                "total_dns": 0,
                "total_revenue": 0.0,
                "total_units": 0,
                "pending_pgi": 0,
                "pending_pod": 0,
                "total_delivery_aging": 0,
                "total_pod_aging": 0,
                "count": 0
            }
            
            # Aggregate all KPIs
            for key, kpi in all_kpis.items():
                if isinstance(kpi, dict):
                    totals["total_dns"] += kpi.get("total_dns", 0)
                    totals["total_revenue"] += kpi.get("total_revenue", 0.0)
                    totals["total_units"] += kpi.get("total_units", 0)
                    totals["pending_pgi"] += kpi.get("pending_delivery", 0) or kpi.get("pending_pgi", 0)
                    totals["pending_pod"] += kpi.get("pending_pod", 0)
                    totals["total_delivery_aging"] += kpi.get("avg_delivery_aging", 0)
                    totals["total_pod_aging"] += kpi.get("avg_pod_aging", 0)
                    totals["count"] += 1
            
            # Calculate averages
            count = totals["count"] or 1
            avg_delivery_aging = totals["total_delivery_aging"] / count
            avg_pod_aging = totals["total_pod_aging"] / count
            
            return {
                "total_dns": totals["total_dns"],
                "total_revenue": round(totals["total_revenue"], 2),
                "total_units": totals["total_units"],
                "avg_revenue": round(totals["total_revenue"] / count, 2) if count > 0 else 0,
                "avg_units": totals["total_units"] / count if count > 0 else 0,
                "pending_pgi": totals["pending_pgi"],
                "pending_pod": totals["pending_pod"],
                "avg_delivery_aging": round(avg_delivery_aging, 1),
                "avg_pod_aging": round(avg_pod_aging, 1),
                "avg_delivery_rate": self._calculate_network_average("delivery_rate", all_kpis),
                "avg_pod_rate": self._calculate_network_average("pod_rate", all_kpis),
                "avg_pgi_rate": self._calculate_network_average("pgi_rate", all_kpis),
                "entities_analyzed": count,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Network KPI calculation failed: {e}")
            return {}
    
    def _calculate_network_average(self, metric: str, all_kpis: Dict[str, Any]) -> float:
        """Calculate network average for a specific metric."""
        values = []
        for kpi in all_kpis.values():
            if isinstance(kpi, dict) and metric in kpi:
                values.append(kpi[metric])
        
        if not values:
            return 0.0
        
        return round(sum(values) / len(values), 1)
    
    # ==========================================================
    # PRIVATE METHODS - RISK ASSESSMENT
    # ==========================================================
    
    def _assess_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for a single entity."""
        return self.calculate_risk_score(data)
    
    def _assess_global_risk(self, insights: Dict[str, Any]) -> Dict[str, Any]:
        """Assess global risk from insights."""
        risk_factors = []
        risk_score = 0
        
        if insights.get("pending_pgi", 0) > 50:
            risk_factors.append("High PGI backlog")
            risk_score += 30
        
        if insights.get("pending_pod", 0) > 100:
            risk_factors.append("High POD backlog")
            risk_score += 30
        
        if insights.get("avg_delivery_aging", 0) > 10:
            risk_factors.append("High delivery aging")
            risk_score += 20
        
        if insights.get("avg_pod_aging", 0) > 10:
            risk_factors.append("High POD aging")
            risk_score += 20
        
        risk_score = min(risk_score, 100)
        risk_status = self.schema.get_risk_status(risk_score)
        risk_emoji = self.schema.get_risk_emoji(risk_status)
        
        return {
            "risk_score": risk_score,
            "risk_status": risk_status,
            "risk_emoji": risk_emoji,
            "risk_factors": risk_factors,
            "risk_count": len(risk_factors),
            "generated_at": datetime.now().isoformat()
        }
    
    def _assess_warehouse_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for warehouse."""
        return self.calculate_risk_score(data)
    
    def _assess_city_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for city."""
        return self.calculate_risk_score(data)
    
    # ==========================================================
    # PRIVATE METHODS - PERFORMANCE SCORING
    # ==========================================================
    
    def _calculate_performance_score(self, data: Dict[str, Any]) -> int:
        """Calculate performance score (0-100)."""
        score = 0
        
        # Delivery rate (max 30 points)
        delivery_rate = data.get("delivery_rate", 0)
        score += min(delivery_rate * 0.3, 30)
        
        # POD rate (max 30 points)
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        # Delivery aging (max 20 points)
        avg_aging = data.get("avg_delivery_aging", 0)
        if avg_aging <= 3:
            score += 20
        elif avg_aging <= 7:
            score += 15
        elif avg_aging <= 15:
            score += 10
        else:
            score += 5
        
        # Volume (max 20 points)
        total_dns = data.get("total_dns", 0)
        if total_dns > 100:
            score += 20
        elif total_dns > 50:
            score += 15
        elif total_dns > 20:
            score += 10
        else:
            score += 5
        
        return min(int(score), 100)
    
    def _calculate_warehouse_performance_score(self, data: Dict[str, Any]) -> int:
        """Calculate warehouse performance score."""
        score = 0
        
        # PGI rate (max 30 points)
        pgi_rate = data.get("pgi_rate", 0)
        score += min(pgi_rate * 0.3, 30)
        
        # POD rate (max 30 points)
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        # Pending (max 20 points)
        pending = data.get("pending_delivery", 0)
        if pending == 0:
            score += 20
        elif pending < 10:
            score += 15
        elif pending < 50:
            score += 10
        else:
            score += 5
        
        # Volume (max 20 points)
        total_dns = data.get("total_dns", 0)
        if total_dns > 100:
            score += 20
        elif total_dns > 50:
            score += 15
        elif total_dns > 20:
            score += 10
        else:
            score += 5
        
        return min(int(score), 100)
    
    def _calculate_city_performance_score(self, data: Dict[str, Any]) -> int:
        """Calculate city performance score."""
        score = 0
        
        # Delivery rate (max 30 points)
        delivery_rate = data.get("delivery_rate", 0)
        score += min(delivery_rate * 0.3, 30)
        
        # POD rate (max 30 points)
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        # Pending (max 20 points)
        pending = data.get("pending_dns", 0)
        if pending == 0:
            score += 20
        elif pending < 10:
            score += 15
        elif pending < 50:
            score += 10
        else:
            score += 5
        
        # Volume (max 20 points)
        total_dns = data.get("total_dns", 0)
        if total_dns > 100:
            score += 20
        elif total_dns > 50:
            score += 15
        elif total_dns > 20:
            score += 10
        else:
            score += 5
        
        return min(int(score), 100)
    
    def _get_performance_rating(self, score: int) -> str:
        """Get performance rating from score."""
        if score >= 90:
            return "EXCELLENT"
        elif score >= 75:
            return "GOOD"
        elif score >= 60:
            return "AVERAGE"
        elif score >= 40:
            return "BELOW AVERAGE"
        else:
            return "CRITICAL"
    
    def _calculate_performance_gap(self, dealer_data: Dict[str, Any], network: Dict[str, Any]) -> str:
        """Calculate performance gap description."""
        dealer_rate = dealer_data.get("delivery_rate", 0)
        network_rate = network.get("avg_delivery_rate", 0)
        
        gap = dealer_rate - network_rate
        
        if gap > 10:
            return "EXCEEDS NETWORK AVERAGE"
        elif gap > 0:
            return "ABOVE NETWORK AVERAGE"
        elif gap > -10:
            return "AT NETWORK AVERAGE"
        else:
            return "BELOW NETWORK AVERAGE"
    
    def _calculate_growth_percent(self, current: Dict[str, Any], previous: Dict[str, Any]) -> float:
        """Calculate growth percentage."""
        current_value = current.get("revenue", current.get("total_revenue", 0))
        previous_value = previous.get("revenue", previous.get("total_revenue", 0))
        
        if previous_value == 0:
            return 100.0 if current_value > 0 else 0.0
        
        return round(((current_value - previous_value) / previous_value) * 100, 1)
    
    def _determine_trend(self, current: Dict[str, Any], previous: Dict[str, Any]) -> str:
        """Determine trend direction."""
        growth = self._calculate_growth_percent(current, previous)
        
        if growth > 10:
            return "STRONG_UP"
        elif growth > 2:
            return "UP"
        elif growth > -2:
            return "STABLE"
        elif growth > -10:
            return "DOWN"
        else:
            return "STRONG_DOWN"
    
    # ==========================================================
    # PRIVATE METHODS - ROOT CAUSE HELPERS
    # ==========================================================
    
    def _get_warehouses_with_high_pending_pgi(self) -> List[str]:
        """Get warehouses with high pending PGI."""
        try:
            warehouses = self.logistics.get_all_warehouse_names()
            result = []
            for wh in warehouses:
                data = self.logistics.get_warehouse_dashboard_data(wh)
                if data and data.get("pending_delivery", 0) > 20:
                    result.append(wh)
            return result
        except:
            return []
    
    def _get_dealers_with_high_pending_pod(self) -> List[str]:
        """Get dealers with high pending POD."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            result = []
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data and data.get("pending_pod", 0) > 10:
                    result.append(dealer)
            return result
        except:
            return []
    
    def _get_cities_with_high_pending_pod(self) -> List[str]:
        """Get cities with high pending POD."""
        try:
            cities = self.logistics.get_all_city_names()
            result = []
            for city in cities:
                data = self.logistics.get_city_dashboard_data(city)
                if data and data.get("pending_pod", 0) > 10:
                    result.append(city)
            return result
        except:
            return []
    
    def _get_dealers_with_high_aging(self) -> List[str]:
        """Get dealers with high delivery aging."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            result = []
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data and data.get("avg_delivery_aging", 0) > 10:
                    result.append(dealer)
            return result
        except:
            return []
    
    def _get_warehouses_with_high_aging(self) -> List[str]:
        """Get warehouses with high delivery aging."""
        try:
            warehouses = self.logistics.get_all_warehouse_names()
            result = []
            for wh in warehouses:
                data = self.logistics.get_warehouse_dashboard_data(wh)
                if data and data.get("avg_delivery_aging", 0) > 10:
                    result.append(wh)
            return result
        except:
            return []
    
    def _get_dealers_with_high_pod_aging(self) -> List[str]:
        """Get dealers with high POD aging."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            result = []
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data and data.get("avg_pod_aging", 0) > 10:
                    result.append(dealer)
            return result
        except:
            return []
    
    def _identify_root_causes(self, issues: List[Dict[str, Any]]) -> List[str]:
        """Identify root causes from issues."""
        root_causes = []
        
        for issue in issues:
            if "PGI backlog" in issue.get("issue", ""):
                root_causes.append("Warehouse capacity constraints")
                root_causes.append("Insufficient staffing for PGI processing")
            if "POD backlog" in issue.get("issue", ""):
                root_causes.append("Slow POD collection process")
                root_causes.append("Inadequate customer follow-up")
            if "delivery aging" in issue.get("issue", ""):
                root_causes.append("Transportation delays")
                root_causes.append("Route optimization issues")
        
        # Remove duplicates
        return list(set(root_causes))
    
    def _generate_root_cause_recommendations(self, issues: List[Dict[str, Any]]) -> List[str]:
        """Generate recommendations from root cause analysis."""
        recommendations = []
        
        for issue in issues:
            if "PGI backlog" in issue.get("issue", ""):
                recommendations.append("Increase PGI processing capacity")
                recommendations.append("Prioritize high-priority shipments")
            if "POD backlog" in issue.get("issue", ""):
                recommendations.append("Accelerate POD collection team")
                recommendations.append("Implement POD automation")
            if "delivery aging" in issue.get("issue", ""):
                recommendations.append("Review delivery routes")
                recommendations.append("Improve warehouse-to-customer transit")
        
        # Add general recommendations
        if not recommendations:
            recommendations.append("Continue monitoring operations")
            recommendations.append("Maintain current SLA compliance")
        
        # Remove duplicates and limit
        return list(set(recommendations))[:5]
    
    # ==========================================================
    # PRIVATE METHODS - EXECUTIVE INSIGHTS
    # ==========================================================
    
    def _generate_executive_insights(self, insights: Dict[str, Any]) -> Dict[str, Any]:
        """Generate executive insights from data."""
        return {
            "top_risks": self._identify_top_risks(insights),
            "top_opportunities": self._identify_top_opportunities(insights),
            "best_dealer": insights.get("best_dealer", {}),
            "worst_dealer": insights.get("worst_dealer", {}),
            "best_warehouse": insights.get("best_warehouse", {}),
            "worst_warehouse": insights.get("worst_warehouse", {}),
            "critical_deliveries": insights.get("critical_deliveries", [])[:5],
            "recommendations": self._generate_executive_recommendations(insights)
        }
    
    def _identify_top_risks(self, insights: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Identify top risks from insights."""
        risks = []
        
        if insights.get("pending_pgi", 0) > 50:
            risks.append({
                "risk": "High PGI backlog",
                "severity": "critical",
                "impact": "Delayed dispatches"
            })
        
        if insights.get("pending_pod", 0) > 100:
            risks.append({
                "risk": "High POD backlog",
                "severity": "critical",
                "impact": "Revenue recognition delay"
            })
        
        if insights.get("avg_delivery_aging", 0) > 10:
            risks.append({
                "risk": "High delivery aging",
                "severity": "high",
                "impact": "Customer satisfaction impact"
            })
        
        if insights.get("worst_warehouse"):
            risks.append({
                "risk": f"Underperforming warehouse: {insights['worst_warehouse']}",
                "severity": "high",
                "impact": "Operational inefficiency"
            })
        
        return risks[:5]
    
    def _identify_top_opportunities(self, insights: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Identify top opportunities from insights."""
        opportunities = []
        
        if insights.get("best_dealer"):
            opportunities.append({
                "opportunity": f"Learn from {insights['best_dealer']}",
                "potential": "High",
                "action": "Benchmark best practices"
            })
        
        if insights.get("pending_pod", 0) > 50:
            opportunities.append({
                "opportunity": "POD collection improvement",
                "potential": "High",
                "action": "Implement automated POD collection"
            })
        
        if insights.get("best_warehouse"):
            opportunities.append({
                "opportunity": f"Scale {insights['best_warehouse']} processes",
                "potential": "Medium",
                "action": "Standardize successful processes"
            })
        
        return opportunities[:3]
    
    def _generate_executive_recommendations(self, insights: Dict[str, Any]) -> List[str]:
        """Generate executive recommendations."""
        recommendations = []
        
        if insights.get("pending_pgi", 0) > 50:
            recommendations.append("🚨 Expedite PGI processing immediately")
        
        if insights.get("pending_pod", 0) > 100:
            recommendations.append("📎 Prioritize POD collection team")
        
        if insights.get("avg_delivery_aging", 0) > 10:
            recommendations.append(f"⏰ Review delivery process - aging at {insights['avg_delivery_aging']} days")
        
        if insights.get("worst_warehouse"):
            recommendations.append(f"🏭 Focus on {insights['worst_warehouse']} warehouse improvement")
        
        if insights.get("best_dealer"):
            recommendations.append(f"🌟 Study best practices from {insights['best_dealer']}")
        
        if not recommendations:
            recommendations.append("✅ Operations stable - continue monitoring")
        
        return recommendations
    
    # ==========================================================
    # PRIVATE METHODS - CONTROL TOWER
    # ==========================================================
    
    def _generate_control_tower_alerts(self, network: Dict[str, Any], critical: List) -> List[Dict[str, Any]]:
        """Generate control tower alerts."""
        alerts = []
        
        if "error" not in network:
            # Pending PGI alert
            pending_pgi = network.get("pending_pgi", 0)
            if pending_pgi > 50:
                alerts.append({
                    "type": "HIGH_PENDING_PGI",
                    "severity": "critical",
                    "message": f"{pending_pgi} deliveries pending PGI processing",
                    "timestamp": datetime.now().isoformat()
                })
            elif pending_pgi > 20:
                alerts.append({
                    "type": "MODERATE_PENDING_PGI",
                    "severity": "high",
                    "message": f"{pending_pgi} deliveries pending PGI processing",
                    "timestamp": datetime.now().isoformat()
                })
            
            # Pending POD alert
            pending_pod = network.get("pending_pod", 0)
            if pending_pod > 100:
                alerts.append({
                    "type": "HIGH_PENDING_POD",
                    "severity": "critical",
                    "message": f"{pending_pod} deliveries pending POD confirmation",
                    "timestamp": datetime.now().isoformat()
                })
            elif pending_pod > 50:
                alerts.append({
                    "type": "MODERATE_PENDING_POD",
                    "severity": "high",
                    "message": f"{pending_pod} deliveries pending POD confirmation",
                    "timestamp": datetime.now().isoformat()
                })
            
            # Delivery aging alert
            avg_aging = network.get("avg_delivery_aging", 0)
            if avg_aging > 15:
                alerts.append({
                    "type": "HIGH_DELIVERY_AGING",
                    "severity": "critical",
                    "message": f"Average delivery aging: {avg_aging} days",
                    "timestamp": datetime.now().isoformat()
                })
            elif avg_aging > 10:
                alerts.append({
                    "type": "MODERATE_DELIVERY_AGING",
                    "severity": "high",
                    "message": f"Average delivery aging: {avg_aging} days",
                    "timestamp": datetime.now().isoformat()
                })
        
        # Critical deliveries alert
        if critical and len(critical) > 5:
            alerts.append({
                "type": "CRITICAL_DELIVERIES",
                "severity": "critical",
                "message": f"{len(critical)} critical deliveries requiring immediate attention",
                "timestamp": datetime.now().isoformat()
            })
        
        return alerts
    
    # ==========================================================
    # PRIVATE METHODS - RANKINGS
    # ==========================================================
    
    def _get_top_dealers_by_delivery_rate(self, limit: int) -> List[Dict]:
        """Get top dealers by delivery rate."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            results = []
            
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data:
                    results.append({
                        "dealer_name": dealer,
                        "delivery_rate": data.get("delivery_rate", 0),
                        "total_dns": data.get("total_dns", 0)
                    })
            
            results.sort(key=lambda x: x["delivery_rate"], reverse=True)
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Top dealers by delivery rate failed: {e}")
            return []
    
    def _get_top_dealers_by_pod_rate(self, limit: int) -> List[Dict]:
        """Get top dealers by POD rate."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            results = []
            
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data:
                    results.append({
                        "dealer_name": dealer,
                        "pod_rate": data.get("pod_rate", 0),
                        "total_dns": data.get("total_dns", 0)
                    })
            
            results.sort(key=lambda x: x["pod_rate"], reverse=True)
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Top dealers by POD rate failed: {e}")
            return []
    
    def _get_bottom_dealers(self, metric: str, limit: int) -> List[Dict]:
        """Get bottom dealers by metric."""
        try:
            dealers = self.logistics.get_all_dealer_names()
            results = []
            
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data:
                    if metric == "delivery_rate":
                        results.append({
                            "dealer_name": dealer,
                            "value": data.get("delivery_rate", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "pod_rate":
                        results.append({
                            "dealer_name": dealer,
                            "value": data.get("pod_rate", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "revenue":
                        results.append({
                            "dealer_name": dealer,
                            "value": data.get("total_revenue", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "units":
                        results.append({
                            "dealer_name": dealer,
                            "value": data.get("total_units", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
            
            results.sort(key=lambda x: x["value"])
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Bottom dealers failed: {e}")
            return []
    
    def _get_top_cities(self, metric: str, limit: int) -> List[Dict]:
        """Get top cities by metric."""
        try:
            cities = self.logistics.get_all_city_names()
            results = []
            
            for city in cities:
                data = self.logistics.get_city_dashboard_data(city)
                if data:
                    if metric == "revenue":
                        results.append({
                            "city_name": city,
                            "value": data.get("total_revenue", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "units":
                        results.append({
                            "city_name": city,
                            "value": data.get("total_units", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "delivery_rate":
                        results.append({
                            "city_name": city,
                            "value": data.get("delivery_rate", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
            
            results.sort(key=lambda x: x["value"], reverse=True)
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Top cities failed: {e}")
            return []
    
    def _get_bottom_cities(self, metric: str, limit: int) -> List[Dict]:
        """Get bottom cities by metric."""
        try:
            cities = self.logistics.get_all_city_names()
            results = []
            
            for city in cities:
                data = self.logistics.get_city_dashboard_data(city)
                if data:
                    if metric == "delivery_rate":
                        results.append({
                            "city_name": city,
                            "value": data.get("delivery_rate", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
                    elif metric == "pod_rate":
                        results.append({
                            "city_name": city,
                            "value": data.get("pod_rate", 0),
                            "total_dns": data.get("total_dns", 0)
                        })
            
            results.sort(key=lambda x: x["value"])
            return results[:limit]
            
        except Exception as e:
            logger.error(f"Bottom cities failed: {e}")
            return []
    
    # ==========================================================
    # PRIVATE METHODS - RECOMMENDATIONS
    # ==========================================================
    
    def _generate_dealer_recommendations(self, dashboard: Dict[str, Any]) -> List[str]:
        """Generate dealer-specific recommendations."""
        recommendations = []
        
        delivery_rate = dashboard.get("delivery_rate", 100)
        if delivery_rate < 70:
            recommendations.append(f"🔴 Critical: Delivery rate {delivery_rate}% - immediate intervention required")
        elif delivery_rate < 85:
            recommendations.append(f"🟡 Moderate: Delivery rate {delivery_rate}% - improvement recommended")
        
        pod_rate = dashboard.get("pod_rate", 100)
        if pod_rate < 70:
            recommendations.append(f"🔴 Critical: POD rate {pod_rate}% - POD collection needs attention")
        elif pod_rate < 85:
            recommendations.append(f"🟡 Moderate: POD rate {pod_rate}% - POD process review recommended")
        
        avg_aging = dashboard.get("avg_delivery_aging", 0)
        if avg_aging > 15:
            recommendations.append(f"🔴 Critical: Delivery aging {avg_aging} days - investigate delays")
        elif avg_aging > 7:
            recommendations.append(f"🟡 Moderate: Delivery aging {avg_aging} days - monitor closely")
        
        if not recommendations:
            recommendations.append("✅ Dealer performance is stable - continue monitoring")
        
        return recommendations
    
    def _get_quality_status(self, score: int) -> str:
        """Get quality status from score."""
        if score >= 90:
            return "EXCELLENT"
        elif score >= 75:
            return "GOOD"
        elif score >= 60:
            return "FAIR"
        else:
            return "POOR"


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_analytics_service() -> AnalyticsService:
    """Factory function for AnalyticsService singleton."""
    return AnalyticsService()
