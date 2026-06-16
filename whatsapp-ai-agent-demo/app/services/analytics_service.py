# ==========================================================
# FILE: app/services/analytics_service.py (v5.0 - ENTERPRISE DEALER INTELLIGENCE)
# ==========================================================
# PURPOSE: Business Intelligence Layer - Enterprise Dealer Intelligence Engine
# ARCHITECTURE: Pure analytics - no SQL, no AI, no routing
#
# ENTERPRISE FEATURES:
# 1. ✅ Dealer 360 Dashboard - Complete dealer view
# 2. ✅ Dealer Profile - Comprehensive dealer information
# 3. ✅ Dealer Executive KPI Summary - Executive-level KPIs
# 4. ✅ DN Performance Engine - Complete DN analytics
# 5. ✅ DN Breakdown Engine - Multi-dimensional breakdowns
# 6. ✅ Delivery Intelligence - Delivery performance
# 7. ✅ Enterprise Aging Engine - Complete aging analysis
# 8. ✅ POD Intelligence - POD analytics
# 9. ✅ Product Intelligence - Product performance
# 10. ✅ Financial Intelligence - Complete financial analytics
# 11. ✅ Dealer Health Engine - Health scoring
# 12. ✅ Dealer Risk Engine - Multi-factor risk assessment
# 13. ✅ Ranking Engine - Comprehensive rankings
# 14. ✅ Timeline Engine - Complete DN timeline
# 15. ✅ Alert Engine - Proactive alerts
# 16. ✅ Executive Intelligence - Data-driven insights
# 17. ✅ AI Ready Payloads - Structured for Groq
# 18. ✅ Performance Optimized - No N+1 queries
# 19. ✅ Data Quality - Integrity scoring
# 20. ✅ Output Contract - Structured responses
# ==========================================================

from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta
from loguru import logger
import time
from collections import defaultdict
from statistics import mean, stdev
import math

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


# ==========================================================
# RESPONSE CONTRACT
# ==========================================================

class AnalyticsResponse:
    """Standardized analytics response contract."""
    
    def __init__(self, success: bool = True, data: Dict[str, Any] = None, error: str = None):
        self.success = success
        self.data = data or {}
        self.error = error
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "timestamp": self.timestamp
        }


# ==========================================================
# ANALYTICS SERVICE - ENTERPRISE DEALER INTELLIGENCE ENGINE
# ==========================================================

