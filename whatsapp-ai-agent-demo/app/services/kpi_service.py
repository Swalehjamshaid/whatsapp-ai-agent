# ==========================================================
# FILE: app/services/kpi_service.py (v1.0 - KPI CALCULATION LAYER)
# ==========================================================
# PURPOSE: Calculate operational KPIs from raw data
#
# ENTERPRISE FEATURES:
# - ✅ SINGLE RESPONSIBILITY: Only KPI calculations
# - ✅ COMPREHENSIVE METRICS: All operational KPIs
# - ✅ DEALER KPI: Dealer-level metrics
# - ✅ WAREHOUSE KPI: Warehouse-level metrics
# - ✅ GLOBAL KPI: Enterprise-level metrics
# - ✅ SLA COMPLIANCE: SLA calculations
# ==========================================================

from typing import Optional, Dict, Any, List
from datetime import date
from loguru import logger

from app.services.logistics_query_service import LogisticsQueryService


class KPIService:
    """
    KPI CALCULATION LAYER
    
    Responsible for calculating operational KPIs.
    Does NOT access database directly - uses LogisticsQueryService.
    """
    
    def __init__(self):
        self.logistics = LogisticsQueryService()
    
    def close(self):
        """Close database connection"""
        self.logistics.close()
    
    # ==========================================================
    # DEALER KPIS
    # ==========================================================
    
    def get_dealer_kpi_summary(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer KPI summary"""
        dashboard = self.logistics.get_dealer_dashboard(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        return {
            "dealer_name": dealer_name,
            "total_dns": dashboard.get("total_dns", 0),
            "total_units": dashboard.get("total_units", 0),
            "total_revenue": dashboard.get("total_revenue", 0.0),
            "delivered_units": dashboard.get("delivered_units", 0),
            "pending_delivery": dashboard.get("pending_delivery", 0),
            "transit_units": dashboard.get("transit_units", 0),
            "delivery_rate": dashboard.get("delivery_rate", 0.0),
            "pod_rate": dashboard.get("pod_rate", 0.0),
            "avg_delivery_aging": dashboard.get("avg_delivery_aging", 0.0),
            "avg_pod_aging": dashboard.get("avg_pod_aging", 0.0),
            "risk_status": self._calculate_risk_status(dashboard)
        }
    
    def get_dealer_pending_kpi(self, dealer_name: Optional[str] = None) -> Dict[str, Any]:
        """Get dealer pending KPI"""
        pending_pgi = self.logistics.get_pending_pgi_count(dealer_name)
        pending_pod = self.logistics.get_pending_pod_count(dealer_name)
        
        result = {
            "pending_pgi": pending_pgi,
            "pending_pod": pending_pod,
            "total_pending": pending_pgi + pending_pod
        }
        
        if dealer_name:
            result["dealer_name"] = dealer_name
        
        return result
    
    def get_dealer_aging_kpi(self, dealer_name: str) -> Dict[str, Any]:
        """Get dealer aging KPI"""
        aging = self.logistics.get_dealer_aging(dealer_name)
        if not aging:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        return {
            "dealer_name": dealer_name,
            "avg_delivery_aging": aging.get("avg_delivery_aging", 0.0),
            "max_delivery_aging": aging.get("max_delivery_aging", 0.0),
            "avg_pod_aging": aging.get("avg_pod_aging", 0.0),
            "max_pod_aging": aging.get("max_pod_aging", 0.0)
        }
    
    # ==========================================================
    # WAREHOUSE KPIS
    # ==========================================================
    
    def get_warehouse_kpi_summary(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse KPI summary"""
        dashboard = self.logistics.get_warehouse_dashboard(warehouse_name)
        if not dashboard:
            return {"error": f"Warehouse '{warehouse_name}' not found"}
        
        total = dashboard.get("total_dns", 1) or 1
        return {
            "warehouse_name": warehouse_name,
            "total_dns": dashboard.get("total_dns", 0),
            "total_units": dashboard.get("total_units", 0),
            "total_revenue": dashboard.get("total_revenue", 0.0),
            "pending_delivery": dashboard.get("pending_delivery", 0),
            "pending_pod": dashboard.get("pending_pod", 0),
            "pgi_completed": dashboard.get("pgi_completed", 0),
            "pod_completed": dashboard.get("pod_completed", 0),
            "pgi_rate": round((dashboard.get("pgi_completed", 0) / total) * 100, 1) if total > 0 else 0,
            "pod_rate": round((dashboard.get("pod_completed", 0) / total) * 100, 1) if total > 0 else 0
        }
    
    def get_warehouse_pending_kpi(self, warehouse_name: str) -> Dict[str, Any]:
        """Get warehouse pending KPI"""
        pending = self.logistics.get_warehouse_pending(warehouse_name)
        return {
            "warehouse_name": warehouse_name,
            "pending_delivery": pending
        }
    
    # ==========================================================
    # GLOBAL KPIS
    # ==========================================================
    
    def get_global_kpi_summary(self) -> Dict[str, Any]:
        """Get global KPI summary"""
        insights = self.logistics.get_executive_insights()
        if not insights:
            return {}
        
        total = insights.get("total_dns", 1) or 1
        return {
            "total_dns": insights.get("total_dns", 0),
            "pending_pgi": insights.get("pending_pgi", 0),
            "pending_pod": insights.get("pending_pod", 0),
            "avg_delivery_aging": insights.get("avg_delivery_aging", 0.0),
            "pgi_rate": round((insights.get("total_dns", 0) - insights.get("pending_pgi", 0)) / total * 100, 1),
            "pod_rate": round((insights.get("total_dns", 0) - insights.get("pending_pod", 0)) / total * 100, 1),
            "worst_warehouse": insights.get("worst_warehouse"),
            "oldest_dn": insights.get("oldest_dn"),
            "oldest_aging": insights.get("oldest_aging", 0)
        }
    
    # ==========================================================
    # RISK CALCULATION
    # ==========================================================
    
    def _calculate_risk_status(self, dashboard: Dict[str, Any]) -> str:
        """Calculate risk status based on KPIs"""
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


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_kpi_service() -> KPIService:
    """Get KPIService instance"""
    return KPIService()
