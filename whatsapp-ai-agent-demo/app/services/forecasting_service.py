# ==========================================================
# FILE: app/services/forecasting_service.py
# ==========================================================
# FORECASTING AND PREDICTION SERVICE
# ==========================================================

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, date
from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from loguru import logger

from app.models import DeliveryReport


class ForecastingService:
    """Forecasting and prediction service"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def get_sales_forecast(self, days: int = 30) -> Dict[str, Any]:
        """Get sales forecast for next N days"""
        historical_data = self._get_historical_sales(90)
        forecast = self._calculate_forecast(historical_data, days)
        
        return {
            "forecast_days": days,
            "forecast_value": forecast["total"],
            "daily_breakdown": forecast["daily"],
            "confidence_interval": forecast["confidence"],
            "based_on_days": len(historical_data),
            "trend": self._get_trend_direction(historical_data)
        }
    
    def get_pod_forecast(self, days: int = 30) -> Dict[str, Any]:
        """Get POD collection forecast"""
        historical_pod = self._get_historical_pod_rate(90)
        
        # Calculate expected POD collection
        pending_pod = self._get_pending_pod_value()
        expected_collection_rate = self._calculate_expected_pod_rate(historical_pod)
        
        return {
            "forecast_days": days,
            "current_pending_pod": pending_pod,
            "expected_collection_rate": expected_collection_rate,
            "expected_collection_value": pending_pod * (expected_collection_rate / 100),
            "trend": self._get_pod_trend(historical_pod)
        }
    
    def get_dealer_growth_forecast(self, dealer: str = None) -> Dict[str, Any]:
        """Get dealer growth forecast"""
        if dealer:
            historical = self._get_dealer_historical(dealer)
            forecast = self._calculate_growth_forecast(historical)
            return {
                "dealer": dealer,
                "forecast": forecast
            }
        else:
            # Get top 10 dealers forecast
            top_dealers = self._get_top_dealers(10)
            forecasts = []
            for d in top_dealers:
                historical = self._get_dealer_historical(d["name"])
                forecasts.append({
                    "dealer": d["name"],
                    "forecast": self._calculate_growth_forecast(historical)
                })
            return {"dealer_forecasts": forecasts}
    
    def get_inventory_forecast(self, product: str = None) -> Dict[str, Any]:
        """Get inventory forecast"""
        if product:
            demand = self._get_product_demand_history(product, 90)
            forecast = self._calculate_demand_forecast(demand)
            return {
                "product": product,
                "forecast_demand": forecast["demand"],
                "confidence": forecast["confidence"],
                "recommended_stock": forecast["demand"] * 1.2  # 20% safety stock
            }
        else:
            # Get top 10 products forecast
            top_products = self._get_top_products(10)
            forecasts = []
            for p in top_products:
                demand = self._get_product_demand_history(p["product"], 90)
                forecasts.append({
                    "product": p["product"],
                    "forecast": self._calculate_demand_forecast(demand)
                })
            return {"product_forecasts": forecasts}
    
    def get_general_forecast(self) -> Dict[str, Any]:
        """Get general forecast summary"""
        return {
            "sales_forecast_30d": self.get_sales_forecast(30),
            "pod_forecast_30d": self.get_pod_forecast(30),
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
            ).order_by(
                func.date(DeliveryReport.dn_create_date)
            ).all()
            
            for r in results:
                sales_by_day[r.sale_date] = float(r.daily_sales or 0)
            
            # Fill missing days with 0
            current = cutoff_date
            while current <= date.today():
                if current not in sales_by_day:
                    sales_by_day[current] = 0
                current += timedelta(days=1)
            
            return [sales_by_day[d] for d in sorted(sales_by_day.keys())]
        
        except Exception as e:
            logger.error(f"Error getting historical sales: {e}")
            return []
    
    def _calculate_forecast(self, historical: List[float], days: int) -> Dict:
        """Calculate forecast using simple moving average"""
        if not historical:
            return {"total": 0, "daily": [], "confidence": 0}
        
        # Calculate moving average (last 7 days)
        window_size = min(7, len(historical))
        recent_avg = sum(historical[-window_size:]) / window_size if window_size > 0 else 0
        
        # Calculate trend
        if len(historical) >= 14:
            first_week_avg = sum(historical[-14:-7]) / 7 if len(historical[-14:-7]) > 0 else recent_avg
            trend = (recent_avg - first_week_avg) / first_week_avg if first_week_avg > 0 else 0
        else:
            trend = 0
        
        # Generate daily forecast
        daily_forecast = []
        for i in range(days):
            # Apply trend decay
            day_factor = 1 + (trend * (i / days))
            daily_value = recent_avg * day_factor
            daily_forecast.append(max(0, daily_value))
        
        # Calculate confidence based on historical variance
        if len(historical) > 1:
            variance = sum((x - recent_avg) ** 2 for x in historical[-window_size:]) / window_size
            confidence = max(0, min(95, 95 - (variance / recent_avg * 10) if recent_avg > 0 else 50))
        else:
            confidence = 50
        
        return {
            "total": sum(daily_forecast),
            "daily": daily_forecast,
            "confidence": confidence
        }
    
    def _get_trend_direction(self, historical: List[float]) -> str:
        """Get trend direction"""
        if len(historical) < 14:
            return "Insufficient data"
        
        first_week = sum(historical[-14:-7]) / 7
        second_week = sum(historical[-7:]) / 7
        
        if second_week > first_week * 1.1:
            return "UPWARD"
        elif second_week < first_week * 0.9:
            return "DOWNWARD"
        else:
            return "STABLE"
    
    def _get_historical_pod_rate(self, days: int) -> List[float]:
        """Get historical POD collection rate"""
        pod_rates = []
        
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            for i in range(days):
                current_date = cutoff_date + timedelta(days=i)
                next_date = current_date + timedelta(days=1)
                
                delivered = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                    DeliveryReport.delivery_date >= current_date,
                    DeliveryReport.delivery_date < next_date,
                    DeliveryReport.pgi_status == "Completed"
                ).scalar() or 1
                
                pod_received = self.db.query(func.count(DeliveryReport.dn_no)).filter(
                    DeliveryReport.pod_date >= current_date,
                    DeliveryReport.pod_date < next_date,
                    DeliveryReport.pod_status == "Received"
                ).scalar() or 0
                
                rate = (pod_received / delivered) * 100 if delivered > 0 else 0
                pod_rates.append(rate)
        
        except Exception as e:
            logger.error(f"Error getting historical POD rate: {e}")
        
        return pod_rates
    
    def _calculate_expected_pod_rate(self, historical: List[float]) -> float:
        """Calculate expected POD collection rate"""
        if not historical:
            return 70
        
        # Weighted average (more weight to recent)
        weights = [i + 1 for i in range(len(historical))]
        weighted_sum = sum(h * w for h, w in zip(historical, weights))
        total_weights = sum(weights)
        
        return weighted_sum / total_weights if total_weights > 0 else 70
    
    def _get_pending_pod_value(self) -> float:
        """Get current pending POD value"""
        try:
            result = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar()
            return float(result or 0)
        except:
            return 0
    
    def _get_pod_trend(self, historical: List[float]) -> str:
        """Get POD trend direction"""
        if len(historical) < 14:
            return "Insufficient data"
        
        first_week = sum(historical[-14:-7]) / 7
        second_week = sum(historical[-7:]) / 7
        
        if second_week > first_week * 1.1:
            return "IMPROVING"
        elif second_week < first_week * 0.9:
            return "DECLINING"
        else:
            return "STABLE"
    
    def _get_dealer_historical(self, dealer: str) -> List[float]:
        """Get historical sales for a dealer"""
        sales = []
        
        try:
            cutoff_date = date.today() - timedelta(days=90)
            
            results = self.db.query(
                func.date(DeliveryReport.dn_create_date).label("sale_date"),
                func.sum(DeliveryReport.dn_amount).label("daily_sales")
            ).filter(
                DeliveryReport.customer_name == dealer,
                DeliveryReport.dn_create_date >= cutoff_date
            ).group_by(
                func.date(DeliveryReport.dn_create_date)
            ).order_by(
                func.date(DeliveryReport.dn_create_date)
            ).all()
            
            for r in results:
                sales.append(float(r.daily_sales or 0))
        
        except Exception as e:
            logger.error(f"Error getting dealer historical: {e}")
        
        return sales
    
    def _calculate_growth_forecast(self, historical: List[float]) -> Dict:
        """Calculate growth forecast for dealer"""
        if len(historical) < 30:
            return {
                "growth_rate": 0,
                "forecast_next_month": 0,
                "confidence": "LOW"
            }
        
        # Calculate monthly totals
        months = defaultdict(float)
        for i, value in enumerate(historical):
            month_num = i // 30
            months[month_num] += value
        
        if len(months) < 2:
            return {
                "growth_rate": 0,
                "forecast_next_month": sum(historical[-30:]),
                "confidence": "LOW"
            }
        
        # Calculate growth rate
        prev_month = months.get(len(months) - 2, 0)
        last_month = months.get(len(months) - 1, 0)
        
        if prev_month > 0:
            growth_rate = ((last_month - prev_month) / prev_month) * 100
        else:
            growth_rate = 0
        
        forecast_next = last_month * (1 + growth_rate / 100)
        
        confidence = "HIGH" if len(historical) > 60 else "MEDIUM" if len(historical) > 30 else "LOW"
        
        return {
            "growth_rate": growth_rate,
            "forecast_next_month": forecast_next,
            "confidence": confidence
        }
    
    def _get_top_dealers(self, limit: int) -> List[Dict]:
        """Get top dealers"""
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_sales")
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                func.sum(DeliveryReport.dn_amount).desc()
            ).limit(limit).all()
            
            return [{"name": r.customer_name, "sales": float(r.total_sales or 0)} for r in results]
        except:
            return []
    
    def _get_product_demand_history(self, product: str, days: int) -> List[float]:
        """Get historical demand for a product"""
        demand = []
        
        try:
            cutoff_date = date.today() - timedelta(days=days)
            
            results = self.db.query(
                func.date(DeliveryReport.dn_create_date).label("order_date"),
                func.sum(DeliveryReport.dn_qty).label("daily_demand")
            ).filter(
                DeliveryReport.product == product,
                DeliveryReport.dn_create_date >= cutoff_date
            ).group_by(
                func.date(DeliveryReport.dn_create_date)
            ).order_by(
                func.date(DeliveryReport.dn_create_date)
            ).all()
            
            for r in results:
                demand.append(float(r.daily_demand or 0))
        
        except Exception as e:
            logger.error(f"Error getting product demand: {e}")
        
        return demand
    
    def _calculate_demand_forecast(self, historical: List[float]) -> Dict:
        """Calculate demand forecast for product"""
        if not historical:
            return {"demand": 0, "confidence": 0}
        
        # Simple average of last 30 days or all data
        window = min(30, len(historical))
        avg_demand = sum(historical[-window:]) / window if window > 0 else 0
        
        # Add trend factor
        if len(historical) >= 14:
            recent_avg = sum(historical[-7:]) / 7 if len(historical[-7:]) > 0 else avg_demand
            earlier_avg = sum(historical[-14:-7]) / 7 if len(historical[-14:-7]) > 0 else avg_demand
            
            if earlier_avg > 0:
                trend = (recent_avg - earlier_avg) / earlier_avg
            else:
                trend = 0
        else:
            trend = 0
        
        forecast_demand = avg_demand * (1 + trend)
        
        # Calculate confidence based on data quality
        if len(historical) >= 60:
            confidence = "HIGH"
        elif len(historical) >= 30:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
        
        return {
            "demand": max(0, forecast_demand),
            "confidence": confidence,
            "trend": trend
        }
    
    def _get_top_products(self, limit: int) -> List[Dict]:
        """Get top products by demand"""
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_demand")
            ).group_by(
                DeliveryReport.product
            ).order_by(
                func.sum(DeliveryReport.dn_qty).desc()
            ).limit(limit).all()
            
            return [{"product": r.product, "demand": float(r.total_demand or 0)} for r in results]
        except:
            return []
    
    def _get_risk_forecast(self) -> Dict:
        """Get risk forecast"""
        pending_value = self._get_pending_pod_value()
        
        # Simple risk calculation
        if pending_value > 10000000:
            risk_level = "HIGH"
            risk_percentage = 40
        elif pending_value > 5000000:
            risk_level = "MEDIUM"
            risk_percentage = 25
        elif pending_value > 1000000:
            risk_level = "LOW"
            risk_percentage = 10
        else:
            risk_level = "MINIMAL"
            risk_percentage = 5
        
        return {
            "risk_level": risk_level,
            "risk_percentage": risk_percentage,
            "pending_value_at_risk": pending_value * (risk_percentage / 100),
            "recommended_action": self._get_risk_recommendation(risk_level)
        }
    
    def _get_risk_recommendation(self, risk_level: str) -> str:
        """Get recommendation based on risk level"""
        recommendations = {
            "HIGH": "Immediate action required - Escalate to management",
            "MEDIUM": "Priority follow-up with pending POD dealers",
            "LOW": "Regular monitoring and follow-up",
            "MINIMAL": "Continue current processes"
        }
        return recommendations.get(risk_level, "Monitor regularly")