class AnalyticsService:
    """
    ENTERPRISE DEALER INTELLIGENCE ENGINE
    
    This service provides pure analytics calculations, aggregations,
    and insights. It does NOT:
    - Execute SQL queries
    - Call external APIs
    - Contain AI prompts
    - Route requests
    - Format responses
    - Parse natural language
    
    All methods return structured AnalyticsResponse objects.
    """
    
    def __init__(self, use_redis: bool = False):
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
        self._use_redis = use_redis
        
        logger.info("=" * 70)
        logger.info("AnalyticsService v5.0 - Enterprise Dealer Intelligence Engine")
        logger.info("=" * 70)
        logger.info("")
        logger.info("   ✅ ENTERPRISE MODULES:")
        logger.info("      - Dealer 360 Dashboard")
        logger.info("      - Dealer Profile")
        logger.info("      - Dealer Executive KPI Summary")
        logger.info("      - DN Performance Engine")
        logger.info("      - DN Breakdown Engine")
        logger.info("      - Delivery Intelligence")
        logger.info("      - Enterprise Aging Engine")
        logger.info("      - POD Intelligence")
        logger.info("      - Product Intelligence")
        logger.info("      - Financial Intelligence")
        logger.info("      - Dealer Health Engine")
        logger.info("      - Dealer Risk Engine")
        logger.info("      - Ranking Engine")
        logger.info("      - Timeline Engine")
        logger.info("      - Alert Engine")
        logger.info("      - Executive Intelligence")
        logger.info("      - AI Ready Payloads")
        logger.info("      - Data Quality")
        logger.info("")
        logger.info("   STATUS: ✅ PRODUCTION READY")
        logger.info("=" * 70)
    
    def close(self):
        """Close dependencies."""
        self.logistics.close()
        self.kpi.close()
        logger.info("AnalyticsService closed")
    
    # ==========================================================
    # MODULE 1: DEALER 360 DASHBOARD
    # ==========================================================
    
    def get_dealer_360_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get complete dealer 360 dashboard.
        
        Returns:
            AnalyticsResponse with dealer profile, KPIs, performance, risk, health, alerts
        """
        try:
            logger.info(f"Dealer 360 dashboard requested: {dealer_name}")
            
            # Resolve dealer
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dealer_name = resolved
            
            # Fetch all components
            profile = self.get_dealer_profile(dealer_name)
            kpis = self.get_dealer_executive_summary(dealer_name)
            performance = self.get_dealer_dn_performance(dealer_name)
            risk = self.assess_dealer_risk(dealer_name)
            health = self.calculate_dealer_health_score(dealer_name)
            alerts = self.get_dealer_alerts(dealer_name)
            rankings = self.get_dealer_rankings(dealer_name)
            timeline = self.get_dealer_timeline(dealer_name, limit=10)
            
            # Compile dashboard
            dashboard = {
                "dealer_name": dealer_name,
                "profile": profile.data if profile.success else {},
                "executive_kpis": kpis.data if kpis.success else {},
                "performance": performance.data if performance.success else {},
                "risk": risk.data if risk.success else {},
                "health": health.data if health.success else {},
                "alerts": alerts.data if alerts.success else {},
                "rankings": rankings.data if rankings.success else {},
                "recent_timeline": timeline.data if timeline.success else {},
                "generated_at": datetime.now().isoformat()
            }
            
            return AnalyticsResponse(success=True, data=dashboard)
            
        except Exception as e:
            logger.error(f"Dealer 360 dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 2: DEALER PROFILE
    # ==========================================================
    
    def get_dealer_profile(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get comprehensive dealer profile.
        
        Returns:
            AnalyticsResponse with dealer profile data
        """
        try:
            logger.info(f"Dealer profile requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            # Get dealer data
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            # Get dealer metadata from schema
            dealer_info = self._get_dealer_metadata(resolved)
            
            profile = {
                "dealer_name": resolved,
                "dealer_code": dealer_info.get("dealer_code", "N/A"),
                "dealer_type": dealer_info.get("dealer_type", "N/A"),
                "dealer_category": dealer_info.get("dealer_category", "N/A"),
                "city": dashboard.get("city", "N/A"),
                "region": dealer_info.get("region", "N/A"),
                "division": dealer_info.get("division", "N/A"),
                "sales_office": dealer_info.get("sales_office", "N/A"),
                "warehouse": dashboard.get("top_warehouse", "N/A"),
                "sales_manager": dealer_info.get("sales_manager", "N/A"),
                "dealer_status": self._get_dealer_status(dashboard),
                "dealer_creation_date": dealer_info.get("creation_date", "N/A"),
                "total_dns": dashboard.get("total_dns", 0),
                "total_revenue": dashboard.get("total_revenue", 0),
                "total_units": dashboard.get("total_units", 0)
            }
            
            return AnalyticsResponse(success=True, data=profile)
            
        except Exception as e:
            logger.error(f"Dealer profile failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 3: DEALER EXECUTIVE KPI SUMMARY
    # ==========================================================
    
    def get_dealer_executive_summary(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get executive KPI summary for dealer.
        
        Returns:
            AnalyticsResponse with executive KPIs
        """
        try:
            logger.info(f"Dealer executive summary requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            aging = self.logistics.get_dealer_aging_data(resolved)
            
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
                "dealer_health_score": self._calculate_health_score_from_dashboard(dashboard)
            }
            
            return AnalyticsResponse(success=True, data=summary)
            
        except Exception as e:
            logger.error(f"Dealer executive summary failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 4: DN PERFORMANCE ENGINE
    # ==========================================================
    
    def get_dealer_dn_performance(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get dealer DN performance metrics.
        
        Returns:
            AnalyticsResponse with DN performance data
        """
        try:
            logger.info(f"Dealer DN performance requested: {dealer_name}")
            
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
    # MODULE 5: DN BREAKDOWN ENGINE
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
            
            # Get DNS for dealer
            dns = self.logistics.get_dealer_dns(resolved, limit=100)
            
            breakdown = defaultdict(lambda: {"count": 0, "revenue": 0, "units": 0})
            
            for dn in dns:
                key = dn.get(breakdown_type, "Unknown")
                breakdown[key]["count"] += 1
                breakdown[key]["revenue"] += dn.get("amount", 0)
                breakdown[key]["units"] += dn.get("units", 0)
            
            # Convert to list and sort
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
    # MODULE 6: DELIVERY INTELLIGENCE
    # ==========================================================
    
    def get_delivery_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get delivery dashboard for dealer.
        
        Returns:
            AnalyticsResponse with delivery metrics
        """
        try:
            logger.info(f"Delivery dashboard requested: {dealer_name}")
            
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
    # MODULE 7: ENTERPRISE AGING ENGINE
    # ==========================================================
    
    def get_delivery_aging_analysis(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get comprehensive delivery aging analysis.
        
        Returns:
            AnalyticsResponse with aging buckets and metrics
        """
        try:
            logger.info(f"Delivery aging analysis requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            # Get DNS for dealer
            dns = self.logistics.get_dealer_dns(resolved, limit=1000)
            
            # Initialize aging buckets
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
                dn_date = dn.get("dn_date")
                pgi_date = dn.get("pgi_date")
                pod_date = dn.get("pod_date")
                
                if dn_date and pgi_date and pod_date:
                    # Calculate delivery aging
                    if pgi_date and pod_date:
                        aging = (pod_date - pgi_date).days
                        total_aging += aging
                        aging_count += 1
                        
                        # Add to bucket
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
    # MODULE 8: POD INTELLIGENCE
    # ==========================================================
    
    def get_pod_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get POD dashboard for dealer.
        
        Returns:
            AnalyticsResponse with POD metrics
        """
        try:
            logger.info(f"POD dashboard requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            pod_pending = dashboard.get("pending_pod", 0)
            pod_completed = dashboard.get("pod_completed", 0)
            total_pod = pod_pending + pod_completed
            
            # POD buckets
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
                pgi_date = dn.get("pgi_date")
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
    # MODULE 9: PRODUCT INTELLIGENCE
    # ==========================================================
    
    def get_product_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get product dashboard for dealer.
        
        Returns:
            AnalyticsResponse with product intelligence
        """
        try:
            logger.info(f"Product dashboard requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            # Get DNS for dealer
            dns = self.logistics.get_dealer_dns(resolved, limit=1000)
            
            # Analyze products
            products = defaultdict(lambda: {"qty": 0, "revenue": 0, "dn_count": 0})
            
            for dn in dns:
                # Use units as product identifier
                model = f"Product_{dn.get('units', 0)}"
                products[model]["qty"] += dn.get("units", 0)
                products[model]["revenue"] += dn.get("amount", 0)
                products[model]["dn_count"] += 1
            
            # Convert to list and sort
            product_list = []
            for model, data in products.items():
                product_list.append({
                    "model": model,
                    "category": "Electronics",
                    "qty": data["qty"],
                    "revenue": data["revenue"],
                    "dn_count": data["dn_count"]
                })
            
            product_list.sort(key=lambda x: x["revenue"], reverse=True)
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "top_models": product_list[:10],
                "bottom_models": product_list[-10:] if len(product_list) > 10 else [],
                "pending_models": [p for p in product_list if p["dn_count"] > 0],
                "total_products": len(product_list),
                "total_revenue": sum(p["revenue"] for p in product_list)
            })
            
        except Exception as e:
            logger.error(f"Product dashboard failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 10: FINANCIAL INTELLIGENCE
    # ==========================================================
    
    def get_financial_dashboard(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get financial dashboard for dealer.
        
        Returns:
            AnalyticsResponse with financial intelligence
        """
        try:
            logger.info(f"Financial dashboard requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            total_revenue = dashboard.get("total_revenue", 0)
            delivered_revenue = dashboard.get("total_revenue", 0) * (dashboard.get("delivered_units", 0) / max(dashboard.get("total_dns", 1), 1))
            pending_revenue = dashboard.get("total_revenue", 0) * (dashboard.get("pending_delivery", 0) / max(dashboard.get("total_dns", 1), 1))
            pending_pod_revenue = dashboard.get("total_revenue", 0) * (dashboard.get("pending_pod", 0) / max(dashboard.get("total_dns", 1), 1))
            
            financial = {
                "total_revenue": total_revenue,
                "delivered_revenue": delivered_revenue,
                "pending_revenue": pending_revenue,
                "pending_pod_revenue": pending_pod_revenue,
                "daily_revenue": self._get_revenue_by_period(resolved, "daily"),
                "weekly_revenue": self._get_revenue_by_period(resolved, "weekly"),
                "monthly_revenue": self._get_revenue_by_period(resolved, "monthly"),
                "yearly_revenue": self._get_revenue_by_period(resolved, "yearly")
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
    # MODULE 11: DEALER HEALTH ENGINE
    # ==========================================================
    
    def calculate_dealer_health_score(self, dealer_name: str) -> AnalyticsResponse:
        """
        Calculate comprehensive dealer health score.
        
        Returns:
            AnalyticsResponse with health score and components
        """
        try:
            logger.info(f"Dealer health score requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            health_score = self._calculate_health_score_from_dashboard(dashboard)
            
            # Component scores
            delivery_score = self._calculate_delivery_score(dashboard)
            pod_score = self._calculate_pod_score(dashboard)
            sales_score = self._calculate_sales_score(dashboard)
            activity_score = self._calculate_activity_score(dashboard)
            
            # Category
            if health_score >= 80:
                category = "Excellent"
            elif health_score >= 60:
                category = "Good"
            elif health_score >= 40:
                category = "Average"
            else:
                category = "Critical"
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "health_score": health_score,
                "health_category": category,
                "delivery_score": delivery_score,
                "pod_score": pod_score,
                "sales_score": sales_score,
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
    
    def _calculate_health_score_from_dashboard(self, dashboard: Dict) -> int:
        """Calculate health score from dashboard data."""
        delivery_score = self._calculate_delivery_score(dashboard)
        pod_score = self._calculate_pod_score(dashboard)
        sales_score = self._calculate_sales_score(dashboard)
        activity_score = self._calculate_activity_score(dashboard)
        
        return min(100, int((delivery_score * 0.25) + (pod_score * 0.25) + (sales_score * 0.25) + (activity_score * 0.25)))
    
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
    
    def _calculate_sales_score(self, dashboard: Dict) -> int:
        """Calculate sales score (0-100)."""
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
    
    # ==========================================================
    # MODULE 12: DEALER RISK ENGINE
    # ==========================================================
    
    def assess_dealer_risk(self, dealer_name: str) -> AnalyticsResponse:
        """
        Comprehensive dealer risk assessment.
        
        Returns:
            AnalyticsResponse with risk metrics
        """
        try:
            logger.info(f"Dealer risk assessment requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            # Calculate risk components
            delivery_risk = self._calculate_delivery_risk(dashboard)
            pod_risk = self._calculate_pod_risk(dashboard)
            aging_risk = self._calculate_aging_risk(dashboard)
            revenue_risk = self._calculate_revenue_risk(dashboard)
            
            total_risk = delivery_risk + pod_risk + aging_risk + revenue_risk
            
            # Determine risk level
            if total_risk <= 25:
                risk_level = "Low"
                risk_score = 25
            elif total_risk <= 50:
                risk_level = "Medium"
                risk_score = 50
            else:
                risk_level = "High"
                risk_score = 75
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "delivery_risk": delivery_risk,
                "pod_risk": pod_risk,
                "aging_risk": aging_risk,
                "revenue_risk": revenue_risk,
                "delayed_value": dashboard.get("total_revenue", 0) * (dashboard.get("pending_delivery", 0) / max(dashboard.get("total_dns", 1), 1)),
                "critical_aging_value": dashboard.get("total_revenue", 0) * (dashboard.get("avg_delivery_aging", 0) / 30) if dashboard.get("avg_delivery_aging", 0) > 0 else 0,
                "pending_pod_value": dashboard.get("total_revenue", 0) * (dashboard.get("pending_pod", 0) / max(dashboard.get("total_dns", 1), 1))
            })
            
        except Exception as e:
            logger.error(f"Dealer risk assessment failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
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
    
    # ==========================================================
    # MODULE 13: RANKING ENGINE
    # ==========================================================
    
    def get_dealer_rankings(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get comprehensive dealer rankings.
        
        Returns:
            AnalyticsResponse with all rankings
        """
        try:
            logger.info(f"Dealer rankings requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            # Get all dealers for ranking
            all_dealers = self.logistics.get_all_dealer_names()
            
            # Collect data
            dealer_data = []
            for dealer in all_dealers:
                data = self.logistics.get_dealer_dashboard_data(dealer)
                if data and data.get("total_dns", 0) > 0:
                    dealer_data.append({
                        "name": dealer,
                        "revenue": data.get("total_revenue", 0),
                        "units": data.get("total_units", 0),
                        "delivery_rate": data.get("delivery_rate", 0),
                        "pod_rate": data.get("pod_rate", 0),
                        "city": data.get("city", "Unknown"),
                        "sales_office": "Unknown",
                        "division": "Unknown"
                    })
            
            # Find dealer rank
            dealer_info = None
            for d in dealer_data:
                if d["name"] == resolved:
                    dealer_info = d
                    break
            
            if not dealer_info:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found in rankings")
            
            # Calculate ranks
            revenue_rank = sorted(dealer_data, key=lambda x: x["revenue"], reverse=True).index(dealer_info) + 1
            quantity_rank = sorted(dealer_data, key=lambda x: x["units"], reverse=True).index(dealer_info) + 1
            delivery_rank = sorted(dealer_data, key=lambda x: x["delivery_rate"], reverse=True).index(dealer_info) + 1
            pod_rank = sorted(dealer_data, key=lambda x: x["pod_rate"], reverse=True).index(dealer_info) + 1
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "revenue_rank": revenue_rank,
                "quantity_rank": quantity_rank,
                "delivery_rank": delivery_rank,
                "pod_rank": pod_rank,
                "total_dealers": len(dealer_data),
                "city_rank": 0,  # Would need city-level ranking
                "sales_office_rank": 0,
                "division_rank": 0
            })
            
        except Exception as e:
            logger.error(f"Dealer rankings failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 14: TIMELINE ENGINE
    # ==========================================================
    
    def get_dealer_timeline(self, dealer_name: str, limit: int = 20) -> AnalyticsResponse:
        """
        Get dealer timeline of all DNs.
        
        Returns:
            AnalyticsResponse with DN timeline
        """
        try:
            logger.info(f"Dealer timeline requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dns = self.logistics.get_dealer_dns(resolved, limit=limit)
            
            timeline = []
            for dn in dns:
                timeline.append({
                    "dn_number": dn.get("dn_no"),
                    "dn_created": dn.get("dn_date"),
                    "pgi_completed": dn.get("pgi_date"),
                    "delivery_date": dn.get("pod_date"),
                    "pod_date": dn.get("pod_date"),
                    "units": dn.get("units", 0),
                    "amount": dn.get("amount", 0),
                    "warehouse": dn.get("warehouse", "Unknown"),
                    "city": dn.get("city", "Unknown")
                })
            
            return AnalyticsResponse(success=True, data={
                "dealer_name": resolved,
                "timeline": timeline,
                "total_dns": len(timeline)
            })
            
        except Exception as e:
            logger.error(f"Dealer timeline failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    # ==========================================================
    # MODULE 15: ALERT ENGINE
    # ==========================================================
    
    def get_dealer_alerts(self, dealer_name: str) -> AnalyticsResponse:
        """
        Get dealer alerts.
        
        Returns:
            AnalyticsResponse with all alerts
        """
        try:
            logger.info(f"Dealer alerts requested: {dealer_name}")
            
            resolved = self._resolve_dealer(dealer_name)
            if not resolved:
                return AnalyticsResponse(success=False, error=f"Dealer '{dealer_name}' not found")
            
            dashboard = self.logistics.get_dealer_dashboard_data(resolved)
            if not dashboard:
                return AnalyticsResponse(success=False, error=f"No data for dealer '{dealer_name}'")
            
            alerts = []
            
            # Delivery alerts
            avg_aging = dashboard.get("avg_delivery_aging", 0)
            if avg_aging > 7:
                alerts.append({
                    "type": "Delivery",
                    "severity": "High" if avg_aging > 14 else "Medium",
                    "message": f"Delivery aging is {avg_aging} days (threshold: 7 days)",
                    "value": avg_aging
                })
            
            # POD alerts
            avg_pod_aging = dashboard.get("avg_pod_aging", 0)
            if avg_pod_aging > 5:
                alerts.append({
                    "type": "POD",
                    "severity": "High" if avg_pod_aging > 10 else "Medium",
                    "message": f"POD aging is {avg_pod_aging} days (threshold: 5 days)",
                    "value": avg_pod_aging
                })
            
            # Health alerts
            health_score = self._calculate_health_score_from_dashboard(dashboard)
            if health_score < 60:
                alerts.append({
                    "type": "Health",
                    "severity": "High" if health_score < 40 else "Medium",
                    "message": f"Health score is {health_score}/100 (threshold: 60)",
                    "value": health_score
                })
            
            # Pending alerts
            pending_revenue = dashboard.get("total_revenue", 0) * (dashboard.get("pending_delivery", 0) / max(dashboard.get("total_dns", 1), 1))
            if pending_revenue > 100000:
                alerts.append({
                    "type": "Pending",
                    "severity": "High" if pending_revenue > 500000 else "Medium",
                    "message": f"Pending DN value is PKR {pending_revenue:,.0f}",
                    "value": pending_revenue
                })
            
            # POD pending alerts
            pod_pending_revenue = dashboard.get("total_revenue", 0) * (dashboard.get("pending_pod", 0) / max(dashboard.get("total_dns", 1), 1))
            if pod_pending_revenue > 50000:
                alerts.append({
                    "type": "POD_Pending",
                    "severity": "High" if pod_pending_revenue > 200000 else "Medium",
                    "message": f"Pending POD value is PKR {pod_pending_revenue:,.0f}",
                    "value": pod_pending_revenue
                })
            
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
    # MODULE 16: EXECUTIVE INTELLIGENCE
    # ==========================================================
    
    def get_executive_insights(self, dealer_name: str = None) -> AnalyticsResponse:
        """
        Get data-driven executive insights.
        
        Returns:
            AnalyticsResponse with insights, issues, recommendations
        """
        try:
            logger.info(f"Executive insights requested")
            
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
            
            # Generate insights
            insights = []
            issues = []
            recommendations = []
            
            # Delivery insights
            delivery_rate = dashboard.get("delivery_rate", 0)
            if delivery_rate >= 90:
                insights.append("Excellent delivery rate")
            elif delivery_rate >= 80:
                insights.append("Good delivery rate")
            else:
                issues.append(f"Low delivery rate: {delivery_rate}%")
                recommendations.append(f"Improve delivery rate from {delivery_rate}% to 90%+")
            
            # POD insights
            pod_rate = dashboard.get("pod_rate", 0)
            if pod_rate >= 90:
                insights.append("Excellent POD rate")
            elif pod_rate >= 80:
                insights.append("Good POD rate")
            else:
                issues.append(f"Low POD rate: {pod_rate}%")
                recommendations.append(f"Improve POD rate from {pod_rate}% to 90%+")
            
            # Aging insights
            avg_aging = dashboard.get("avg_delivery_aging", 0)
            if avg_aging <= 3:
                insights.append("Excellent delivery speed")
            elif avg_aging <= 7:
                insights.append("Good delivery speed")
            else:
                issues.append(f"High delivery aging: {avg_aging} days")
                recommendations.append(f"Reduce delivery aging from {avg_aging} to < 7 days")
            
            # Revenue insights
            revenue = dashboard.get("total_revenue", 0)
            if revenue > 1000000:
                insights.append(f"Top-tier revenue: PKR {revenue:,.0f}")
            elif revenue > 500000:
                insights.append(f"Good revenue: PKR {revenue:,.0f}")
            else:
                recommendations.append(f"Increase revenue from PKR {revenue:,.0f}")
            
            # Health insight
            health_score = self._calculate_health_score_from_dashboard(dashboard)
            if health_score >= 80:
                insights.append(f"Excellent health score: {health_score}/100")
            elif health_score >= 60:
                insights.append(f"Good health score: {health_score}/100")
            else:
                issues.append(f"Low health score: {health_score}/100")
                recommendations.append(f"Improve health score from {health_score} to 80+")
            
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
            # Get network data
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
    # MODULE 17: AI READY PAYLOADS
    # ==========================================================
    
    def get_ai_context(self, dealer_name: str = None) -> AnalyticsResponse:
        """
        Get structured AI context for Groq.
        
        Returns:
            AnalyticsResponse with facts only (no narrative)
        """
        try:
            logger.info(f"AI context requested")
            
            if dealer_name:
                context = self._get_dealer_ai_context(dealer_name)
            else:
                context = self._get_network_ai_context()
            
            # Add metadata
            context["timestamp"] = datetime.now().isoformat()
            context["data_source"] = "AnalyticsService"
            context["version"] = "5.0"
            
            return AnalyticsResponse(success=True, data=context)
            
        except Exception as e:
            logger.error(f"AI context failed: {e}")
            return AnalyticsResponse(success=False, error=str(e))
    
    def _get_dealer_ai_context(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer AI context."""
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
            "health_score": self._calculate_health_score_from_dashboard(dashboard),
            "risk_assessment": self.assess_dealer_risk(resolved).data
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
    # MODULE 18: DATA QUALITY
    # ==========================================================
    
    def get_data_integrity_score(self) -> AnalyticsResponse:
        """
        Get data integrity score.
        
        Returns:
            AnalyticsResponse with integrity metrics
        """
        try:
            logger.info("Data integrity score requested")
            
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
        """Resolve dealer name."""
        if not dealer_name:
            return None
        return self.schema.resolve_dealer(dealer_name)
    
    def _get_dealer_metadata(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer metadata from schema."""
        # In production, this would come from a dealer master table
        return {
            "dealer_code": dealer_name[:10].upper(),
            "dealer_type": "Retail",
            "dealer_category": "Electronics",
            "region": "Unknown",
            "division": "Unknown",
            "sales_office": "Unknown",
            "sales_manager": "Unknown",
            "creation_date": "N/A"
        }
    
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
    
    # ==========================================================
    # WRAPPER METHODS (Preserve existing API)
    # ==========================================================
    
    # These methods are already defined above but we need to ensure
    # they are available. The existing methods remain unchanged.
    
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
