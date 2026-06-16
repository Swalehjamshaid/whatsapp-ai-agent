# ==========================================================
# FILE: app/services/analytics_service.py (v1.0 - BUSINESS INTELLIGENCE LAYER)
# ==========================================================
# PURPOSE: Business Intelligence and Analytics
#
# ENTERPRISE FEATURES:
# - ✅ SINGLE RESPONSIBILITY: Only analytics
# - ✅ DEALER ANALYTICS: Complete dealer insights
# - ✅ WAREHOUSE ANALYTICS: Warehouse performance
# - ✅ RANKING ANALYTICS: Top/bottom rankings
# - ✅ EXECUTIVE ANALYTICS: Executive insights
# - ✅ TREND ANALYTICS: Trend detection
# ==========================================================

from typing import Optional, Dict, Any, List
from datetime import date
from loguru import logger

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService


class AnalyticsService:
    """
    BUSINESS INTELLIGENCE LAYER
    
    Responsible for analytics and business intelligence.
    Does NOT access database directly - uses LogisticsQueryService.
    """
    
    def __init__(self):
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
    
    def close(self):
        """Close database connection"""
        self.logistics.close()
        self.kpi.close()
    
    # ==========================================================
    # DEALER ANALYTICS
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Get complete dealer dashboard"""
        dashboard = self.logistics.get_dealer_dashboard(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        # Add KPI insights
        kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
        if "error" not in kpi:
            dashboard["kpi_summary"] = kpi
        
        # Add aging details
        aging = self.logistics.get_dealer_aging(dealer_name)
        if aging:
            dashboard["aging_details"] = aging
        
        return dashboard
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer performance analytics"""
        dashboard = self.logistics.get_dealer_dashboard(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        return {
            "dealer_name": dealer_name,
            "revenue": dashboard.get("total_revenue", 0.0),
            "units": dashboard.get("total_units", 0),
            "delivery_rate": dashboard.get("delivery_rate", 0.0),
            "pod_rate": dashboard.get("pod_rate", 0.0),
            "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0.0),
            "avg_pod_aging": dashboard.get("avg_pod_aging", 0.0),
            "risk_status": self._calculate_risk_status(dashboard)
        }
    
    def get_dealer_dns(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        """Get dealer DNs"""
        return self.logistics.get_dealer_dns(dealer_name, limit)
    
    # ==========================================================
    # WAREHOUSE ANALYTICS
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse dashboard"""
        dashboard = self.logistics.get_warehouse_dashboard(warehouse_name)
        if not dashboard:
            return {"error": f"Warehouse '{warehouse_name}' not found"}
        
        return dashboard
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse performance analytics"""
        kpi = self.kpi.get_warehouse_kpi_summary(warehouse_name)
        if "error" in kpi:
            return {"error": kpi["error"]}
        
        return {
            "warehouse_name": warehouse_name,
            "total_dns": kpi.get("total_dns", 0),
            "total_units": kpi.get("total_units", 0),
            "total_revenue": kpi.get("total_revenue", 0.0),
            "pgi_rate": kpi.get("pgi_rate", 0.0),
            "pod_rate": kpi.get("pod_rate", 0.0),
            "pending_delivery": kpi.get("pending_delivery", 0),
            "pending_pod": kpi.get("pending_pod", 0)
        }
    
    # ==========================================================
    # RANKING ANALYTICS
    # ==========================================================
    
    def get_top_dealers(self, metric: str = "revenue", limit: int = 10) -> List[Dict]:
        """Get top dealers by metric"""
        if metric == "revenue":
            return self.logistics.get_top_dealers_by_revenue(limit)
        elif metric == "units":
            return self.logistics.get_top_dealers_by_units(limit)
        elif metric == "pod_aging":
            return self.logistics.get_worst_dealers_by_pod_aging(limit)
        return []
    
    def get_top_warehouses(self, metric: str = "pending", limit: int = 10) -> List[Dict]:
        """Get top warehouses by metric"""
        if metric == "pending":
            return self.logistics.get_top_warehouses_by_pending(limit)
        return []
    
    # ==========================================================
    # EXECUTIVE ANALYTICS
    # ==========================================================
    
    def get_executive_insights(self) -> Dict[str, Any]:
        """Get executive insights"""
        insights = self.logistics.get_executive_insights()
        if not insights:
            return {}
        
        # Add recommendations
        recommendations = self._generate_recommendations(insights)
        insights["recommendations"] = recommendations
        
        return insights
    
    def get_critical_alerts(self, threshold_days: int = 15, limit: int = 10) -> List[Dict]:
        """Get critical alerts"""
        return self.logistics.get_critical_deliveries(threshold_days, limit)
    
    # ==========================================================
    # HELPERS
    # ==========================================================
    
    def _calculate_risk_status(self, dashboard: Dict[str, Any]) -> str:
        """Calculate risk status"""
        delivery_rate = dashboard.get("delivery_rate", 0)
        pod_rate = dashboard.get("pod_rate", 0)
        avg_delivery_aging = dashboard.get("avg_delivery_aging", 0)
        
        if delivery_rate < 50 or pod_rate < 50 or avg_delivery_aging > 30:
            return "CRITICAL"
        elif delivery_rate < 70 or pod_rate < 70 or avg_delivery_aging > 15:
            return "HIGH"
        elif delivery_rate < 85 or pod_rate < 85 or avg_delivery_aging > 7:
            return "MEDIUM"
        return "LOW"
    
    def _generate_recommendations(self, insights: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on insights"""
        recommendations = []
        
        pending_pgi = insights.get("pending_pgi", 0)
        pending_pod = insights.get("pending_pod", 0)
        avg_delivery_aging = insights.get("avg_delivery_aging", 0)
        
        if pending_pgi > 50:
            recommendations.append("🚨 Expedite PGI processing immediately")
        
        if pending_pod > 100:
            recommendations.append("📎 Prioritize POD collection team")
        
        if avg_delivery_aging > 10:
            recommendations.append(f"⏰ Review delivery process - aging at {avg_delivery_aging} days")
        
        if insights.get("worst_warehouse"):
            recommendations.append(f"🏭 Focus on {insights['worst_warehouse']} warehouse")
        
        if not recommendations:
            recommendations.append("✅ Operations stable - continue monitoring")
        
        return recommendations


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_analytics_service() -> AnalyticsService:
    """Get AnalyticsService instance"""
    return AnalyticsService()
