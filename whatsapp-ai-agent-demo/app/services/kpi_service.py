# ==========================================================
# FILE: app/services/kpi_service.py (v1.1 - KPI CALCULATION LAYER)
# ==========================================================
# PURPOSE: Calculate operational KPIs from raw data
# ==========================================================

from typing import Optional, Dict, Any
from datetime import date
from loguru import logger

from app.services.logistics_query_service import LogisticsQueryService
from app.schemas.schema_service import get_schema_service


class KPIService:
    """OPERATIONAL KPI ENGINE - KPI Calculations Only"""
    
    def __init__(self):
        self.logistics = LogisticsQueryService()
        self.schema = get_schema_service()
        self.today = date.today()
    
    def close(self):
        self.logistics.close()
    
    def get_pending_pgi(self, dealer_name: Optional[str] = None) -> Dict[str, Any]:
        count = self.logistics.get_pending_pgi_count(dealer_name)
        result = {"pending_pgi": count}
        if dealer_name:
            result["dealer_name"] = dealer_name
        return result
    
    def get_pending_pod(self, dealer_name: Optional[str] = None) -> Dict[str, Any]:
        count = self.logistics.get_pending_pod_count(dealer_name)
        result = {"pending_pod": count}
        if dealer_name:
            result["dealer_name"] = dealer_name
        return result
    
    def get_pgi_aging(self, dealer_name: str) -> Dict[str, Any]:
        data = self.logistics.get_dealer_aging_data(dealer_name)
        return {
            "dealer_name": dealer_name,
            "avg_delivery_aging": data.get("avg_delivery_aging", 0.0),
            "max_delivery_aging": data.get("max_delivery_aging", 0.0)
        }
    
    def get_pod_aging(self, dealer_name: str) -> Dict[str, Any]:
        data = self.logistics.get_dealer_aging_data(dealer_name)
        return {
            "dealer_name": dealer_name,
            "avg_pod_aging": data.get("avg_pod_aging", 0.0),
            "max_pod_aging": data.get("max_pod_aging", 0.0)
        }
    
    def get_dealer_kpi_summary(self, dealer_name: str) -> Dict[str, Any]:
        dashboard = self.logistics.get_dealer_dashboard_data(dealer_name)
        if not dashboard:
            return {"error": f"Dealer '{dealer_name}' not found"}
        
        risk_score = self._calculate_risk_score(dashboard)
        risk_status = self.schema.get_risk_status(risk_score)
        
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
            "risk_score": round(risk_score, 1),
            "risk_status": risk_status,
            "risk_emoji": self.schema.get_risk_emoji(risk_status)
        }
    
    def get_global_kpi_summary(self) -> Dict[str, Any]:
        insights = self.logistics.get_executive_insights_data()
        if not insights:
            return {}
        total = insights.get("total_dns", 1) or 1
        return {
            "total_dns": insights.get("total_dns", 0),
            "pending_pgi": insights.get("pending_pgi", 0),
            "pending_pod": insights.get("pending_pod", 0),
            "avg_delivery_aging": insights.get("avg_delivery_aging", 0.0),
            "pgi_rate": round((insights.get("total_dns", 0) - insights.get("pending_pgi", 0)) / total * 100, 1),
            "pod_rate": round((insights.get("total_dns", 0) - insights.get("pending_pod", 0)) / total * 100, 1)
        }
    
    def get_warehouse_kpi_summary(self, warehouse_name: str) -> Dict[str, Any]:
        dashboard = self.logistics.get_warehouse_dashboard_data(warehouse_name)
        if not dashboard:
            return {"error": f"Warehouse '{warehouse_name}' not found"}
        return {
            "warehouse_name": warehouse_name,
            "total_dns": dashboard.get("total_dns", 0),
            "total_units": dashboard.get("total_units", 0),
            "total_revenue": dashboard.get("total_revenue", 0.0),
            "pending_delivery": dashboard.get("pending_delivery", 0),
            "pending_pod": dashboard.get("pending_pod", 0),
            "pgi_rate": dashboard.get("pgi_rate", 0.0),
            "pod_rate": dashboard.get("pod_rate", 0.0)
        }
    
    def _calculate_risk_score(self, dashboard: Dict[str, Any]) -> float:
        delivery_rate = dashboard.get("delivery_rate", 0)
        pod_rate = dashboard.get("pod_rate", 0)
        avg_delivery_aging = dashboard.get("avg_delivery_aging", 0)
        
        delivery_score = min(100, delivery_rate)
        pod_score = min(100, pod_rate)
        aging_score = max(0, 100 - (avg_delivery_aging * 2))
        
        return (delivery_score * 0.4 + pod_score * 0.3 + aging_score * 0.3)


def get_kpi_service() -> KPIService:
    return KPIService()
