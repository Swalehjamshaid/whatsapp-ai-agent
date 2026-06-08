# ==========================================================
# FILE: app/services/forecasting_service.py (ENTERPRISE v2.0)
# ==========================================================
# FORECASTING SERVICE
# - Sales forecasts
# - POD collection forecasts
# - Inventory forecasts
# - Predictive risk analysis
# ==========================================================

from typing import Dict, Any, List
from datetime import date, timedelta
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func
from loguru import logger

from app.models import DeliveryReport


class ForecastingService:
    """Predictive Analytics and Forecasting Service"""
    
    def __init__(self, db: Session, cache_service=None):
        self.db = db
        self.cache = cache_service
        logger.info("✅ Forecasting Service initialized")
    
    def get_sales_forecast(self, days: int = 30) -> Dict[str, Any]:
        """Get sales forecast for next N days"""
        try:
            historical = self._get_historical_sales(90)
            forecast = self._calculate_forecast(historical, days)
            
            return {
                "forecast_days": days,
                "forecast_value": forecast["total"],
                "daily_average": round(forecast["total"] / days, 2),
                "confidence": forecast["confidence"],
                "trend": self._get_trend_direction(historical)
            }
        except Exception as e:
            logger.error(f"Sales forecast error: {e}")
            return {"error": str(e)}
    
    def get_pod_forecast(self) -> Dict[str, Any]:
        """Get POD collection forecast"""
        try:
            pending_pod = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status != "Received"
            ).scalar() or 0
            
            # Expected collection based on historical rates
            historical_rate = self._get_historical_pod_rate(30)
            expected_collection = pending_pod * (historical_rate / 100)
            
            return {
                "current_pending_pod": float(pending_pod),
                "expected_collection_rate": round(historical_rate, 1),
                "expected_collection_value": round(expected_collection, 2),
                "risk_level": "High" if pending_pod > 10_000_000 else "Medium"
            }
        except Exception as e:
            logger.error(f"POD forecast error: {e}")
            return {"error": str(e)}
    
    def get_general_forecast(self) -> Dict[str, Any]:
        """Get general forecast summary"""
        return {
            "sales_forecast_30d": self.get_sales_forecast(30),
            "pod_forecast": self.get_pod_forecast(),
            "risk_forecast": self._get_risk_forecast()
        }
    
    # ==========================================================
    # PRIVATE METHODS
    # ==========================================================
    
    def _get_historical_sales(self, days: int) -> List[float]:
        """Get historical sales data"""
        sales_by_day = defaultdict(float)
        
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            results = self.db.query(
                func.date(DeliveryReport.dn_create_date).label("sale_date"),
                func.sum(DeliveryReport.dn_amount).label("daily_sales")
            ).filter(
                DeliveryReport.dn_create_date >= cutoff_date
            ).group_by(
                func.date(DeliveryReport.dn_create_date)
            ).all()
            
            for r in results:
                sales_by_day[r.sale_date] = float(r.daily_sales or 0)
            
            # Fill missing days
            current = cutoff_date
            while current <= date.today():
                if current not in sales_by_day:
                    sales_by_day[current] = 0
                current += timedelta(days=1)
            
            return [sales_by_day[d] for d in sorted(sales_by_day.keys())]
        except:
            return []
    
    def _calculate_forecast(self, historical: List[float], days: int) -> Dict:
        """Calculate forecast using moving average"""
        if not historical:
            return {"total": 0, "confidence": 0}
        
        window_size = min(7, len(historical))
        recent_avg = sum(historical[-window_size:]) / window_size if window_size > 0 else 0
        
        # Calculate trend
        if len(historical) >= 14:
            first_week_avg = sum(historical[-14:-7]) / 7 if len(historical[-14:-7]) > 0 else recent_avg
            trend = (recent_avg - first_week_avg) / first_week_avg if first_week_avg > 0 else 0
        else:
            trend = 0
        
        daily_forecast = []
        for i in range(days):
            day_factor = 1 + (trend * (i / days))
            daily_value = recent_avg * day_factor
            daily_forecast.append(max(0, daily_value))
        
        # Calculate confidence
        if len(historical) > 1:
            variance = sum((x - recent_avg) ** 2 for x in historical[-window_size:]) / window_size
            confidence = max(0, min(95, 95 - (variance / recent_avg * 10) if recent_avg > 0 else 50))
        else:
            confidence = 50
        
        return {
            "total": sum(daily_forecast),
            "confidence": round(confidence, 1)
        }
    
    def _get_trend_direction(self, historical: List[float]) -> str:
        if len(historical) < 14:
            return "Insufficient data"
        
        first_week = sum(historical[-14:-7]) / 7 if len(historical[-14:-7]) > 0 else 0
        second_week = sum(historical[-7:]) / 7 if len(historical[-7:]) > 0 else 0
        
        if second_week > first_week * 1.1:
            return "UPWARD"
        elif second_week < first_week * 0.9:
            return "DOWNWARD"
        else:
            return "STABLE"
    
    def _get_historical_pod_rate(self, days: int) -> float:
        """Get historical POD collection rate"""
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.delivery_date >= cutoff_date,
                DeliveryReport.pgi_status == "Completed"
            ).scalar() or 1
            
            pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                DeliveryReport.pod_date >= cutoff_date,
                DeliveryReport.pod_status == "Received"
            ).scalar() or 0
            
            return (pod_received / delivered) * 100 if delivered else 70
        except:
            return 70
    
    def _get_risk_forecast(self) -> Dict:
        """Get risk forecast"""
        try:
            pending_value = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status != "Completed"
            ).scalar() or 0
            
            if pending_value > 10_000_000:
                risk_level = "HIGH"
                risk_percentage = 40
            elif pending_value > 5_000_000:
                risk_level = "MEDIUM"
                risk_percentage = 25
            else:
                risk_level = "LOW"
                risk_percentage = 10
            
            return {
                "risk_level": risk_level,
                "risk_percentage": risk_percentage,
                "value_at_risk": pending_value * (risk_percentage / 100)
            }
        except:
            return {"risk_level": "UNKNOWN", "risk_percentage": 0, "value_at_risk": 0}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def get_forecasting_service(db: Session, cache_service=None) -> ForecastingService:
    return ForecastingService(db, cache_service)
