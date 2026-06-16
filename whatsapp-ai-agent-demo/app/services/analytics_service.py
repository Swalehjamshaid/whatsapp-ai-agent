# ==========================================================
# FILE: app/services/analytics_service.py (v3.0 - COMPLETE METHODS)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Aggregation, Calculation, Insight
# ARCHITECTURE: Pure analytics - no SQL, no AI, no routing
# 
# ALIGNED WITH: SchemaService v7.0
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
    
    ALIGNED WITH: SchemaService v7.0
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
        
        logger.info("=" * 70)
        logger.info("AnalyticsService v3.0 - Complete Methods")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ DEALER ANALYTICS:")
        logger.info("      - get_dealer_dashboard()")
        logger.info("      - get_dealer_revenue()")
        logger.info("      - get_dealer_units()")
        logger.info("      - get_dealer_performance()")
        logger.info("      - get_dealer_aging()")
        logger.info("      - get_dealer_dns()")
        logger.info("      - get_dealer_benchmark()")
        logger.info("      - get_dealer_trend()")
        logger.info("")
        logger.info("   ✅ WAREHOUSE ANALYTICS:")
        logger.info("      - get_warehouse_dashboard()")
        logger.info("      - get_warehouse_performance()")
        logger.info("      - get_warehouse_benchmark()")
        logger.info("      - get_warehouse_trend()")
        logger.info("")
        logger.info("   ✅ CITY ANALYTICS:")
        logger.info("      - get_city_dashboard()")
        logger.info("      - get_city_performance()")
        logger.info("      - get_city_benchmark()")
        logger.info("      - get_city_trend()")
        logger.info("")
        logger.info("   ✅ EXECUTIVE ANALYTICS:")
        logger.info("      - get_executive_summary()")
        logger.info("      - get_root_cause_insights()")
        logger.info("      - get_control_tower_alerts()")
        logger.info("      - get_delivery_performance()")
        logger.info("      - get_trend_analysis()")
        logger.info("")
        logger.info("   ✅ DN ANALYTICS:")
        logger.info("      - get_dn_analytics()")
        logger.info("")
        logger.info("   ✅ RANKING & COMPARISON:")
        logger.info("      - get_dealer_ranking()")
        logger.info("      - compare_dealers()")
        logger.info("      - compare_warehouses()")
        logger.info("      - compare_cities()")
        logger.info("")
        logger.info("   ✅ DATA QUALITY:")
        logger.info("      - get_data_quality_report()")
        logger.info("")
        logger.info("   ✅ NETWORK KPIs:")
        logger.info("      - get_network_kpis()")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
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
    
    def get_dealer_revenue(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer revenue analytics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with revenue data
        """
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            return {
                "dealer_name": dealer_name,
                "total_revenue": dashboard.get("total_revenue", 0.0),
                "count": dashboard.get("total_dns", 0),
                "avg_revenue": dashboard.get("avg_revenue", 0.0) if dashboard.get("total_dns", 0) > 0 else 0.0
            }
        except Exception as e:
            logger.error(f"Dealer revenue failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_units(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer units analytics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with units data
        """
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            return {
                "dealer_name": dealer_name,
                "total_units": dashboard.get("total_units", 0),
                "count": dashboard.get("total_dns", 0),
                "avg_units": dashboard.get("avg_units", 0.0) if dashboard.get("total_dns", 0) > 0 else 0.0
            }
        except Exception as e:
            logger.error(f"Dealer units failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer performance metrics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with performance data
        """
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            # Get KPI summary for risk status
            kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
            risk_status = kpi.get("risk_status", "unknown") if "error" not in kpi else "unknown"
            
            # Calculate performance score
            performance_score = self._calculate_performance_score(dashboard)
            
            return {
                "dealer_name": dealer_name,
                "delivery_rate": dashboard.get("delivery_rate", 0.0),
                "pod_rate": dashboard.get("pod_rate", 0.0),
                "pending_pgi": dashboard.get("pending_delivery", 0),
                "pending_pod": dashboard.get("pending_pod", 0),
                "avg_aging": dashboard.get("avg_delivery_aging", 0.0),
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score),
                "risk_status": risk_status,
                "risk_emoji": self.schema.get_risk_emoji(risk_status) if hasattr(self.schema, 'get_risk_emoji') else "🟢"
            }
        except Exception as e:
            logger.error(f"Dealer performance failed: {e}")
            return {"error": str(e)}
    
    def get_dealer_aging(self, dealer_name: str) -> Dict[str, Any]:
        """
        Get dealer aging analytics.
        
        Args:
            dealer_name: Full dealer name
            
        Returns:
            Dict with aging data
        """
        try:
            dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
            if not dashboard:
                return {"error": f"Dealer '{dealer_name}' not found"}
            
            return {
                "dealer_name": dealer_name,
                "avg_aging": dashboard.get("avg_delivery_aging", 0.0),
                "max_aging": dashboard.get("max_delivery_aging", 0),
                "count": dashboard.get("total_dns", 0)
            }
        except Exception as e:
            logger.error(f"Dealer aging failed: {e}")
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
            dashboard = self.logistics.get_warehouse_dashboard_data(warehouse_name)
            if not dashboard:
                return {"error": f"Warehouse '{warehouse_name}' not found"}
            
            # Calculate performance score
            performance_score = self._calculate_warehouse_performance_score(dashboard)
            
            return {
                "warehouse_name": warehouse_name,
                "total_dns": dashboard.get("total_dns", 0),
                "total_units": dashboard.get("total_units", 0),
                "total_revenue": dashboard.get("total_revenue", 0.0),
                "pgi_rate": dashboard.get("pgi_rate", 0.0),
                "pod_rate": dashboard.get("pod_rate", 0.0),
                "pending_delivery": dashboard.get("pending_delivery", 0),
                "pending_pod": dashboard.get("pending_pod", 0),
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score)
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
                "total_dns": city_data.get("total_dns", 0),
                "total_revenue": city_data.get("total_revenue", 0.0),
                "total_units": city_data.get("total_units", 0),
                "delivery_rate": city_data.get("delivery_rate", 0.0),
                "pod_rate": city_data.get("pod_rate", 0.0),
                "pending_dns": city_data.get("pending_dns", 0),
                "performance_score": performance_score,
                "performance_rating": self._get_performance_rating(performance_score)
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
    # 4. DN ANALYTICS (FIXED)
    # ==========================================================
    
    def get_dn_analytics(self, dn_number: str) -> Dict[str, Any]:
        """
        Get analytics for a specific DN with date validation.
        
        Args:
            dn_number: DN number
            
        Returns:
            Dict with DN analytics and validation
        """
        try:
            # Get DN details from logistics
            dn_data = self.logistics.get_dn_details(dn_number)
            if not dn_data:
                return {"found": False, "error": f"DN {dn_number} not found"}
            
            # Validate dates using SchemaService
            dn_date = dn_data.get('dn_create_date')
            pgi_date = dn_data.get('pgi_date')
            pod_date = dn_data.get('pod_date')
            
            validation = self.schema.calculate_delivery_metrics(dn_date, pgi_date, pod_date)
            
            # Determine status
            status = self._determine_dn_status(dn_data, validation)
            
            return {
                "found": True,
                "record": {
                    "dn_number": dn_data.get('dn_number'),
                    "sold_to_party_name": dn_data.get('sold_to_party_name'),
                    "ship_to_city": dn_data.get('ship_to_city'),
                    "warehouse": dn_data.get('warehouse'),
                    "amount": dn_data.get('amount'),
                    "units": dn_data.get('units'),
                    "dn_date": dn_date.strftime("%Y-%m-%d") if dn_date else None,
                    "pgi_date": pgi_date.strftime("%Y-%m-%d") if pgi_date else None,
                    "pod_date": pod_date.strftime("%Y-%m-%d") if pod_date else None,
                },
                "validation": validation,
                "status": status
            }
            
        except Exception as e:
            logger.error(f"DN analytics failed for {dn_number}: {e}")
            return {"found": False, "error": str(e)}
    
    def _determine_dn_status(self, dn_data: Dict, validation: Dict) -> str:
        """Determine DN status based on dates and validation."""
        if not dn_data.get('pgi_date'):
            return "pending_pgi"
        elif not dn_data.get('pod_date'):
            return "pending_pod"
        elif validation.get("is_valid", False):
            return "delivered"
        else:
            return "unknown"
    
    # ==========================================================
    # 5. EXECUTIVE ANALYTICS (FIXED - ALL METHODS ADDED)
    # ==========================================================
    
    def get_executive_summary(self) -> Dict[str, Any]:
        """
        Get executive summary with comprehensive insights.
        
        Returns:
            Dict with executive summary data
        """
        method_start = time.time()
        logger.info("Executive summary requested")
        
        try:
            # Get network KPIs
            network = self.get_network_kpis()
            
            # Get control tower data
            control_tower = self.get_control_tower_alerts()
            
            # Get top dealers
            top_dealers = self.get_dealer_ranking(limit=5, top=True)
            
            # Get bottom dealers
            bottom_dealers = self.get_dealer_ranking(limit=5, top=False)
            
            # Identify top issues
            top_issues = []
            if network.get("pending_pgi", 0) > 50:
                top_issues.append(f"High PGI backlog: {network['pending_pgi']} deliveries")
            if network.get("pending_pod", 0) > 100:
                top_issues.append(f"High POD backlog: {network['pending_pod']} deliveries")
            if network.get("avg_delivery_aging", 0) > 10:
                top_issues.append(f"High delivery aging: {network['avg_delivery_aging']} days")
            if network.get("avg_pod_aging", 0) > 10:
                top_issues.append(f"High POD aging: {network['avg_pod_aging']} days")
            
            if not top_issues:
                top_issues = ["All metrics within acceptable range"]
            
            # Generate recommendations
            recommendations = []
            if network.get("pending_pgi", 0) > 50:
                recommendations.append("🚨 Expedite PGI processing immediately")
            if network.get("pending_pod", 0) > 100:
                recommendations.append("📎 Prioritize POD collection team")
            if network.get("avg_delivery_aging", 0) > 10:
                recommendations.append(f"⏰ Review delivery process - aging at {network['avg_delivery_aging']} days")
            
            if not recommendations:
                recommendations = ["✅ Operations stable - continue monitoring"]
            
            return {
                "summary": {
                    "total_dns": network.get("total_dns", 0),
                    "total_revenue": network.get("total_revenue", 0.0),
                    "overall_pod_rate": network.get("avg_pod_rate", 0.0),
                    "active_dealers": network.get("entities_analyzed", 0)
                },
                "top_issues": top_issues,
                "recommendations": recommendations,
                "top_dealers": top_dealers.get("dealers", [])[:5],
                "bottom_dealers": bottom_dealers.get("dealers", [])[:5],
                "critical_alerts": control_tower.get("alerts", [])[:5],
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Executive summary failed: {e}")
            return {"error": str(e)}
    
    def get_root_cause_insights(self) -> Dict[str, Any]:
        """
        Get root cause insights from data analysis.
        
        Returns:
            Dict with root cause analysis
        """
        method_start = time.time()
        logger.info("Root cause insights requested")
        
        try:
            network = self.get_network_kpis()
            
            key_issues = []
            recommendations = []
            
            # Analyze PGI backlog
            pending_pgi = network.get("pending_pgi", 0)
            if pending_pgi > 50:
                key_issues.append(f"Critical PGI backlog: {pending_pgi} deliveries awaiting PGI")
                recommendations.append("Increase PGI processing capacity")
                recommendations.append("Prioritize high-priority shipments")
            elif pending_pgi > 20:
                key_issues.append(f"Moderate PGI backlog: {pending_pgi} deliveries awaiting PGI")
                recommendations.append("Monitor PGI processing efficiency")
            
            # Analyze POD backlog
            pending_pod = network.get("pending_pod", 0)
            if pending_pod > 100:
                key_issues.append(f"Critical POD backlog: {pending_pod} deliveries awaiting POD")
                recommendations.append("Accelerate POD collection team")
                recommendations.append("Implement POD automation")
            elif pending_pod > 50:
                key_issues.append(f"Moderate POD backlog: {pending_pod} deliveries awaiting POD")
                recommendations.append("Review POD collection process")
            
            # Analyze delivery aging
            avg_aging = network.get("avg_delivery_aging", 0)
            if avg_aging > 15:
                key_issues.append(f"Critical delivery aging: {avg_aging} days average")
                recommendations.append("Review delivery routes and logistics")
            elif avg_aging > 10:
                key_issues.append(f"High delivery aging: {avg_aging} days average")
                recommendations.append("Optimize warehouse-to-customer transit")
            
            # Analyze POD aging
            avg_pod_aging = network.get("avg_pod_aging", 0)
            if avg_pod_aging > 15:
                key_issues.append(f"Critical POD aging: {avg_pod_aging} days average")
                recommendations.append("Implement automated POD confirmation")
            elif avg_pod_aging > 10:
                key_issues.append(f"High POD aging: {avg_pod_aging} days average")
                recommendations.append("Streamline POD collection process")
            
            # Identify root causes
            root_causes = []
            if pending_pgi > 50:
                root_causes.append("Warehouse capacity constraints")
                root_causes.append("Insufficient staffing for PGI processing")
            if pending_pod > 100:
                root_causes.append("Slow POD collection process")
                root_causes.append("Inadequate customer follow-up")
            if avg_aging > 10:
                root_causes.append("Transportation delays")
                root_causes.append("Route optimization issues")
            
            # Remove duplicates
            root_causes = list(set(root_causes))
            
            if not key_issues:
                key_issues = ["No critical issues identified"]
                root_causes = ["Operations running normally"]
                recommendations = ["Continue monitoring operations"]
            
            return {
                "key_issues": key_issues,
                "root_causes": root_causes[:5],
                "recommendations": list(set(recommendations))[:5],
                "metrics": {
                    "total_dns": network.get("total_dns", 0),
                    "avg_processing_days": network.get("avg_delivery_aging", 0),
                    "avg_delivery_days": network.get("avg_delivery_aging", 0),
                    "pod_rate": network.get("avg_pod_rate", 0),
                    "pending_pod": network.get("pending_pod", 0),
                    "pending_pgi": network.get("pending_pgi", 0)
                },
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Root cause insights failed: {e}")
            return {"error": str(e)}
    
    def get_control_tower_alerts(self) -> Dict[str, Any]:
        """
        Get control tower alerts for critical deliveries.
        
        Returns:
            Dict with control tower data
        """
        method_start = time.time()
        logger.info("Control tower alerts requested")
        
        try:
            # Get critical deliveries
            critical = self.logistics.get_critical_deliveries(threshold_days=15)
            
            # Get network KPIs
            network = self.get_network_kpis()
            
            alerts = []
            critical_count = 0
            high_count = 0
            
            # Generate alerts from critical deliveries
            for delivery in critical[:10]:
                alerts.append({
                    "type": "CRITICAL_DELIVERY",
                    "dealer": delivery.get("sold_to_party_name", "Unknown"),
                    "dn": delivery.get("dn_number"),
                    "risk_status": "critical",
                    "description": f"Delivery aging: {delivery.get('aging_days', 0)} days",
                    "days": delivery.get("aging_days", 0)
                })
                critical_count += 1
            
            # Add PGI alert
            pending_pgi = network.get("pending_pgi", 0) if "error" not in network else 0
            if pending_pgi > 50:
                alerts.append({
                    "type": "PENDING_PGI",
                    "dealer": "Network",
                    "risk_status": "critical",
                    "description": f"{pending_pgi} deliveries pending PGI",
                    "days": 0
                })
                critical_count += 1
            elif pending_pgi > 20:
                alerts.append({
                    "type": "PENDING_PGI",
                    "dealer": "Network",
                    "risk_status": "high",
                    "description": f"{pending_pgi} deliveries pending PGI",
                    "days": 0
                })
                high_count += 1
            
            # Add POD alert
            pending_pod = network.get("pending_pod", 0) if "error" not in network else 0
            if pending_pod > 100:
                alerts.append({
                    "type": "PENDING_POD",
                    "dealer": "Network",
                    "risk_status": "critical",
                    "description": f"{pending_pod} deliveries pending POD",
                    "days": 0
                })
                critical_count += 1
            elif pending_pod > 50:
                alerts.append({
                    "type": "PENDING_POD",
                    "dealer": "Network",
                    "risk_status": "high",
                    "description": f"{pending_pod} deliveries pending POD",
                    "days": 0
                })
                high_count += 1
            
            return {
                "alerts": alerts[:20],
                "critical_count": critical_count,
                "high_count": high_count,
                "generated_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Control tower alerts failed: {e}")
            return {"error": str(e)}
    
    def get_delivery_performance(self) -> Dict[str, Any]:
        """
        Get delivery performance metrics.
        
        Returns:
            Dict with delivery performance data
        """
        try:
            return self.kpi.get_delivery_performance_summary()
        except Exception as e:
            logger.error(f"Delivery performance failed: {e}")
            return {"error": str(e)}
    
    def get_trend_analysis(self) -> Dict[str, Any]:
        """
        Get trend analysis.
        
        Returns:
            Dict with trend data
        """
        try:
            return self.logistics.get_trend_analysis()
        except Exception as e:
            logger.error(f"Trend analysis failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 6. NETWORK KPIs
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
    # 7. RANKING ENGINE
    # ==========================================================
    
    def get_dealer_ranking(self, limit: int = 10, top: bool = True) -> Dict[str, Any]:
        """
        Get dealer ranking.
        
        Args:
            limit: Number of dealers to return
            top: True for top, False for bottom
            
        Returns:
            Dict with dealer ranking
        """
        try:
            dealers = self.logistics.get_all_dealer_names()
            results = []
            
            for dealer in dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data:
                    results.append({
                        "name": dealer,
                        "revenue": data.get("total_revenue", 0),
                        "units": data.get("total_units", 0),
                        "pod_rate": data.get("pod_rate", 0),
                        "dn_count": data.get("total_dns", 0)
                    })
            
            results.sort(key=lambda x: x["revenue"], reverse=top)
            results = results[:limit]
            
            return {"dealers": results}
            
        except Exception as e:
            logger.error(f"Dealer ranking failed: {e}")
            return {"dealers": []}
    
    def compare_dealers(self, dealer1: str, dealer2: str) -> Dict[str, Any]:
        """
        Compare two dealers.
        
        Args:
            dealer1: First dealer name
            dealer2: Second dealer name
            
        Returns:
            Dict with comparison data
        """
        try:
            data1 = self.logistics.get_dealer_dashboard_data(dealer1)
            data2 = self.logistics.get_dealer_dashboard_data(dealer2)
            
            return {
                dealer1: {
                    "revenue": data1.get("total_revenue", 0) if data1 else 0,
                    "units": data1.get("total_units", 0) if data1 else 0,
                    "dn_count": data1.get("total_dns", 0) if data1 else 0,
                    "pod_rate": data1.get("pod_rate", 0) if data1 else 0
                },
                dealer2: {
                    "revenue": data2.get("total_revenue", 0) if data2 else 0,
                    "units": data2.get("total_units", 0) if data2 else 0,
                    "dn_count": data2.get("total_dns", 0) if data2 else 0,
                    "pod_rate": data2.get("pod_rate", 0) if data2 else 0
                }
            }
        except Exception as e:
            logger.error(f"Dealer comparison failed: {e}")
            return {"error": str(e)}
    
    def compare_warehouses(self, warehouse1: str, warehouse2: str) -> Dict[str, Any]:
        """
        Compare two warehouses.
        
        Args:
            warehouse1: First warehouse name
            warehouse2: Second warehouse name
            
        Returns:
            Dict with comparison data
        """
        try:
            data1 = self.logistics.get_warehouse_dashboard_data(warehouse1)
            data2 = self.logistics.get_warehouse_dashboard_data(warehouse2)
            
            return {
                warehouse1: {
                    "revenue": data1.get("total_revenue", 0) if data1 else 0,
                    "units": data1.get("total_units", 0) if data1 else 0,
                    "dn_count": data1.get("total_dns", 0) if data1 else 0,
                    "pod_rate": data1.get("pod_rate", 0) if data1 else 0
                },
                warehouse2: {
                    "revenue": data2.get("total_revenue", 0) if data2 else 0,
                    "units": data2.get("total_units", 0) if data2 else 0,
                    "dn_count": data2.get("total_dns", 0) if data2 else 0,
                    "pod_rate": data2.get("pod_rate", 0) if data2 else 0
                }
            }
        except Exception as e:
            logger.error(f"Warehouse comparison failed: {e}")
            return {"error": str(e)}
    
    def compare_cities(self, city1: str, city2: str) -> Dict[str, Any]:
        """
        Compare two cities.
        
        Args:
            city1: First city name
            city2: Second city name
            
        Returns:
            Dict with comparison data
        """
        try:
            data1 = self.logistics.get_city_dashboard_data(city1)
            data2 = self.logistics.get_city_dashboard_data(city2)
            
            return {
                city1: {
                    "revenue": data1.get("total_revenue", 0) if data1 else 0,
                    "units": data1.get("total_units", 0) if data1 else 0,
                    "dn_count": data1.get("total_dns", 0) if data1 else 0,
                    "pod_rate": data1.get("pod_rate", 0) if data1 else 0
                },
                city2: {
                    "revenue": data2.get("total_revenue", 0) if data2 else 0,
                    "units": data2.get("total_units", 0) if data2 else 0,
                    "dn_count": data2.get("total_dns", 0) if data2 else 0,
                    "pod_rate": data2.get("pod_rate", 0) if data2 else 0
                }
            }
        except Exception as e:
            logger.error(f"City comparison failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # 8. DATA QUALITY
    # ==========================================================
    
    def get_data_quality_report(self) -> Dict[str, Any]:
        """
        Get data quality report.
        
        Returns:
            Dict with data quality metrics
        """
        try:
            return self.logistics.get_data_quality_metrics()
        except Exception as e:
            logger.error(f"Data quality report failed: {e}")
            return {"error": str(e)}
    
    # ==========================================================
    # PRIVATE METHODS
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
    
    def _assess_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for a single entity."""
        risk_score = 0
        risk_factors = []
        
        delivery_rate = data.get("delivery_rate", 100)
        if delivery_rate < 70:
            risk_factors.append(f"Low delivery rate: {delivery_rate}%")
            risk_score += 30
        elif delivery_rate < 85:
            risk_factors.append(f"Moderate delivery rate: {delivery_rate}%")
            risk_score += 15
        
        pod_rate = data.get("pod_rate", 100)
        if pod_rate < 70:
            risk_factors.append(f"Low POD rate: {pod_rate}%")
            risk_score += 30
        elif pod_rate < 85:
            risk_factors.append(f"Moderate POD rate: {pod_rate}%")
            risk_score += 15
        
        avg_aging = data.get("avg_delivery_aging", 0)
        if avg_aging > 15:
            risk_factors.append(f"High delivery aging: {avg_aging} days")
            risk_score += 20
        elif avg_aging > 7:
            risk_factors.append(f"Moderate delivery aging: {avg_aging} days")
            risk_score += 10
        
        risk_score = min(risk_score, 100)
        risk_status = self.schema.get_risk_status(risk_score) if hasattr(self.schema, 'get_risk_status') else "low"
        risk_emoji = self.schema.get_risk_emoji(risk_status) if hasattr(self.schema, 'get_risk_emoji') else "🟢"
        
        return {
            "risk_score": risk_score,
            "risk_status": risk_status,
            "risk_emoji": risk_emoji,
            "risk_factors": risk_factors,
            "risk_count": len(risk_factors)
        }
    
    def _assess_warehouse_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for warehouse."""
        return self._assess_risk(data)
    
    def _assess_city_risk(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Assess risk for city."""
        return self._assess_risk(data)
    
    def _calculate_performance_score(self, data: Dict[str, Any]) -> int:
        """Calculate performance score (0-100)."""
        score = 0
        
        delivery_rate = data.get("delivery_rate", 0)
        score += min(delivery_rate * 0.3, 30)
        
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        avg_aging = data.get("avg_delivery_aging", 0)
        if avg_aging <= 3:
            score += 20
        elif avg_aging <= 7:
            score += 15
        elif avg_aging <= 15:
            score += 10
        else:
            score += 5
        
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
        
        pgi_rate = data.get("pgi_rate", 0)
        score += min(pgi_rate * 0.3, 30)
        
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        pending = data.get("pending_delivery", 0)
        if pending == 0:
            score += 20
        elif pending < 10:
            score += 15
        elif pending < 50:
            score += 10
        else:
            score += 5
        
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
        
        delivery_rate = data.get("delivery_rate", 0)
        score += min(delivery_rate * 0.3, 30)
        
        pod_rate = data.get("pod_rate", 0)
        score += min(pod_rate * 0.3, 30)
        
        pending = data.get("pending_dns", 0)
        if pending == 0:
            score += 20
        elif pending < 10:
            score += 15
        elif pending < 50:
            score += 10
        else:
            score += 5
        
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
                "delivery_rate_gap": dealer_data.get("delivery_rate", 0) - network.get("avg_delivery_rate", 0)
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
    
    def _get_dealer_trend(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer trend analysis."""
        try:
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


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

_analytics_service = None


def get_analytics_service() -> AnalyticsService:
    """Factory function for AnalyticsService singleton."""
    global _analytics_service
    if _analytics_service is None:
        _analytics_service = AnalyticsService()
    return _analytics_service


# ==========================================================
# EXPORTS
# ==========================================================

__all__ = [
    'AnalyticsService',
    'get_analytics_service'
]
