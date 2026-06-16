# ==========================================================
# FILE: app/services/analytics_service.py (v1.1 - BUSINESS INTELLIGENCE LAYER)
# ==========================================================
# PURPOSE: Business Intelligence and Analytics
# ==========================================================

from typing import Optional, Dict, Any, List
from datetime import date
from loguru import logger

from app.services.logistics_query_service import LogisticsQueryService
from app.services.kpi_service import KPIService
from app.schemas.schema_service import get_schema_service


class AnalyticsService:
    """BUSINESS INTELLIGENCE LAYER - Analytics Only"""
    
    def __init__(self):
        self.logistics = LogisticsQueryService()
        self.kpi = KPIService()
        self.schema = get_schema_service()
        self.today = date.today()
    
    def close(self):
        self.logistics.close()
        self.kpi.close()
    
    # ==========================================================
    # DEALER ANALYTICS
    # ==========================================================
    
    def get_dealer_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
        if "error" not in kpi:
            dashboard["kpi_summary"] = kpi
        
        aging = self.logistics.get_dealer_aging_data(dealer_name)
        if aging:
            dashboard["aging_details"] = aging
        
        dashboard["risk_assessment"] = self._assess_risk(dashboard)
        return dashboard
    
    def get_dealer_performance(self, dealer_name: str) -> Dict[str, Any]:
        dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        kpi = self.kpi.get_dealer_kpi_summary(dealer_name)
        risk_status = kpi.get("risk_status", "unknown") if "error" not in kpi else "unknown"
        
        return {
            "dealer_name": dealer_name,
            "revenue": dashboard.get("total_revenue", 0.0),
            "units": dashboard.get("total_units", 0),
            "delivery_rate": dashboard.get("delivery_rate", 0.0),
            "pod_rate": dashboard.get("pod_rate", 0.0),
            "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0.0),
            "avg_pod_aging": dashboard.get("avg_pod_aging", 0.0),
            "risk_status": risk_status,
            "risk_emoji": self.schema.get_risk_emoji(risk_status)
        }
    
    def get_dealer_dns(self, dealer_name: str, limit: int = 20) -> List[Dict]:
        return self.logistics.get_dealer_dns(dealer_name, limit)
    
    # ==========================================================
    # WAREHOUSE ANALYTICS
    # ==========================================================
    
    def get_warehouse_dashboard(self, warehouse_name: str) -> Dict[str, Any]:
        dashboard = self.logistics.get_warehouse_dashboard_data(warehouse_name)
        if not dashboard:
            return {"error": f"Warehouse '{warehouse_name}' not found"}
        
        kpi = self.kpi.get_warehouse_kpi_summary(warehouse_name)
        if "error" not in kpi:
            dashboard["kpi_summary"] = kpi
        return dashboard
    
    def get_warehouse_performance(self, warehouse_name: str) -> Dict[str, Any]:
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
        if metric == "revenue":
            return self.logistics.get_top_dealers_by_revenue(limit)
        elif metric == "units":
            return self.logistics.get_top_dealers_by_units(limit)
        return []
    
    def get_top_warehouses(self, limit: int = 10) -> List[Dict]:
        return self.logistics.get_top_warehouses_by_pending(limit)
    
    # ==========================================================
    # CONTROL TOWER
    # ==========================================================
    
    def get_control_tower(self, threshold_days: int = 15) -> Dict[str, Any]:
        critical = self.logistics.get_critical_deliveries(threshold_days)
        return {
            "critical_deliveries": critical[:5],
            "total_critical": len(critical),
            "threshold_days": threshold_days,
            "timestamp": self.today.isoformat()
        }
    
    # ==========================================================
    # EXECUTIVE ANALYTICS
    # ==========================================================
    
    def get_executive_dashboard(self) -> Dict[str, Any]:
        insights = self.logistics.get_executive_insights_data()
        if not insights:
            return {}
        insights["recommendations"] = self._generate_recommendations(insights)
        insights["risk_assessment"] = self._assess_global_risk(insights)
        return insights
    
    # ==========================================================
    # HELPERS
    # ==========================================================
    
    def _assess_risk(self, dashboard: Dict[str, Any]) -> Dict[str, Any]:
        risk_factors = []
        if dashboard.get("delivery_rate", 0) < 70:
            risk_factors.append("Low delivery rate")
        if dashboard.get("pod_rate", 0) < 70:
            risk_factors.append("Low POD rate")
        if dashboard.get("avg_delivery_aging", 0) > 15:
            risk_factors.append("High delivery aging")
        return {"risk_factors": risk_factors, "risk_count": len(risk_factors)}
    
    def _assess_global_risk(self, insights: Dict[str, Any]) -> Dict[str, Any]:
        risk_factors = []
        if insights.get("pending_pgi", 0) > 50:
            risk_factors.append("High PGI backlog")
        if insights.get("pending_pod", 0) > 100:
            risk_factors.append("High POD backlog")
        if insights.get("avg_delivery_aging", 0) > 10:
            risk_factors.append("High delivery aging")
        return {"risk_factors": risk_factors, "risk_count": len(risk_factors)}
    
    def _generate_recommendations(self, insights: Dict[str, Any]) -> List[str]:
        recommendations = []
        if insights.get("pending_pgi", 0) > 50:
            recommendations.append("🚨 Expedite PGI processing immediately")
        if insights.get("pending_pod", 0) > 100:
            recommendations.append("📎 Prioritize POD collection team")
        if insights.get("avg_delivery_aging", 0) > 10:
            recommendations.append(f"⏰ Review delivery process - aging at {insights['avg_delivery_aging']} days")
        if insights.get("worst_warehouse"):
            recommendations.append(f"🏭 Focus on {insights['worst_warehouse']} warehouse")
        if not recommendations:
            recommendations.append("✅ Operations stable - continue monitoring")
        return recommendations


def get_analytics_service() -> AnalyticsService:
    return AnalyticsService()
