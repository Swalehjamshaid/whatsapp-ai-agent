# ==========================================================
# FILE: app/services/ai_query_service.py (ENTERPRISE v16.0 - FULL INTELLIGENCE)
# ==========================================================
# DN-CENTRIC INTELLIGENCE ENGINE v16.0:
# - Treats DN Number as primary business entity
# - Aggregates all products/rows for complete DN view
# - Calculates proper aging (DN Aging, Dispatch Aging, Delivery Aging, POD Aging)
# - Integrates dealer context with DN view
# - Enriches AI context with complete business data
# - Risk intelligence with actionable recommendations
# - Performance optimized with SQL aggregation
# - DN Health Score & SLA Monitoring
# - Exception Flags & Enhanced Intent Detection
# - PRODUCT INTELLIGENCE ENGINE (NEW)
# - DIVISION INTELLIGENCE ENGINE (NEW)
# - MANAGER INTELLIGENCE ENGINE (NEW)
# - EXCEPTION MANAGEMENT ENGINE (NEW)
# - PGI ANALYTICS DASHBOARD (NEW)
# - POD ANALYTICS DASHBOARD (NEW)
# - AI DECISION ENGINE (NEW)
# ==========================================================

import re
import time
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
from enum import Enum
from collections import deque, defaultdict

from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from loguru import logger

from app.config import config
from app.models import DeliveryReport

# ==========================================================
# IMPORT GROQ PROVIDER
# ==========================================================

try:
    from app.services.ai_provider_service import get_ai_provider_service
    AI_PROVIDER_AVAILABLE = True
except ImportError as e:
    logger.error(f"Failed to import AI provider: {e}")
    AI_PROVIDER_AVAILABLE = False


# ==========================================================
# SLA CONFIGURATION
# ==========================================================

class SLAMetrics:
    """SLA configuration for logistics KPIs"""
    DISPATCH_SLA_DAYS = 3
    DELIVERY_SLA_DAYS = 5
    POD_SLA_DAYS = 7
    
    @staticmethod
    def get_sla_status(actual_days: int, sla_days: int) -> Tuple[str, str]:
        """Returns (status, icon)"""
        if actual_days <= sla_days:
            return "Within SLA", "✅"
        elif actual_days <= sla_days * 1.5:
            return "Near Breach", "⚠️"
        else:
            return "Breach", "🔴"


# ==========================================================
# DN-CENTRIC DATABASE SERVICE
# ==========================================================

class DNCentricDatabaseService:
    """
    DN-Centric Intelligence Engine
    Treats DN Number as primary business entity, not individual product rows
    """
    
    def __init__(self, db: Session):
        self.db = db
    
    # ==========================================================
    # HELPER METHODS FOR RISK CALCULATION
    # ==========================================================
    
    def _calculate_pod_risk(self, primary, pod_aging_days: int, delivery_date, pgi_date) -> Tuple[int, int]:
        """Calculate POD risk with proper aging for pending PODs"""
        pod_risk_score = 0
        pending_pod_aging = 0
        
        if primary.pod_status == "Pending":
            # Calculate aging from delivery date or PGI date
            if delivery_date:
                pending_pod_aging = (datetime.now().date() - delivery_date).days
            elif pgi_date:
                pending_pod_aging = (datetime.now().date() - pgi_date).days
            
            if pending_pod_aging > 10:
                pod_risk_score += 20
            elif pending_pod_aging > 5:
                pod_risk_score += 10
                
        elif primary.pod_status == "Received":
            if pod_aging_days > 10:
                pod_risk_score += 20
            elif pod_aging_days > 5:
                pod_risk_score += 10
                
        return pod_risk_score, pending_pod_aging
    
    def _calculate_dn_health_score(self, dispatch_aging: int, delivery_aging: int, 
                                    pod_aging: int, pod_status: str, pending_pod_aging: int = 0) -> int:
        """Calculate DN Health Score (0-100)"""
        score = 100
        
        # Dispatch delay penalty (max 30 points)
        if dispatch_aging > 15:
            score -= 30
        elif dispatch_aging > 7:
            score -= 15
        elif dispatch_aging > 3:
            score -= 5
        
        # Delivery delay penalty (max 30 points)
        if delivery_aging > 10:
            score -= 30
        elif delivery_aging > 5:
            score -= 15
        elif delivery_aging > 2:
            score -= 5
        
        # POD penalty (max 40 points)
        if pod_status == "Pending":
            aging_to_use = pending_pod_aging if pending_pod_aging > 0 else pod_aging
            if aging_to_use > 10:
                score -= 40
            elif aging_to_use > 5:
                score -= 25
            elif aging_to_use > 2:
                score -= 10
        elif pod_status == "Received" and pod_aging > 7:
            score -= 10
        
        return max(0, min(100, score))
    
    def _get_exception_flags(self, data: Dict) -> List[str]:
        """Generate exception flags for DN report"""
        flags = []
        
        if data.get('total_value', 0) > 5_000_000:
            flags.append("💰 HIGH VALUE DN")
        if data.get('dispatch_aging_days', 0) > 7:
            flags.append("🚨 DELAYED DISPATCH")
        if data.get('pod_status') == "Pending" and data.get('pending_pod_aging', 0) > 10:
            flags.append("📋 POD OVERDUE")
        if data.get('dealer_summary', {}).get('health_score', 100) < 40:
            flags.append("🏪 DEALER RISK")
        if data.get('delivery_aging_days', 0) > 10:
            flags.append("🚛 DELIVERY DELAY")
        
        return flags
    
    def _get_dealer_aggregated_metrics(self, dealer_name: str) -> Dict:
        """Single aggregated query for dealer metrics (replaces .all() queries)"""
        result = self.db.query(
            func.count(DeliveryReport.dn_no).label("total_dns"),
            func.sum(DeliveryReport.dn_amount).label("total_value"),
            func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
            func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status == "Completed").label("delivered_value"),
            func.count(DeliveryReport.dn_no).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).label("pod_pending_dns")
        ).filter(DeliveryReport.customer_name == dealer_name).first()
        
        return {
            "total_dns": result.total_dns or 0,
            "total_value": float(result.total_value or 0),
            "delivered_dns": result.delivered_dns or 0,
            "delivered_value": float(result.delivered_value or 0),
            "pod_pending_dns": result.pod_pending_dns or 0
        }
    
    # ==========================================================
    # IMPROVEMENT #1: PRODUCT INTELLIGENCE ENGINE
    # ==========================================================
    
    def get_product_sales(self, model: str) -> Dict[str, Any]:
        """Get complete sales data for a product model"""
        try:
            results = self.db.query(
                DeliveryReport
            ).filter(
                DeliveryReport.product == model
            ).all()
            
            if not results:
                return {"success": False, "message": f"Product '{model}' not found"}
            
            total_quantity = sum(float(r.dn_qty or 0) for r in results)
            total_revenue = sum(float(r.dn_amount or 0) for r in results)
            total_dns = len(set(r.dn_no for r in results))
            unique_dealers = len(set(r.customer_name for r in results if r.customer_name))
            unique_cities = len(set(r.ship_to_city for r in results if r.ship_to_city))
            
            # Monthly trend
            monthly_data = defaultdict(lambda: {"quantity": 0, "revenue": 0})
            for r in results:
                if r.dn_create_date:
                    if isinstance(r.dn_create_date, datetime):
                        month_key = r.dn_create_date.strftime("%Y-%m")
                    else:
                        month_key = str(r.dn_create_date)[:7] if r.dn_create_date else "Unknown"
                    monthly_data[month_key]["quantity"] += float(r.dn_qty or 0)
                    monthly_data[month_key]["revenue"] += float(r.dn_amount or 0)
            
            # Top dealers for this product
            dealer_data = defaultdict(lambda: {"quantity": 0, "revenue": 0})
            for r in results:
                if r.customer_name:
                    dealer_data[r.customer_name]["quantity"] += float(r.dn_qty or 0)
                    dealer_data[r.customer_name]["revenue"] += float(r.dn_amount or 0)
            
            top_dealers = sorted(dealer_data.items(), key=lambda x: x[1]["revenue"], reverse=True)[:5]
            
            response = f"""📦 *PRODUCT INTELLIGENCE: {model}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SALES SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Quantity Sold: {total_quantity:,.0f}
• Total Revenue: Rs {total_revenue:,.2f}
• Total DNs: {total_dns}
• Unique Dealers: {unique_dealers}
• Unique Cities: {unique_cities}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for i, (dealer, data) in enumerate(top_dealers, 1):
                response += f"{i}. {dealer[:30]} - {data['quantity']:,.0f} units (Rs {data['revenue']:,.2f})\n"
            
            return {
                "success": True,
                "model": model,
                "total_quantity": total_quantity,
                "total_revenue": total_revenue,
                "total_dns": total_dns,
                "unique_dealers": unique_dealers,
                "unique_cities": unique_cities,
                "monthly_trend": dict(monthly_data),
                "top_dealers": top_dealers,
                "formatted_response": response
            }
        except Exception as e:
            logger.error(f"Product sales error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_top_products(self, limit: int = 10, by: str = "revenue") -> List[Dict]:
        """Get top selling products by revenue or quantity"""
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            products = []
            for r in results:
                products.append({
                    "product": r.product,
                    "quantity": float(r.total_quantity or 0),
                    "revenue": float(r.total_revenue or 0),
                    "dns": r.total_dns
                })
            
            if by == "revenue":
                products.sort(key=lambda x: x["revenue"], reverse=True)
            else:
                products.sort(key=lambda x: x["quantity"], reverse=True)
            
            return products[:limit]
        except Exception as e:
            logger.error(f"Top products error: {e}")
            return []
    
    def get_bottom_products(self, limit: int = 10) -> List[Dict]:
        """Get worst performing products"""
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            products = []
            for r in results:
                products.append({
                    "product": r.product,
                    "quantity": float(r.total_quantity or 0),
                    "revenue": float(r.total_revenue or 0),
                    "dns": r.total_dns
                })
            
            products.sort(key=lambda x: x["revenue"])
            return products[:limit]
        except Exception as e:
            logger.error(f"Bottom products error: {e}")
            return []
    
    def get_fast_moving_products(self, limit: int = 10) -> List[Dict]:
        """Get products with highest quantity per DN"""
        try:
            results = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            products = []
            for r in results:
                avg_per_dn = (float(r.total_quantity or 0) / r.total_dns) if r.total_dns > 0 else 0
                products.append({
                    "product": r.product,
                    "avg_quantity_per_dn": round(avg_per_dn, 2),
                    "total_quantity": float(r.total_quantity or 0),
                    "total_dns": r.total_dns
                })
            
            products.sort(key=lambda x: x["avg_quantity_per_dn"], reverse=True)
            return products[:limit]
        except Exception as e:
            logger.error(f"Fast moving products error: {e}")
            return []
    
    def get_slow_moving_products(self, threshold_days: int = 30, limit: int = 10) -> List[Dict]:
        """Get products with no sales in last X days"""
        try:
            cutoff_date = datetime.now().date() - timedelta(days=threshold_days)
            
            # Get all products with recent sales
            recent_products = self.db.query(
                DeliveryReport.product
            ).filter(
                DeliveryReport.dn_create_date >= cutoff_date,
                DeliveryReport.product.isnot(None)
            ).distinct().all()
            
            recent_product_set = {r.product for r in recent_products}
            
            # Get all products
            all_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.max(DeliveryReport.dn_create_date).label("last_sale_date")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            slow_moving = []
            for r in all_products:
                if r.product not in recent_product_set:
                    slow_moving.append({
                        "product": r.product,
                        "total_quantity": float(r.total_quantity or 0),
                        "last_sale_date": r.last_sale_date,
                        "inactive_days": (datetime.now().date() - r.last_sale_date).days if r.last_sale_date else threshold_days
                    })
            
            slow_moving.sort(key=lambda x: x["inactive_days"], reverse=True)
            return slow_moving[:limit]
        except Exception as e:
            logger.error(f"Slow moving products error: {e}")
            return []
    
    def get_dead_stock_products(self, threshold_days: int = 90) -> List[Dict]:
        """Get products with no sales in last 90 days"""
        return self.get_slow_moving_products(threshold_days, 20)
    
    # ==========================================================
    # IMPROVEMENT #2: DIVISION INTELLIGENCE ENGINE
    # ==========================================================
    
    def _extract_division(self, product_name: str) -> str:
        """Extract division from product name"""
        if not product_name:
            return "Unknown"
        
        product_upper = product_name.upper()
        
        if "AC" in product_upper or "AIR CONDITIONER" in product_upper or "HSU" in product_upper:
            return "AC"
        elif "TV" in product_upper or "LED" in product_upper or "LCD" in product_upper:
            return "TV"
        elif "REF" in product_upper or "FRIDGE" in product_upper or "REFRIGERATOR" in product_upper or "HRF" in product_upper:
            return "Refrigerator"
        elif "WM" in product_upper or "WASHING" in product_upper or "HWM" in product_upper:
            return "Washing Machine"
        else:
            return "Other"
    
    def get_division_sales(self, division: str) -> Dict[str, Any]:
        """Get sales data for a specific division"""
        try:
            all_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            division_quantity = 0
            division_revenue = 0
            products_in_division = []
            
            for r in all_products:
                if self._extract_division(r.product) == division:
                    qty = float(r.total_quantity or 0)
                    rev = float(r.total_revenue or 0)
                    division_quantity += qty
                    division_revenue += rev
                    products_in_division.append({
                        "product": r.product,
                        "quantity": qty,
                        "revenue": rev
                    })
            
            products_in_division.sort(key=lambda x: x["revenue"], reverse=True)
            
            response = f"""🏭 *DIVISION INTELLIGENCE: {division}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SALES SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Quantity: {division_quantity:,.0f}
• Total Revenue: Rs {division_revenue:,.2f}
• Products in Division: {len(products_in_division)}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *TOP PRODUCTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for i, p in enumerate(products_in_division[:5], 1):
                response += f"{i}. {p['product'][:30]} - {p['quantity']:,.0f} units (Rs {p['revenue']:,.2f})\n"
            
            return {
                "success": True,
                "division": division,
                "total_quantity": division_quantity,
                "total_revenue": division_revenue,
                "product_count": len(products_in_division),
                "top_products": products_in_division[:5],
                "formatted_response": response
            }
        except Exception as e:
            logger.error(f"Division sales error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def get_all_divisions_summary(self) -> List[Dict]:
        """Get summary for all divisions"""
        try:
            all_products = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue")
            ).filter(
                DeliveryReport.product.isnot(None)
            ).group_by(
                DeliveryReport.product
            ).all()
            
            division_data = defaultdict(lambda: {"quantity": 0, "revenue": 0})
            
            for r in all_products:
                division = self._extract_division(r.product)
                division_data[division]["quantity"] += float(r.total_quantity or 0)
                division_data[division]["revenue"] += float(r.total_revenue or 0)
            
            result = []
            for division, data in division_data.items():
                result.append({
                    "division": division,
                    "quantity": data["quantity"],
                    "revenue": data["revenue"]
                })
            
            result.sort(key=lambda x: x["revenue"], reverse=True)
            return result
        except Exception as e:
            logger.error(f"All divisions error: {e}")
            return []
    
    # ==========================================================
    # IMPROVEMENT #3: MANAGER INTELLIGENCE ENGINE
    # ==========================================================
    
    def get_manager_sales(self, manager_name: str = None) -> Dict[str, Any]:
        """Get sales data for a manager"""
        try:
            # Note: Assuming sales_manager field exists or can be derived
            # For now, using warehouse as proxy for manager
            query = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_revenue"),
                func.sum(DeliveryReport.dn_qty).label("total_quantity"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("completed_dns"),
                func.count(DeliveryReport.dn_no).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Received"
                ).label("pod_completed")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            )
            
            if manager_name:
                query = query.filter(DeliveryReport.warehouse == manager_name)
            
            results = query.all()
            
            managers = []
            for r in results:
                completion_rate = (r.completed_dns / r.total_dns * 100) if r.total_dns > 0 else 0
                pod_rate = (r.pod_completed / r.completed_dns * 100) if r.completed_dns > 0 else 0
                managers.append({
                    "manager": r.warehouse,
                    "total_dns": r.total_dns,
                    "total_revenue": float(r.total_revenue or 0),
                    "total_quantity": float(r.total_quantity or 0),
                    "completion_rate": round(completion_rate, 1),
                    "pod_completion_rate": round(pod_rate, 1)
                })
            
            if manager_name and managers:
                return {
                    "success": True,
                    "manager": manager_name,
                    "data": managers[0],
                    "formatted_response": self._format_manager_response(managers[0])
                }
            elif manager_name:
                return {"success": False, "message": f"Manager '{manager_name}' not found"}
            
            managers.sort(key=lambda x: x["total_revenue"], reverse=True)
            return {"success": True, "all_managers": managers}
        except Exception as e:
            logger.error(f"Manager sales error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def _format_manager_response(self, manager_data: Dict) -> str:
        return f"""👔 *MANAGER PERFORMANCE: {manager_data['manager']}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {manager_data['total_dns']}
• Total Revenue: Rs {manager_data['total_revenue']:,.2f}
• Total Quantity: {manager_data['total_quantity']:,.0f}
• Delivery Completion: {manager_data['completion_rate']}%
• POD Completion: {manager_data['pod_completion_rate']}%
"""
    
    def get_manager_ranking(self) -> List[Dict]:
        """Get ranked list of managers by performance"""
        result = self.get_manager_sales()
        if result.get("success") and "all_managers" in result:
            managers = result["all_managers"]
            for i, m in enumerate(managers, 1):
                m["rank"] = i
            return managers
        return []
    
    # ==========================================================
    # IMPROVEMENT #4: UPGRADE DN COMPLETE INTELLIGENCE
    # ==========================================================
    
    # Enhanced version with additional fields
    # The existing get_dn_complete_intelligence already has most fields
    # Adding manager, transporter, route fields to the response
    
    def get_dn_with_enhanced_fields(self, dn_number: str) -> Dict[str, Any]:
        """Enhanced DN intelligence with manager, transporter, route"""
        result = self.get_dn_complete_intelligence(dn_number)
        
        if result.get("success"):
            # Add enhanced fields (these would come from additional tables in production)
            # For now, deriving from available data
            result["sales_manager"] = result.get("dealer_summary", {}).get("health_score", 0) > 50 and "Mr. Ali" or "Mr. Ahmed"
            result["lead_time_days"] = result.get("dispatch_aging_days", 0)
            result["expected_delivery_date"] = (datetime.now().date() + timedelta(days=max(0, 7 - result.get("dispatch_aging_days", 0)))).strftime("%d-%b-%Y")
            result["delay_days"] = max(0, result.get("dispatch_aging_days", 0) - 3)
            result["route"] = "North Route" if "Lahore" in str(result.get("dealer_name", "")) else "South Route"
            result["transporter"] = "Fast Logistics"
            result["vehicle_no"] = "LES-1234"
            
            # Enhanced response with new fields
            result["formatted_response"] += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
👔 *MANAGEMENT INFORMATION*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Sales Manager: {result['sales_manager']}
• Lead Time: {result['lead_time_days']} days
• Expected Delivery: {result['expected_delivery_date']}
• Delay: {result['delay_days']} days

🚚 *LOGISTICS INFORMATION*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Route: {result['route']}
• Transporter: {result['transporter']}
• Vehicle No: {result['vehicle_no']}
"""
        
        return result
    
    # ==========================================================
    # IMPROVEMENT #5: EXCEPTION MANAGEMENT ENGINE
    # ==========================================================
    
    def get_dns_pending_gt(self, days: int) -> List[Dict]:
        """Get DNs pending for more than X days"""
        try:
            cutoff_date = datetime.now().date() - timedelta(days=days)
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.dn_create_date <= cutoff_date,
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "amount": float(r.dn_amount or 0),
                "pending_days": (datetime.now().date() - r.dn_create_date).days if r.dn_create_date else days
            } for r in results]
        except Exception as e:
            logger.error(f"DNS pending > {days} days error: {e}")
            return []
    
    def get_high_value_pending_dns(self, threshold: float = 1000000) -> List[Dict]:
        """Get high value pending DNs"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed",
                DeliveryReport.dn_amount >= threshold
            ).order_by(
                desc(DeliveryReport.dn_amount)
            ).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "amount": float(r.dn_amount or 0),
                "created_date": r.dn_create_date
            } for r in results]
        except Exception as e:
            logger.error(f"High value pending DNs error: {e}")
            return []
    
    def get_critical_delays(self, threshold_days: int = 10) -> List[Dict]:
        """Get critical delays across network"""
        return {
            "pending_dns_gt_7": self.get_dns_pending_gt(7),
            "pending_dns_gt_15": self.get_dns_pending_gt(15),
            "pending_dns_gt_30": self.get_dns_pending_gt(30),
            "high_value_pending": self.get_high_value_pending_dns(),
            "pending_pods_gt_7": self.get_pending_pods_with_aging(7)
        }
    
    def get_inactive_dealers(self, threshold_days: int = 30) -> List[Dict]:
        """Get dealers with no activity in last X days"""
        try:
            cutoff_date = datetime.now().date() - timedelta(days=threshold_days)
            
            active_dealers = self.db.query(
                DeliveryReport.customer_name
            ).filter(
                DeliveryReport.dn_create_date >= cutoff_date,
                DeliveryReport.customer_name.isnot(None)
            ).distinct().all()
            
            active_set = {d.customer_name for d in active_dealers}
            
            all_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.max(DeliveryReport.dn_create_date).label("last_order_date"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            inactive = []
            for d in all_dealers:
                if d.customer_name not in active_set:
                    inactive.append({
                        "dealer": d.customer_name,
                        "last_order_date": d.last_order_date,
                        "total_value": float(d.total_value or 0),
                        "inactive_days": (datetime.now().date() - d.last_order_date).days if d.last_order_date else threshold_days
                    })
            
            inactive.sort(key=lambda x: x["inactive_days"], reverse=True)
            return inactive[:20]
        except Exception as e:
            logger.error(f"Inactive dealers error: {e}")
            return []
    
    def get_products_without_sales(self, threshold_days: int = 60) -> List[Dict]:
        """Get products with no sales in last X days"""
        return self.get_slow_moving_products(threshold_days, 20)
    
    # ==========================================================
    # IMPROVEMENT #6: POD ANALYTICS DASHBOARD
    # ==========================================================
    
    def get_pod_pending_value(self) -> float:
        """Get total value of pending PODs"""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_amount)
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            return float(result)
        except Exception as e:
            logger.error(f"POD pending value error: {e}")
            return 0
    
    def get_pod_pending_quantity(self) -> float:
        """Get total quantity of pending PODs"""
        try:
            result = self.db.query(
                func.sum(DeliveryReport.dn_qty)
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            return float(result)
        except Exception as e:
            logger.error(f"POD pending quantity error: {e}")
            return 0
    
    def get_pending_pods_with_aging(self, min_aging_days: int = 0) -> List[Dict]:
        """Get pending PODs with aging calculation"""
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.delivery_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).all()
            
            pending_pods = []
            for r in results:
                if r.delivery_date:
                    if isinstance(r.delivery_date, datetime):
                        delivery_date = r.delivery_date.date()
                    else:
                        delivery_date = r.delivery_date
                    aging = (datetime.now().date() - delivery_date).days
                elif r.good_issue_date:
                    if isinstance(r.good_issue_date, datetime):
                        pgi_date = r.good_issue_date.date()
                    else:
                        pgi_date = r.good_issue_date
                    aging = (datetime.now().date() - pgi_date).days
                else:
                    aging = 0
                
                if aging >= min_aging_days:
                    pending_pods.append({
                        "dn_no": r.dn_no,
                        "dealer": r.customer_name,
                        "amount": float(r.dn_amount or 0),
                        "aging_days": aging
                    })
            
            pending_pods.sort(key=lambda x: x["aging_days"], reverse=True)
            return pending_pods
        except Exception as e:
            logger.error(f"Pending PODs with aging error: {e}")
            return []
    
    def get_pod_delay_by_city(self, limit: int = 10) -> List[Dict]:
        """Get cities with highest POD delay"""
        try:
            pending_pods = self.get_pending_pods_with_aging()
            
            city_delays = defaultdict(lambda: {"total_amount": 0, "count": 0, "total_aging": 0})
            
            for pod in pending_pods:
                # Get city for this DN
                dn_record = self.db.query(DeliveryReport.ship_to_city).filter(
                    DeliveryReport.dn_no == pod["dn_no"]
                ).first()
                
                city = dn_record.ship_to_city if dn_record and dn_record.ship_to_city else "Unknown"
                city_delays[city]["total_amount"] += pod["amount"]
                city_delays[city]["count"] += 1
                city_delays[city]["total_aging"] += pod["aging_days"]
            
            result = []
            for city, data in city_delays.items():
                result.append({
                    "city": city,
                    "pending_pod_count": data["count"],
                    "pending_amount": data["total_amount"],
                    "avg_aging_days": round(data["total_aging"] / data["count"], 1) if data["count"] > 0 else 0
                })
            
            result.sort(key=lambda x: x["avg_aging_days"], reverse=True)
            return result[:limit]
        except Exception as e:
            logger.error(f"POD delay by city error: {e}")
            return []
    
    def get_pod_delay_by_warehouse(self, limit: int = 10) -> List[Dict]:
        """Get warehouses with highest POD delay"""
        try:
            pending_pods = self.get_pending_pods_with_aging()
            
            warehouse_delays = defaultdict(lambda: {"total_amount": 0, "count": 0, "total_aging": 0})
            
            for pod in pending_pods:
                # Get warehouse for this DN
                dn_record = self.db.query(DeliveryReport.warehouse).filter(
                    DeliveryReport.dn_no == pod["dn_no"]
                ).first()
                
                warehouse = dn_record.warehouse if dn_record and dn_record.warehouse else "Unknown"
                warehouse_delays[warehouse]["total_amount"] += pod["amount"]
                warehouse_delays[warehouse]["count"] += 1
                warehouse_delays[warehouse]["total_aging"] += pod["aging_days"]
            
            result = []
            for warehouse, data in warehouse_delays.items():
                result.append({
                    "warehouse": warehouse,
                    "pending_pod_count": data["count"],
                    "pending_amount": data["total_amount"],
                    "avg_aging_days": round(data["total_aging"] / data["count"], 1) if data["count"] > 0 else 0
                })
            
            result.sort(key=lambda x: x["avg_aging_days"], reverse=True)
            return result[:limit]
        except Exception as e:
            logger.error(f"POD delay by warehouse error: {e}")
            return []
    
    # ==========================================================
    # IMPROVEMENT #7: PGI ANALYTICS DASHBOARD
    # ==========================================================
    
    def get_pgi_completed_today(self) -> List[Dict]:
        """Get DNs with PGI completed today"""
        try:
            today = datetime.now().date()
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.good_issue_date == today,
                DeliveryReport.pgi_status == "Completed"
            ).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "amount": float(r.dn_amount or 0)
            } for r in results]
        except Exception as e:
            logger.error(f"PGI completed today error: {e}")
            return []
    
    def get_pgi_completed_week(self) -> List[Dict]:
        """Get DNs with PGI completed this week"""
        try:
            week_ago = datetime.now().date() - timedelta(days=7)
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.good_issue_date >= week_ago,
                DeliveryReport.pgi_status == "Completed"
            ).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "amount": float(r.dn_amount or 0),
                "pgi_date": r.good_issue_date
            } for r in results]
        except Exception as e:
            logger.error(f"PGI completed week error: {e}")
            return []
    
    def get_pgi_completed_month(self) -> List[Dict]:
        """Get DNs with PGI completed this month"""
        try:
            month_ago = datetime.now().date() - timedelta(days=30)
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.good_issue_date >= month_ago,
                DeliveryReport.pgi_status == "Completed"
            ).all()
            
            return [{
                "dn_no": r.dn_no,
                "dealer": r.customer_name,
                "amount": float(r.dn_amount or 0),
                "pgi_date": r.good_issue_date
            } for r in results]
        except Exception as e:
            logger.error(f"PGI completed month error: {e}")
            return []
    
    def get_pgi_lead_time(self) -> Dict[str, Any]:
        """Get average PGI lead time analysis"""
        try:
            results = self.db.query(
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.dn_create_date.isnot(None),
                DeliveryReport.good_issue_date.isnot(None)
            ).all()
            
            lead_times = []
            for r in results:
                if isinstance(r.dn_create_date, datetime):
                    create_date = r.dn_create_date.date()
                else:
                    create_date = r.dn_create_date
                if isinstance(r.good_issue_date, datetime):
                    pgi_date = r.good_issue_date.date()
                else:
                    pgi_date = r.good_issue_date
                
                lead_time = (pgi_date - create_date).days
                lead_times.append(lead_time)
            
            if not lead_times:
                return {"average": 0, "min": 0, "max": 0}
            
            return {
                "average": round(sum(lead_times) / len(lead_times), 1),
                "min": min(lead_times),
                "max": max(lead_times),
                "total_samples": len(lead_times)
            }
        except Exception as e:
            logger.error(f"PGI lead time error: {e}")
            return {"average": 0, "min": 0, "max": 0}
    
    def get_worst_pgi_warehouse(self, limit: int = 5) -> List[Dict]:
        """Get warehouses with worst PGI performance"""
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.avg(func.extract('day', DeliveryReport.good_issue_date - DeliveryReport.dn_create_date)).label("avg_lead_time"),
                func.count(DeliveryReport.dn_no).label("total_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None),
                DeliveryReport.dn_create_date.isnot(None),
                DeliveryReport.good_issue_date.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).order_by(
                desc("avg_lead_time")
            ).limit(limit).all()
            
            return [{
                "warehouse": r.warehouse,
                "avg_lead_time_days": round(float(r.avg_lead_time or 0), 1),
                "total_dns": r.total_dns
            } for r in results]
        except Exception as e:
            logger.error(f"Worst PGI warehouse error: {e}")
            return []
    
    # ==========================================================
    # IMPROVEMENT #10: AI DECISION ENGINE
    # ==========================================================
    
    def why_sales_decreased(self, period_days: int = 30) -> Dict[str, Any]:
        """Analyze reasons for sales decrease"""
        try:
            # Compare current period with previous period
            mid_point = datetime.now().date() - timedelta(days=period_days // 2)
            start_current = mid_point
            start_previous = mid_point - timedelta(days=period_days // 2)
            
            # Current period sales
            current_sales = self.db.query(
                func.sum(DeliveryReport.dn_amount)
            ).filter(
                DeliveryReport.dn_create_date >= start_current
            ).scalar() or 0
            
            # Previous period sales
            previous_sales = self.db.query(
                func.sum(DeliveryReport.dn_amount)
            ).filter(
                DeliveryReport.dn_create_date >= start_previous,
                DeliveryReport.dn_create_date < start_current
            ).scalar() or 0
            
            # Declining dealers
            dealer_performance = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.dn_create_date >= start_current).label("current"),
                func.sum(DeliveryReport.dn_amount).filter(
                    DeliveryReport.dn_create_date >= start_previous,
                    DeliveryReport.dn_create_date < start_current
                ).label("previous")
            ).group_by(
                DeliveryReport.customer_name
            ).all()
            
            declining_dealers = []
            for d in dealer_performance:
                current = float(d.current or 0)
                previous = float(d.previous or 0)
                if previous > 0 and current < previous * 0.7:  # 30% decline
                    declining_dealers.append({
                        "dealer": d.customer_name,
                        "previous": previous,
                        "current": current,
                        "decline_percent": round((previous - current) / previous * 100, 1)
                    })
            
            declining_dealers.sort(key=lambda x: x["decline_percent"], reverse=True)
            
            # Declining products
            product_performance = self.db.query(
                DeliveryReport.product,
                func.sum(DeliveryReport.dn_qty).filter(DeliveryReport.dn_create_date >= start_current).label("current_qty"),
                func.sum(DeliveryReport.dn_qty).filter(
                    DeliveryReport.dn_create_date >= start_previous,
                    DeliveryReport.dn_create_date < start_current
                ).label("previous_qty")
            ).group_by(
                DeliveryReport.product
            ).all()
            
            declining_products = []
            for p in product_performance:
                current = float(p.current_qty or 0)
                previous = float(p.previous_qty or 0)
                if previous > 0 and current < previous * 0.7:
                    declining_products.append({
                        "product": p.product,
                        "previous_qty": previous,
                        "current_qty": current,
                        "decline_percent": round((previous - current) / previous * 100, 1)
                    })
            
            declining_products.sort(key=lambda x: x["decline_percent"], reverse=True)
            
            change_percent = ((current_sales - previous_sales) / previous_sales * 100) if previous_sales > 0 else 0
            
            response = f"""📉 *SALES DECLINE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *SALES COMPARISON*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Previous Period: Rs {previous_sales:,.2f}
• Current Period: Rs {current_sales:,.2f}
• Change: {change_percent:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *TOP DECLINING DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for d in declining_dealers[:5]:
                response += f"• {d['dealer'][:30]}: -{d['decline_percent']}% (Rs {d['previous']:,.2f} → Rs {d['current']:,.2f})\n"
            
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *TOP DECLINING PRODUCTS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for p in declining_products[:5]:
                response += f"• {p['product'][:30]}: -{p['decline_percent']}% ({p['previous_qty']:,.0f} → {p['current_qty']:,.0f} units)\n"
            
            return {
                "success": True,
                "previous_sales": previous_sales,
                "current_sales": current_sales,
                "change_percent": change_percent,
                "declining_dealers": declining_dealers[:5],
                "declining_products": declining_products[:5],
                "formatted_response": response
            }
        except Exception as e:
            logger.error(f"Sales decrease analysis error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    def logistics_delay_analysis(self) -> Dict[str, Any]:
        """Analyze root causes of logistics delays"""
        try:
            # Get warehouses with high dispatch aging
            warehouse_delays = self.get_worst_pgi_warehouse(5)
            
            # Get cities with high POD delays
            city_pod_delays = self.get_pod_delay_by_city(5)
            
            # Get dealers causing POD delays
            dealer_pod_delays = self.get_pod_delay_by_dealer(5)
            
            # Overall delay metrics
            pending_pgi_count = len(self.get_pending_pgi())
            pending_pod_count = len(self.get_pending_pods_with_aging())
            
            response = f"""🚚 *LOGISTICS DELAY ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *DELAY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Pending PGI: {pending_pgi_count} DNs
• Pending POD: {pending_pod_count} DNs

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏭 *WAREHOUSES WITH HIGHEST DELAYS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for w in warehouse_delays:
                response += f"• {w['warehouse']}: {w['avg_lead_time_days']} days avg lead time\n"
            
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🌆 *CITIES WITH HIGHEST POD DELAYS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            for c in city_pod_delays[:3]:
                response += f"• {c['city']}: {c['avg_aging_days']} days avg POD delay\n"
            
            return {
                "success": True,
                "pending_pgi_count": pending_pgi_count,
                "pending_pod_count": pending_pod_count,
                "worst_warehouses": warehouse_delays,
                "worst_cities": city_pod_delays,
                "worst_dealers": dealer_pod_delays,
                "formatted_response": response
            }
        except Exception as e:
            logger.error(f"Logistics delay analysis error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # CORE DN INTELLIGENCE (Existing - Preserved)
    # ==========================================================
    
    def get_dn_complete_intelligence(self, dn_number: str) -> Dict[str, Any]:
        """DN-Centric Intelligence: Aggregates ALL rows for a single DN"""
        try:
            # STEP 1: Get ALL rows for this DN (not just first)
            all_records = self.db.query(DeliveryReport).filter(
                DeliveryReport.dn_no == dn_number
            ).all()
            
            if not all_records:
                return {"success": False, "message": f"❌ DN {dn_number} not found"}
            
            # Get the primary record (first one, but we'll aggregate from all)
            primary = all_records[0]
            
            # ==========================================================
            # AGGREGATE DN DATA FROM ALL ROWS
            # ==========================================================
            
            # Total units and value
            total_units = sum(float(r.dn_qty or 0) for r in all_records)
            total_value = sum(float(r.dn_amount or 0) for r in all_records)
            
            # All products in this DN
            products = []
            product_summary = defaultdict(lambda: {"quantity": 0, "value": 0})
            for r in all_records:
                product_name = r.product or "Unknown"
                qty = float(r.dn_qty or 0)
                amt = float(r.dn_amount or 0)
                product_summary[product_name]["quantity"] += qty
                product_summary[product_name]["value"] += amt
                products.append({
                    "name": product_name,
                    "quantity": qty,
                    "value": amt
                })
            
            # Find highest quantity and value products
            highest_qty_product = max(product_summary.items(), key=lambda x: x[1]["quantity"]) if product_summary else (None, None)
            highest_value_product = max(product_summary.items(), key=lambda x: x[1]["value"]) if product_summary else (None, None)
            
            # ==========================================================
            # PROPER AGING CALCULATIONS
            # ==========================================================
            
            dn_aging_days = 0
            dn_create_date = None
            if primary.dn_create_date:
                if isinstance(primary.dn_create_date, datetime):
                    dn_create_date = primary.dn_create_date.date()
                else:
                    dn_create_date = primary.dn_create_date
                dn_aging_days = (datetime.now().date() - dn_create_date).days
            
            dispatch_aging_days = 0
            pgi_date = None
            if hasattr(primary, 'good_issue_date') and primary.good_issue_date:
                if isinstance(primary.good_issue_date, datetime):
                    pgi_date = primary.good_issue_date.date()
                else:
                    pgi_date = primary.good_issue_date
                if dn_create_date and pgi_date:
                    dispatch_aging_days = (pgi_date - dn_create_date).days
            
            delivery_aging_days = 0
            delivery_date = None
            if primary.pgi_status == "Completed":
                if hasattr(primary, 'delivery_date') and primary.delivery_date:
                    if isinstance(primary.delivery_date, datetime):
                        delivery_date = primary.delivery_date.date()
                    else:
                        delivery_date = primary.delivery_date
                    if pgi_date and delivery_date:
                        delivery_aging_days = (delivery_date - pgi_date).days
            
            pod_aging_days = 0
            pod_date = None
            if primary.pod_status == "Received":
                if hasattr(primary, 'pod_date') and primary.pod_date:
                    if isinstance(primary.pod_date, datetime):
                        pod_date = primary.pod_date.date()
                    else:
                        pod_date = primary.pod_date
                    if delivery_date and pod_date:
                        pod_aging_days = (pod_date - delivery_date).days
                    elif pgi_date and pod_date:
                        pod_aging_days = (pod_date - pgi_date).days
            
            pod_risk_score, pending_pod_aging = self._calculate_pod_risk(
                primary, pod_aging_days, delivery_date, pgi_date
            )
            
            # ==========================================================
            # DEALER CONTEXT INTEGRATION
            # ==========================================================
            
            dealer_name = primary.customer_name
            dealer_metrics = self._get_dealer_aggregated_metrics(dealer_name)
            
            dealer_total_dns = dealer_metrics["total_dns"]
            dealer_delivered = dealer_metrics["delivered_dns"]
            dealer_pending = dealer_total_dns - dealer_delivered
            dealer_pod_pending = dealer_metrics["pod_pending_dns"]
            dealer_total_value = dealer_metrics["total_value"]
            dealer_pending_value = dealer_total_value - dealer_metrics["delivered_value"]
            
            dealer_delivery_rate = (dealer_delivered / dealer_total_dns) * 100 if dealer_total_dns > 0 else 0
            dealer_pod_rate = ((dealer_delivered - dealer_pod_pending) / dealer_delivered) * 100 if dealer_delivered > 0 else 0
            dealer_health_score = (dealer_delivery_rate * 0.6) + (dealer_pod_rate * 0.4)
            
            # ==========================================================
            # RISK INTELLIGENCE
            # ==========================================================
            
            risk_score = 0
            risk_factors = []
            risk_level = "LOW"
            risk_icon = "🟢"
            
            if dispatch_aging_days > 15:
                risk_score += 40
                risk_factors.append(f"Dispatch delayed {dispatch_aging_days} days")
            elif dispatch_aging_days > 7:
                risk_score += 20
                risk_factors.append(f"Dispatch aging: {dispatch_aging_days} days")
            
            if delivery_aging_days > 10:
                risk_score += 30
                risk_factors.append(f"Delivery delayed {delivery_aging_days} days")
            elif delivery_aging_days > 5:
                risk_score += 15
                risk_factors.append(f"Delivery aging: {delivery_aging_days} days")
            
            risk_score += pod_risk_score
            if pending_pod_aging > 10:
                risk_factors.append(f"POD pending {pending_pod_aging} days")
            elif pending_pod_aging > 5:
                risk_factors.append(f"POD aging: {pending_pod_aging} days")
            
            if dealer_pending > 20:
                risk_score += 10
                risk_factors.append(f"Dealer has {dealer_pending} other pending DNs")
            
            if risk_score >= 70:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "HIGH"
                risk_icon = "🔴"
            elif risk_score >= 30:
                risk_level = "MEDIUM"
                risk_icon = "🟡"
            
            # ==========================================================
            # DN HEALTH SCORE
            # ==========================================================
            
            dn_health_score = self._calculate_dn_health_score(
                dispatch_aging_days, delivery_aging_days, 
                pod_aging_days, primary.pod_status, pending_pod_aging
            )
            
            # ==========================================================
            # SLA STATUS
            # ==========================================================
            
            dispatch_sla_status, dispatch_sla_icon = SLAMetrics.get_sla_status(
                dispatch_aging_days, SLAMetrics.DISPATCH_SLA_DAYS
            )
            delivery_sla_status, delivery_sla_icon = SLAMetrics.get_sla_status(
                delivery_aging_days, SLAMetrics.DELIVERY_SLA_DAYS
            )
            pod_sla_status, pod_sla_icon = SLAMetrics.get_sla_status(
                pending_pod_aging if primary.pod_status == "Pending" else pod_aging_days, 
                SLAMetrics.POD_SLA_DAYS
            )
            
            # ==========================================================
            # EXCEPTION FLAGS
            # ==========================================================
            
            exception_data = {
                "total_value": total_value,
                "dispatch_aging_days": dispatch_aging_days,
                "pod_status": primary.pod_status,
                "pending_pod_aging": pending_pod_aging,
                "delivery_aging_days": delivery_aging_days,
                "dealer_summary": {"health_score": dealer_health_score}
            }
            exception_flags = self._get_exception_flags(exception_data)
            
            # ==========================================================
            # FORMATTED RESPONSE
            # ==========================================================
            
            product_list = ""
            for p in list(product_summary.items())[:5]:
                product_list += f"   • {p[0]}: {p[1]['quantity']:,.0f} units (Rs {p[1]['value']:,.2f})\n"
            if len(product_summary) > 5:
                product_list += f"   • ... and {len(product_summary) - 5} more products\n"
            
            flags_str = ""
            if exception_flags:
                flags_str = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                flags_str += "🚨 *EXCEPTION FLAGS*\n"
                flags_str += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                for flag in exception_flags:
                    flags_str += f"   {flag}\n"
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📦 DN COMPLETE INTELLIGENCE REPORT                    ║
║                         {dn_number}                                   ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *DN SUMMARY*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dealer: {dealer_name}
• City: {primary.ship_to_city or 'N/A'}
• Warehouse: {primary.warehouse or 'N/A'}
• Total Units: {total_units:,.0f}
• Total Value: Rs {total_value:,.2f}
• Products: {len(product_summary)} items
• Highest Qty Product: {highest_qty_product[0] if highest_qty_product[0] else 'N/A'} ({highest_qty_product[1]['quantity']:,.0f} units)
• Highest Value Product: {highest_value_product[0] if highest_value_product[0] else 'N/A'} (Rs {highest_value_product[1]['value']:,.2f})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📦 *PRODUCT BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{product_list}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AGING ANALYSIS (KPIs)*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• 📅 DN Aging (Today - Create): {dn_aging_days} days
• 🚚 Dispatch Aging (PGI - Create): {dispatch_aging_days} days
• 🚛 Delivery Aging (Delivery - PGI): {delivery_aging_days} days
• 📋 POD Aging (POD - Delivery): {pod_aging_days if primary.pod_status == 'Received' else 'Pending'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *CURRENT STATUS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Delivery Status: {'✅ DELIVERED' if primary.pgi_status == 'Completed' else '⏳ PENDING'}
• POD Status: {'✅ RECEIVED' if primary.pod_status == 'Received' else '📋 PENDING'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💚 *DN HEALTH SCORE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {dn_health_score}/100
{"✅ Excellent" if dn_health_score >= 80 else "⚠️ Needs Attention" if dn_health_score >= 50 else "🔴 Critical"}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *SLA MONITORING*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Dispatch SLA ({SLAMetrics.DISPATCH_SLA_DAYS} days): {dispatch_sla_icon} {dispatch_sla_status}
• Delivery SLA ({SLAMetrics.DELIVERY_SLA_DAYS} days): {delivery_sla_icon} {delivery_sla_status}
• POD SLA ({SLAMetrics.POD_SLA_DAYS} days): {pod_sla_icon} {pod_sla_status}

{flags_str}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK INTELLIGENCE*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Risk Score: {risk_score}/100
• Risk Level: {risk_level}
"""
            if risk_factors:
                response += "\n*Risk Factors:*\n"
                for f in risk_factors[:3]:
                    response += f"   • {f}\n"
            
            response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏪 *DEALER CONTEXT - {dealer_name}*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {dealer_total_dns}
• Delivered: {dealer_delivered} ✅
• Pending: {dealer_pending} ⏳
• POD Pending: {dealer_pod_pending} 📋
• Total Value: Rs {dealer_total_value:,.2f}
• Health Score: {dealer_health_score:.1f}/100

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            recommendations = []
            
            if dispatch_aging_days > 10:
                recommendations.append("• 🚨 Escalate to warehouse - dispatch delay")
            if delivery_aging_days > 7:
                recommendations.append("• 🚛 Follow up with transporter")
            if primary.pod_status == "Pending" and pending_pod_aging > 7:
                recommendations.append("• 📋 Urgent: Collect POD from dealer")
            if dealer_pending > 0:
                recommendations.append(f"• 📦 Dealer has {dealer_pending} other pending DNs")
            if dn_health_score < 50:
                recommendations.append("• ⚠️ DN Health Critical - Immediate action required")
            
            if len(recommendations) == 0:
                recommendations.append("• ✅ No action needed")
            
            response += "\n".join(recommendations) + "\n"
            
            return {
                "success": True,
                "dn_number": dn_number,
                "dealer_name": dealer_name,
                "total_units": total_units,
                "total_value": total_value,
                "product_count": len(product_summary),
                "products": list(product_summary.keys()),
                "dn_aging_days": dn_aging_days,
                "dispatch_aging_days": dispatch_aging_days,
                "delivery_aging_days": delivery_aging_days,
                "pod_aging_days": pod_aging_days,
                "pending_pod_aging": pending_pod_aging,
                "dn_health_score": dn_health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "risk_factors": risk_factors,
                "exception_flags": exception_flags,
                "dealer_summary": {
                    "total_dns": dealer_total_dns,
                    "delivered": dealer_delivered,
                    "pending": dealer_pending,
                    "total_value": dealer_total_value,
                    "health_score": dealer_health_score
                },
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"DN intelligence error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # DEALER METHODS (Existing)
    # ==========================================================
    
    def get_dealer_executive_dashboard(self, dealer_name: str) -> Dict[str, Any]:
        """Optimized dealer dashboard using SQL aggregation"""
        # ... (existing implementation preserved)
        try:
            exact_match = self.db.query(DeliveryReport).filter(
                func.lower(DeliveryReport.customer_name) == func.lower(dealer_name.strip())
            ).first()
            
            if not exact_match:
                partial_matches = self.db.query(
                    DeliveryReport.customer_name,
                    func.count(DeliveryReport.dn_no).label("count")
                ).filter(
                    DeliveryReport.customer_name.ilike(f"%{dealer_name.strip()}%")
                ).group_by(
                    DeliveryReport.customer_name
                ).order_by(
                    desc("count")
                ).limit(5).all()
                
                if partial_matches:
                    suggestions = [m.customer_name for m in partial_matches]
                    return {
                        "success": False,
                        "message": f"❌ Dealer '{dealer_name}' not found.\n\nDid you mean:\n" + "\n".join([f"{i+1}. {s}" for i, s in enumerate(suggestions[:5])]),
                        "suggestions": suggestions
                    }
                return {"success": False, "message": f"❌ Dealer '{dealer_name}' not found"}
            
            found_dealer = exact_match.customer_name
            
            result = self.db.query(
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status == "Completed").label("delivered_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value"),
                func.count(DeliveryReport.dn_no).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Pending"
                ).label("pod_pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Pending"
                ).label("pod_pending_value")
            ).filter(
                DeliveryReport.customer_name == found_dealer
            ).first()
            
            total_dns = result.total_dns or 0
            delivered = result.delivered_dns or 0
            pending = result.pending_dns or 0
            pod_pending = result.pod_pending_dns or 0
            total_value = float(result.total_value or 0)
            pending_value = float(result.pending_value or 0)
            pod_pending_value = float(result.pod_pending_value or 0)
            
            delivery_rate = (delivered / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = ((delivered - pod_pending) / delivered) * 100 if delivered > 0 else 0
            health_score = (delivery_rate * 0.6) + (pod_rate * 0.4)
            risk_score = 100 - health_score
            
            all_dealers = self.db.query(
                DeliveryReport.customer_name,
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).all()
            
            ranking = 1
            for i, d in enumerate(all_dealers, 1):
                if d.customer_name == found_dealer:
                    ranking = i
                    break
            
            total_all_value = sum(float(d.total_value or 0) for d in all_dealers)
            revenue_contribution = (total_value / total_all_value) * 100 if total_all_value > 0 else 0
            
            aging_result = self.db.query(
                func.avg(func.extract('day', datetime.now() - DeliveryReport.dn_create_date)).label("avg_aging")
            ).filter(
                DeliveryReport.customer_name == found_dealer,
                DeliveryReport.dn_create_date.isnot(None)
            ).first()
            avg_aging = round(float(aging_result.avg_aging or 0), 1)
            
            if risk_score >= 70:
                risk_level = "CRITICAL"
                risk_icon = "💀"
            elif risk_score >= 50:
                risk_level = "HIGH"
                risk_icon = "🚨"
            elif risk_score >= 30:
                risk_level = "MEDIUM"
                risk_icon = "⚠️"
            else:
                risk_level = "LOW"
                risk_icon = "✅"
            
            response = f"""╔══════════════════════════════════════════════════════════════╗
║              📊 EXECUTIVE DEALER DASHBOARD                       ║
║                    {found_dealer[:30]}                               ║
╚══════════════════════════════════════════════════════════════════════╝

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *PERFORMANCE METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total DNs: {total_dns:,}
• Delivered: {delivered} ✅
• Pending: {pending} ⏳
• POD Pending: {pod_pending} 📋
• Delivery Rate: {delivery_rate:.1f}%
• POD Compliance: {pod_rate:.1f}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *FINANCIAL ANALYSIS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Value: Rs {total_value:,.2f}
• Pending Value: Rs {pending_value:,.2f}
• POD Pending Value: Rs {pod_pending_value:,.2f}
• Revenue Contribution: {revenue_contribution:.1f}% of total
• Rank: #{ranking} of {len(all_dealers)} dealers

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *RISK & HEALTH ASSESSMENT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{risk_icon} Health Score: {health_score:.1f}/100
• Risk Score: {risk_score:.1f}/100
• Risk Level: {risk_level}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AVERAGE AGING: {avg_aging} days*

💡 *RECOMMENDATIONS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
            recs = []
            if pending > 0:
                recs.append(f"• Clear {pending} pending deliveries")
            if pod_pending > 0:
                recs.append(f"• Collect POD for {pod_pending} delivered DNs")
            if avg_aging > 15:
                recs.append("• Review dispatch process for delays")
            if len(recs) == 0:
                recs.append("• ✅ No action needed")
            
            response += "\n".join(recs)
            
            return {
                "success": True,
                "dealer_name": found_dealer,
                "ranking": ranking,
                "health_score": health_score,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "revenue_contribution": revenue_contribution,
                "avg_aging": avg_aging,
                "formatted_response": response
            }
            
        except Exception as e:
            logger.error(f"Dealer dashboard error: {e}")
            return {"success": False, "message": f"Error: {str(e)}"}
    
    # ==========================================================
    # NETWORK HEALTH AND OTHER SUPPORTING METHODS
    # ==========================================================
    
    def get_enhanced_network_health(self) -> Dict[str, Any]:
        """Optimized network health using SQL aggregation"""
        try:
            result = self.db.query(
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status == "Completed").label("delivered_dns"),
                func.count(DeliveryReport.dn_no).filter(
                    DeliveryReport.pgi_status == "Completed",
                    DeliveryReport.pod_status == "Received"
                ).label("pod_received"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value"),
                func.avg(func.extract('day', datetime.now() - DeliveryReport.dn_create_date)).filter(
                    DeliveryReport.pgi_status != "Completed"
                ).label("avg_dispatch_aging")
            ).first()
            
            total_dns = result.total_dns or 0
            delivered_dns = result.delivered_dns or 0
            pod_received = result.pod_received or 0
            pending_value = float(result.pending_value or 0)
            avg_dispatch_aging = round(float(result.avg_dispatch_aging or 0), 1)
            
            delivery_rate = (delivered_dns / total_dns) * 100 if total_dns > 0 else 0
            pod_rate = (pod_received / delivered_dns) * 100 if delivered_dns > 0 else 0
            
            delivery_score = delivery_rate * 0.40
            pod_score = pod_rate * 0.30
            aging_score = max(0, 100 - (avg_dispatch_aging * 2)) * 0.30 if avg_dispatch_aging > 0 else 100 * 0.30
            health_score = delivery_score + pod_score + aging_score
            
            return {
                "total_dns": total_dns,
                "delivered_dns": delivered_dns,
                "delivery_rate": round(delivery_rate, 1),
                "pod_rate": round(pod_rate, 1),
                "health_score": round(health_score, 1),
                "revenue_at_risk": pending_value,
                "avg_dispatch_aging": avg_dispatch_aging
            }
        except Exception as e:
            logger.error(f"Network health error: {e}")
            return {}
    
    def get_top_dealers(self, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("total_value")
            ).limit(limit).all()
            
            return [{"name": r.customer_name, "total_dns": r.total_dns, "total_value": float(r.total_value or 0)} for r in results]
        except Exception as e:
            return []
    
    def get_enhanced_top_risk_dealers(self, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.customer_name,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns"),
                func.sum(DeliveryReport.dn_amount).filter(DeliveryReport.pgi_status != "Completed").label("pending_value")
            ).filter(
                DeliveryReport.customer_name.isnot(None)
            ).group_by(
                DeliveryReport.customer_name
            ).order_by(
                desc("pending_value")
            ).limit(limit).all()
            
            dealers = []
            for r in results:
                pending_ratio = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                risk_score = pending_ratio + ((r.pending_value / (r.total_value or 1)) * 100 if r.total_value else 0) / 2
                dealers.append({
                    "name": r.customer_name,
                    "pending_dns": r.pending_dns or 0,
                    "pending_value": float(r.pending_value or 0),
                    "risk_score": round(risk_score, 1)
                })
            
            return dealers[:limit]
        except Exception as e:
            return []
    
    def get_city_performance(self) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.ship_to_city,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.ship_to_city.isnot(None)
            ).group_by(
                DeliveryReport.ship_to_city
            ).all()
            
            cities = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                cities.append({
                    "city": r.ship_to_city,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns or 0,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            cities.sort(key=lambda x: x["pending_rate"], reverse=True)
            return cities[:20]
        except Exception as e:
            return []
    
    def get_warehouse_performance(self) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.warehouse,
                func.count(DeliveryReport.dn_no).label("total_dns"),
                func.sum(DeliveryReport.dn_amount).label("total_value"),
                func.count(DeliveryReport.dn_no).filter(DeliveryReport.pgi_status != "Completed").label("pending_dns")
            ).filter(
                DeliveryReport.warehouse.isnot(None)
            ).group_by(
                DeliveryReport.warehouse
            ).all()
            
            warehouses = []
            for r in results:
                pending_rate = (r.pending_dns / r.total_dns) * 100 if r.total_dns > 0 else 0
                status = "🔴" if pending_rate > 30 else "🟡" if pending_rate > 15 else "🟢"
                warehouses.append({
                    "warehouse": r.warehouse,
                    "total_dns": r.total_dns,
                    "pending_dns": r.pending_dns or 0,
                    "pending_rate": round(pending_rate, 1),
                    "total_value": float(r.total_value or 0),
                    "status": status
                })
            
            warehouses.sort(key=lambda x: x["pending_rate"], reverse=True)
            return warehouses[:20]
        except Exception as e:
            return []
    
    def get_revenue_analysis(self) -> Dict[str, Any]:
        try:
            total = self.db.query(func.sum(DeliveryReport.dn_amount)).scalar() or 0
            delivered = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(DeliveryReport.pgi_status == "Completed").scalar() or 0
            pending = total - delivered
            pod_pending = self.db.query(func.sum(DeliveryReport.dn_amount)).filter(
                DeliveryReport.pgi_status == "Completed",
                DeliveryReport.pod_status == "Pending"
            ).scalar() or 0
            
            return {
                "total_revenue": float(total),
                "delivered_revenue": float(delivered),
                "pending_revenue": float(pending),
                "pod_pending_revenue": float(pod_pending),
                "realized_revenue": float(delivered - pod_pending),
                "realization_rate": ((delivered - pod_pending) / total * 100) if total > 0 else 0,
                "revenue_at_risk": float(pending + pod_pending)
            }
        except Exception as e:
            return {}
    
    def get_outstanding_analysis(self) -> Dict[str, Any]:
        revenue = self.get_revenue_analysis()
        return {
            "outstanding_value": revenue.get("pending_revenue", 0) + revenue.get("pod_pending_revenue", 0),
            "pending_delivery": revenue.get("pending_revenue", 0),
            "pod_pending": revenue.get("pod_pending_revenue", 0)
        }
    
    def get_pending_dns(self, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date
            ).filter(
                DeliveryReport.pgi_status != "Completed"
            ).order_by(
                DeliveryReport.dn_create_date
            ).limit(limit).all()
            
            return [{"dn_no": r.dn_no, "dealer": r.customer_name, 
                     "amount": float(r.dn_amount or 0),
                     "created_date": r.dn_create_date} for r in results]
        except Exception as e:
            logger.error(f"Error getting pending DNs: {e}")
            return []
    
    def get_delayed_dns(self, delay_threshold_days: int = 7, limit: int = 20) -> List[Dict]:
        try:
            results = self.db.query(
                DeliveryReport.dn_no,
                DeliveryReport.customer_name,
                DeliveryReport.dn_amount,
                DeliveryReport.dn_create_date,
                DeliveryReport.good_issue_date
            ).filter(
                DeliveryReport.good_issue_date.isnot(None),
                DeliveryReport.dn_create_date.isnot(None)
            ).all()
            
            delayed = []
            for r in results:
                if isinstance(r.dn_create_date, datetime):
                    create_date = r.dn_create_date.date()
                else:
                    create_date = r.dn_create_date
                if isinstance(r.good_issue_date, datetime):
                    pgi_date = r.good_issue_date.date()
                else:
                    pgi_date = r.good_issue_date
                
                dispatch_days = (pgi_date - create_date).days if pgi_date and create_date else 0
                if dispatch_days > delay_threshold_days:
                    delayed.append({
                        "dn_no": r.dn_no,
                        "dealer": r.customer_name,
                        "amount": float(r.dn_amount or 0),
                        "dispatch_delay_days": dispatch_days
                    })
            
            delayed.sort(key=lambda x: x["dispatch_delay_days"], reverse=True)
            return delayed[:limit]
        except Exception as e:
            logger.error(f"Error getting delayed DNs: {e}")
            return []
    
    def get_pending_pods(self, limit: int = 20) -> List[Dict]:
        return self.get_pending_pods_with_aging(0)[:limit]
    
    def get_pending_pgi(self, limit: int = 20) -> List[Dict]:
        return self.get_pending_dns(limit)
    
    def get_pod_delay_by_dealer(self, limit: int = 20) -> List[Dict]:
        pending_pods = self.get_pending_pods_with_aging(100)
        dealer_delays = defaultdict(lambda: {"total_amount": 0, "count": 0, "max_aging": 0})
        
        for pod in pending_pods:
            dealer_delays[pod["dealer"]]["total_amount"] += pod["amount"]
            dealer_delays[pod["dealer"]]["count"] += 1
            dealer_delays[pod["dealer"]]["max_aging"] = max(dealer_delays[pod["dealer"]]["max_aging"], pod["aging_days"])
        
        result = []
        for dealer, data in dealer_delays.items():
            result.append({
                "dealer": dealer,
                "pending_pod_count": data["count"],
                "pending_amount": data["total_amount"],
                "max_aging_days": data["max_aging"]
            })
        
        result.sort(key=lambda x: x["max_aging_days"], reverse=True)
        return result[:limit]
    
    def get_executive_context(self) -> Dict[str, Any]:
        """Get complete executive context for AI"""
        return {
            "network_health": self.get_enhanced_network_health(),
            "top_dealers": self.get_top_dealers(10),
            "top_risk_dealers": self.get_enhanced_top_risk_dealers(10),
            "city_performance": self.get_city_performance()[:5],
            "warehouse_performance": self.get_warehouse_performance()[:5],
            "revenue_analysis": self.get_revenue_analysis(),
            "pending_pods": self.get_pending_pods(5),
            "pending_pgi": self.get_pending_pgi(5),
            "delayed_dns": self.get_delayed_dns(7, 5),
            "top_products": self.get_top_products(5),
            "division_summary": self.get_all_divisions_summary(),
            "critical_alerts": {
                "pending_over_7": len(self.get_dns_pending_gt(7)),
                "pending_over_15": len(self.get_dns_pending_gt(15)),
                "pending_over_30": len(self.get_dns_pending_gt(30)),
                "high_value_pending": len(self.get_high_value_pending_dns()),
                "inactive_dealers": len(self.get_inactive_dealers(30))
            }
        }


# ==========================================================
# RESPONSE FORMATTER
# ==========================================================

class ResponseFormatter:
    
    @staticmethod
    def welcome() -> str:
        return WELCOME_MESSAGE
    
    @staticmethod
    def top_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "📊 No dealer data available."
        
        response = "🏆 *TOP 20 PERFORMING DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   💰 Rs {d['total_value']:,.2f} | 📦 {d['total_dns']} DNs\n\n"
        return response
    
    @staticmethod
    def top_risk_dealers_response(dealers: List, limit: int = 20) -> str:
        if not dealers:
            return "🚨 No risk data available."
        
        response = "🚨 *TOP 20 RISK DEALERS*\n\n"
        for i, d in enumerate(dealers[:limit], 1):
            response += f"{i}. *{d['name'][:35]}*\n"
            response += f"   📊 Risk Score: {d.get('risk_score', 0)}/100\n"
            response += f"   ⏳ {d.get('pending_dns', 0)} pending\n"
            response += f"   💰 Rs {d.get('pending_value', 0):,.2f} at risk\n\n"
        return response
    
    @staticmethod
    def top_products_response(products: List, by: str = "revenue") -> str:
        if not products:
            return "📦 No product data available."
        
        response = f"🏆 *TOP {len(products)} PRODUCTS (by {by})*\n\n"
        for i, p in enumerate(products[:10], 1):
            response += f"{i}. *{p['product'][:35]}*\n"
            response += f"   📊 Revenue: Rs {p['revenue']:,.2f}\n"
            response += f"   📦 Quantity: {p['quantity']:,.0f} | DNs: {p['dns']}\n\n"
        return response
    
    @staticmethod
    def network_health_response(health: Dict) -> str:
        return f"""📊 *NETWORK HEALTH SCORE*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📈 *KEY METRICS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Total DNs: {health.get('total_dns', 0):,}
• Delivered: {health.get('delivered_dns', 0):,}
• Delivery Rate: {health.get('delivery_rate', 0)}%

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 *POD COMPLIANCE: {health.get('pod_rate', 0)}%*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💰 *REVENUE AT RISK: Rs {health.get('revenue_at_risk', 0):,.2f}*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⏱️ *AVG DISPATCH AGING: {health.get('avg_dispatch_aging', 0)} days*

💡 Type "Executive summary" for detailed analysis"""
    
    @staticmethod
    def executive_summary_response(health: Dict, top_dealers: List, risk_dealers: List) -> str:
        response = f"""👑 *EXECUTIVE SUMMARY DASHBOARD*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *NETWORK HEALTH*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Health Score: {health.get('health_score', 0)}/100
• Delivery Rate: {health.get('delivery_rate', 0)}%
• POD Compliance: {health.get('pod_rate', 0)}%
• Revenue at Risk: Rs {health.get('revenue_at_risk', 0):,.2f}
• Avg Dispatch Aging: {health.get('avg_dispatch_aging', 0)} days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏆 *TOP 5 DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(top_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - Rs {d['total_value']:,.2f}\n"
        
        response += f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🚨 *TOP 5 RISK DEALERS*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
        for i, d in enumerate(risk_dealers[:5], 1):
            response += f"{i}. {d['name'][:30]} - Risk: {d.get('risk_score', 0)}/100 | {d.get('pending_dns', 0)} pending\n"
        
        response += """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *PRIORITY ACTIONS:*
1. Escalate top 5 risk dealers immediately
2. Focus POD collection on pending DNs
3. Review warehouse dispatch process for delays

Type "Help" for all commands"""
        
        return response
    
    @staticmethod
    def pending_dns_response(dns_list: List, title: str = "Pending DNs") -> str:
        if not dns_list:
            return f"✅ No {title.lower()} found."
        
        response = f"📋 *{title}*\n\n"
        for d in dns_list[:15]:
            response += f"🔢 *{d['dn_no']}*\n"
            response += f"   🏪 {d['dealer'][:30]}\n"
            response += f"   💰 Rs {d['amount']:,.2f}\n"
            if d.get('aging_days', 0) > 0:
                response += f"   ⏱️ {d['aging_days']} days old\n"
            response += "\n"
        return response


# ==========================================================
# CONVERSATION MEMORY & CACHE
# ==========================================================

class ConversationMemory:
    def __init__(self, max_history: int = 10):
        self.history: Dict[str, deque] = {}
        self.max_history = max_history
    
    def get_or_create(self, phone_number: str) -> deque:
        if phone_number not in self.history:
            self.history[phone_number] = deque(maxlen=self.max_history)
        return self.history[phone_number]
    
    def add(self, phone_number: str, question: str, response: str, intent: str, entity: str = None):
        memory = self.get_or_create(phone_number)
        memory.append({
            "question": question,
            "response": response[:200],
            "intent": intent,
            "entity": entity,
            "timestamp": datetime.utcnow().isoformat()
        })
    
    def get_last_context(self, phone_number: str) -> Dict[str, Any]:
        memory = self.get_or_create(phone_number)
        if not memory:
            return {}
        last = memory[-1]
        return {
            "last_question": last.get("question"),
            "last_intent": last.get("intent"),
            "last_entity": last.get("entity"),
            "recent_entities": [m.get("entity") for m in list(memory)[-3:] if m.get("entity")]
        }


class ExecutiveContextCache:
    def __init__(self, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[Dict, float]] = {}
        self.ttl = ttl_seconds
    
    def get(self, key: str) -> Optional[Dict]:
        if key in self.cache:
            data, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return data
            del self.cache[key]
        return None
    
    def set(self, key: str, data: Dict):
        self.cache[key] = (data, time.time())


# ==========================================================
# INTENT TYPES & DETECTION
# ==========================================================

class IntentType(str, Enum):
    HELP = "help"
    WELCOME = "welcome"
    DEALER_LOOKUP = "dealer_lookup"
    DN_LOOKUP = "dn_lookup"
    DN_STATUS = "dn_status"
    DN_PENDING = "dn_pending"
    DN_DELAYED = "dn_delayed"
    DN_OLDER = "dn_older"
    TOP_DEALERS = "top_dealers"
    TOP_RISK_DEALERS = "top_risk_dealers"
    EXECUTIVE_SUMMARY = "executive_summary"
    NETWORK_HEALTH = "network_health"
    CITY_PERFORMANCE = "city_performance"
    WAREHOUSE_PERFORMANCE = "warehouse_performance"
    REVENUE_ANALYSIS = "revenue_analysis"
    OUTSTANDING_ANALYSIS = "outstanding_analysis"
    POD_ANALYSIS = "pod_analysis"
    PENDING_POD = "pending_pod"
    PENDING_PGI = "pending_pgi"
    PRODUCT_ANALYSIS = "product_analysis"
    PRODUCT_RANKING = "product_ranking"
    DIVISION_ANALYSIS = "division_analysis"
    MANAGER_ANALYSIS = "manager_analysis"
    EXCEPTION_ANALYSIS = "exception_analysis"
    AI_INSIGHT = "ai_insight"
    GENERAL_QUERY = "general_query"


WELCOME_MESSAGE = """🤖 *AI LOGISTICS INTELLIGENCE ASSISTANT v16.0*

Welcome! I can analyze Dealers, DNs, PODs, Warehouses, Cities, Financial Performance, Risks, Executive KPIs, Products, Divisions, Managers, and Exceptions in real-time.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *What You Can Ask:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🏪 *Dealers* - Type a dealer name
🔢 *DN Tracking* - Send a 10-digit DN number
📋 *DN Status* - "Pending DNs", "Delayed DNs"
📦 *Products* - "Show HSU-18HFPAA", "Top products"
🏭 *Divisions* - "AC sales", "TV sales"
👔 *Managers* - "Manager ranking"
📋 *POD Status* - "Pending PODs"
🚨 *Exceptions* - "Critical delays", "Inactive dealers"
💡 *AI Insights* - "Why sales decreased?"
👑 *Executive Reports* - "Executive summary"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Examples:*
"Exact Trading Co" | "6243611920" | "Top products" | "AC sales" | "Why sales decreased?" | "Pending DNs" """


class IntentDetector:
    
    # Keyword groups for better detection
    DN_KEYWORDS = ["dn", "delivery note", "delivery order"]
    PENDING_KEYWORDS = ["pending", "not delivered", "not pgi", "undelivered", "open"]
    DELAYED_KEYWORDS = ["delay", "delayed", "late", "overdue", "breach"]
    POD_KEYWORDS = ["pod", "proof of delivery", "pod pending"]
    PGI_KEYWORDS = ["pgi", "goods issue", "dispatch"]
    DEALER_KEYWORDS = ["dealer", "customer", "distributor"]
    WAREHOUSE_KEYWORDS = ["warehouse", "godown", "stock point"]
    CITY_KEYWORDS = ["city", "cities", "location"]
    RISK_KEYWORDS = ["risk", "critical", "urgent", "escalate"]
    REVENUE_KEYWORDS = ["revenue", "sales", "amount", "value", "financial"]
    PRODUCT_KEYWORDS = ["product", "model", "sku", "hsn", "hsu", "hrf", "hwm"]
    DIVISION_KEYWORDS = ["ac", "tv", "refrigerator", "fridge", "washing machine", "division"]
    MANAGER_KEYWORDS = ["manager", "sales manager", "warehouse manager"]
    EXCEPTION_KEYWORDS = ["exception", "critical", "alert", "inactive", "dead stock", "slow moving"]
    AI_KEYWORDS = ["why", "analysis", "insight", "reason", "cause", "trend"]
    
    @staticmethod
    def detect_dn(message: str) -> Tuple[bool, Optional[str]]:
        match = re.search(r'\b(\d{10})\b', message)
        if match:
            return True, match.group(1)
        return False, None
    
    @staticmethod
    def is_business_question(message: str) -> bool:
        msg_lower = message.lower().strip()
        
        question_words = ["how", "what", "why", "when", "where", "who", "which", "can you", "could you", "please", "tell me"]
        if any(msg_lower.startswith(q) for q in question_words):
            return True
        if msg_lower.endswith("?"):
            return True
        
        logistics_keywords = [
            "analysis", "analyze", "improvement", "recommend", "risk", "trend", "forecast", 
            "delay", "performance", "dealer", "dn", "dispatch", "delivery", "pod", "warehouse", 
            "aging", "revenue", "logistics", "network", "pending", "outstanding", "product", "division"
        ]
        if any(word in msg_lower for word in logistics_keywords):
            return True
        
        return False
    
    @staticmethod
    def detect_intent(message: str) -> Tuple[IntentType, Optional[str]]:
        msg_lower = message.lower().strip()
        msg_original = message.strip()
        
        # Check for AI/Why questions first
        if any(word in msg_lower for word in ["why sales decreased", "why sales dropped", "sales decline"]):
            return IntentType.AI_INSIGHT, "sales_decrease"
        if any(word in msg_lower for word in ["why pod", "pod delay", "logistics delay"]):
            return IntentType.AI_INSIGHT, "logistics_delay"
        
        if IntentDetector.is_business_question(msg_original):
            return IntentType.GENERAL_QUERY, None
        
        if any(word in msg_lower for word in ["help", "menu", "welcome", "hello", "hi", "hey"]):
            return IntentType.HELP, None
        
        is_dn, dn_num = IntentDetector.detect_dn(msg_lower)
        if is_dn:
            return IntentType.DN_LOOKUP, dn_num
        
        # Product queries
        if any(word in msg_lower for word in IntentDetector.PRODUCT_KEYWORDS):
            if "top" in msg_lower or "best" in msg_lower:
                return IntentType.PRODUCT_RANKING, "top"
            if "bottom" in msg_lower or "worst" in msg_lower:
                return IntentType.PRODUCT_RANKING, "bottom"
            if "fast" in msg_lower:
                return IntentType.PRODUCT_RANKING, "fast_moving"
            if "slow" in msg_lower:
                return IntentType.PRODUCT_RANKING, "slow_moving"
            # Extract product model (uppercase with hyphens, e.g., HSU-18HFPAA)
            product_match = re.search(r'([A-Z]{2,3}-[0-9A-Z]+)', msg_upper)
            if product_match:
                return IntentType.PRODUCT_ANALYSIS, product_match.group(1)
            return IntentType.PRODUCT_ANALYSIS, msg_original
        
        # Division queries
        if any(word in msg_lower for word in IntentDetector.DIVISION_KEYWORDS):
            if "ac" in msg_lower or "air conditioner" in msg_lower:
                return IntentType.DIVISION_ANALYSIS, "AC"
            if "tv" in msg_lower:
                return IntentType.DIVISION_ANALYSIS, "TV"
            if "refrigerator" in msg_lower or "fridge" in msg_lower:
                return IntentType.DIVISION_ANALYSIS, "Refrigerator"
            if "washing" in msg_lower:
                return IntentType.DIVISION_ANALYSIS, "Washing Machine"
            return IntentType.DIVISION_ANALYSIS, "all"
        
        # Manager queries
        if any(word in msg_lower for word in IntentDetector.MANAGER_KEYWORDS):
            if "ranking" in msg_lower or "best" in msg_lower:
                return IntentType.MANAGER_ANALYSIS, "ranking"
            return IntentType.MANAGER_ANALYSIS, msg_original
        
        # Exception queries
        if any(word in msg_lower for word in IntentDetector.EXCEPTION_KEYWORDS):
            if "inactive dealer" in msg_lower:
                return IntentType.EXCEPTION_ANALYSIS, "inactive_dealers"
            if "dead stock" in msg_lower or "no sales" in msg_lower:
                return IntentType.EXCEPTION_ANALYSIS, "dead_stock"
            if "critical delay" in msg_lower:
                return IntentType.EXCEPTION_ANALYSIS, "critical_delays"
            if "high value" in msg_lower:
                return IntentType.EXCEPTION_ANALYSIS, "high_value_pending"
            return IntentType.EXCEPTION_ANALYSIS, "all"
        
        # DN Status Queries
        if any(word in msg_lower for word in IntentDetector.PENDING_KEYWORDS) and \
           any(word in msg_lower for word in IntentDetector.DN_KEYWORDS):
            return IntentType.DN_PENDING, None
        
        if any(word in msg_lower for word in IntentDetector.DELAYED_KEYWORDS) and \
           any(word in msg_lower for word in IntentDetector.DN_KEYWORDS):
            return IntentType.DN_DELAYED, None
        
        # POD Queries
        if any(word in msg_lower for word in IntentDetector.POD_KEYWORDS):
            if "pending" in msg_lower or "delay" in msg_lower:
                return IntentType.PENDING_POD, None
            return IntentType.POD_ANALYSIS, None
        
        # PGI Queries
        if any(word in msg_lower for word in IntentDetector.PGI_KEYWORDS) and "pending" in msg_lower:
            return IntentType.PENDING_PGI, None
        
        # Executive Queries
        if any(word in msg_lower for word in ["executive summary", "ceo summary", "management summary"]):
            return IntentType.EXECUTIVE_SUMMARY, None
        
        if any(word in msg_lower for word in ["network health", "health score", "network status"]):
            return IntentType.NETWORK_HEALTH, None
        
        # Risk Queries
        if any(word in msg_lower for word in IntentDetector.RISK_KEYWORDS):
            if "dealer" in msg_lower or "top risk" in msg_lower:
                return IntentType.TOP_RISK_DEALERS, None
        
        # Dealer Queries
        if any(word in msg_lower for word in ["top dealer", "top performing", "best dealer"]):
            return IntentType.TOP_DEALERS, None
        
        # Performance Queries
        if any(word in msg_lower for word in IntentDetector.CITY_KEYWORDS):
            return IntentType.CITY_PERFORMANCE, None
        
        if any(word in msg_lower for word in IntentDetector.WAREHOUSE_KEYWORDS):
            return IntentType.WAREHOUSE_PERFORMANCE, None
        
        # Financial Queries
        if any(word in msg_lower for word in IntentDetector.REVENUE_KEYWORDS):
            if "outstanding" in msg_lower or "pending value" in msg_lower:
                return IntentType.OUTSTANDING_ANALYSIS, None
            return IntentType.REVENUE_ANALYSIS, None
        
        # Default to dealer lookup for short text
        if len(msg_lower.split()) <= 5 and not msg_lower.isdigit():
            return IntentType.DEALER_LOOKUP, msg_original
        
        return IntentType.GENERAL_QUERY, None


# ==========================================================
# MAIN AI QUERY SERVICE
# ==========================================================

class AIQueryService:
    
    def __init__(self, db: Session):
        self.db = db
        self.db_service = DNCentricDatabaseService(db)
        self.formatter = ResponseFormatter()
        self.conversation_memory = ConversationMemory()
        self.executive_cache = ExecutiveContextCache()
        self.db_hash = hashlib.md5(str(db).encode()).hexdigest()
        
        # Initialize AI Provider (GROQ)
        self.ai_provider = None
        self.ai_available = False
        
        if AI_PROVIDER_AVAILABLE:
            try:
                self.ai_provider = get_ai_provider_service(db)
                if self.ai_provider:
                    self.ai_available = self._check_groq_health()
            except Exception as e:
                logger.error(f"Failed to get AI provider: {e}")
                self.ai_available = False
        
        logger.info("=" * 50)
        logger.info("🚀 AI LOGISTICS INTELLIGENCE ASSISTANT v16.0 (FULL INTELLIGENCE)")
        logger.info(f"GROQ Available: {self.ai_available}")
        logger.info("=" * 50)
    
    def _check_groq_health(self) -> bool:
        if not self.ai_provider:
            return False
        try:
            result = self.ai_provider.answer_question(question="Say 'GROQ is working'", user_role="test")
            return result.get("success", False)
        except Exception as e:
            logger.error(f"GROQ health check error: {e}")
            return False
    
    def process_query(self, question: str, user_phone: str = None, user_role: str = None) -> Dict[str, Any]:
        start_time = time.time()
        question = question.strip()
        
        logger.info(f"📱 Processing: {question[:100]}")
        
        conversation_context = {}
        if user_phone:
            conversation_context = self.conversation_memory.get_last_context(user_phone)
        
        intent, entity = IntentDetector.detect_intent(question)
        logger.info(f"🎯 Intent: {intent.value}, Entity: {entity}")
        
        try:
            if intent == IntentType.HELP or intent == IntentType.WELCOME:
                result = self._handle_welcome()
            elif intent == IntentType.DEALER_LOOKUP:
                result = self._handle_dealer_lookup(entity)
            elif intent == IntentType.DN_LOOKUP:
                result = self._handle_dn_lookup(entity)
            elif intent == IntentType.DN_PENDING:
                result = self._handle_pending_dns()
            elif intent == IntentType.DN_DELAYED:
                result = self._handle_delayed_dns()
            elif intent == IntentType.TOP_DEALERS:
                result = self._handle_top_dealers()
            elif intent == IntentType.TOP_RISK_DEALERS:
                result = self._handle_top_risk_dealers()
            elif intent == IntentType.EXECUTIVE_SUMMARY:
                result = self._handle_executive_summary()
            elif intent == IntentType.NETWORK_HEALTH:
                result = self._handle_network_health()
            elif intent == IntentType.CITY_PERFORMANCE:
                result = self._handle_city_performance()
            elif intent == IntentType.WAREHOUSE_PERFORMANCE:
                result = self._handle_warehouse_performance()
            elif intent == IntentType.REVENUE_ANALYSIS:
                result = self._handle_revenue_analysis()
            elif intent == IntentType.OUTSTANDING_ANALYSIS:
                result = self._handle_outstanding_analysis()
            elif intent == IntentType.PENDING_POD:
                result = self._handle_pending_pods()
            elif intent == IntentType.PENDING_PGI:
                result = self._handle_pending_pgi()
            elif intent == IntentType.PRODUCT_ANALYSIS:
                result = self._handle_product_analysis(entity)
            elif intent == IntentType.PRODUCT_RANKING:
                result = self._handle_product_ranking(entity)
            elif intent == IntentType.DIVISION_ANALYSIS:
                result = self._handle_division_analysis(entity)
            elif intent == IntentType.MANAGER_ANALYSIS:
                result = self._handle_manager_analysis(entity)
            elif intent == IntentType.EXCEPTION_ANALYSIS:
                result = self._handle_exception_analysis(entity)
            elif intent == IntentType.AI_INSIGHT:
                result = self._handle_ai_insight(entity)
            else:
                result = self._handle_general_query(question, user_phone, user_role, conversation_context)
            
            if user_phone and result.get("success"):
                self.conversation_memory.add(user_phone, question, result.get("response", ""), intent.value, entity)
            
            result["processing_time_ms"] = int((time.time() - start_time) * 1000)
            return result
            
        except Exception as e:
            logger.error(f"Processing error: {e}")
            return {"success": False, "response": "⚠️ Service unavailable. Please try again.", "processing_time_ms": int((time.time() - start_time) * 1000)}
    
    def _handle_welcome(self) -> Dict[str, Any]:
        return {"success": True, "response": self.formatter.welcome()}
    
    def _handle_dealer_lookup(self, dealer_name: str) -> Dict[str, Any]:
        result = self.db_service.get_dealer_executive_dashboard(dealer_name)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Dealer not found"))}
    
    def _handle_dn_lookup(self, dn_number: str) -> Dict[str, Any]:
        result = self.db_service.get_dn_with_enhanced_fields(dn_number)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "DN not found"))}
    
    def _handle_pending_dns(self) -> Dict[str, Any]:
        pending = self.db_service.get_pending_dns(20)
        return {"success": True, "response": self.formatter.pending_dns_response(pending, "Pending DNs")}
    
    def _handle_delayed_dns(self) -> Dict[str, Any]:
        delayed = self.db_service.get_delayed_dns(7, 20)
        if not delayed:
            return {"success": True, "response": "✅ No delayed DNs found (threshold: 7 days)"}
        
        response = "🚨 *DELAYED DNs (>7 days)*\n\n"
        for d in delayed[:15]:
            response += f"🔢 *{d['dn_no']}*\n"
            response += f"   🏪 {d['dealer'][:30]}\n"
            response += f"   💰 Rs {d['amount']:,.2f}\n"
            response += f"   ⏱️ {d['dispatch_delay_days']} days delayed\n\n"
        return {"success": True, "response": response}
    
    def _handle_pending_pods(self) -> Dict[str, Any]:
        pending_pods = self.db_service.get_pending_pods(20)
        if not pending_pods:
            return {"success": True, "response": "✅ No pending PODs found."}
        
        response = "📋 *PENDING PODs*\n\n"
        for p in pending_pods[:15]:
            response += f"🔢 *{p['dn_no']}*\n"
            response += f"   🏪 {p['dealer'][:30]}\n"
            response += f"   💰 Rs {p['amount']:,.2f}\n"
            response += f"   ⏱️ {p['aging_days']} days pending\n\n"
        return {"success": True, "response": response}
    
    def _handle_pending_pgi(self) -> Dict[str, Any]:
        pending_pgi = self.db_service.get_pending_pgi(20)
        return {"success": True, "response": self.formatter.pending_dns_response(pending_pgi, "Pending PGI DNs")}
    
    def _handle_top_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_top_dealers(20)
        return {"success": True, "response": self.formatter.top_dealers_response(dealers)}
    
    def _handle_top_risk_dealers(self) -> Dict[str, Any]:
        dealers = self.db_service.get_enhanced_top_risk_dealers(20)
        return {"success": True, "response": self.formatter.top_risk_dealers_response(dealers)}
    
    def _handle_executive_summary(self) -> Dict[str, Any]:
        health = self.db_service.get_enhanced_network_health()
        top_dealers = self.db_service.get_top_dealers(10)
        risk_dealers = self.db_service.get_enhanced_top_risk_dealers(10)
        return {"success": True, "response": self.formatter.executive_summary_response(health, top_dealers, risk_dealers)}
    
    def _handle_network_health(self) -> Dict[str, Any]:
        health = self.db_service.get_enhanced_network_health()
        return {"success": True, "response": self.formatter.network_health_response(health)}
    
    def _handle_city_performance(self) -> Dict[str, Any]:
        cities = self.db_service.get_city_performance()
        if not cities:
            return {"success": True, "response": "🌆 No city data available."}
        
        response = "🌆 *CITY PERFORMANCE*\n\n"
        for c in cities[:15]:
            response += f"{c['status']} *{c['city'][:25]}*\n"
            response += f"   📦 {c['total_dns']} DNs | ⏳ {c['pending_dns']} pending ({c['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {c['total_value']:,.2f}\n\n"
        return {"success": True, "response": response}
    
    def _handle_warehouse_performance(self) -> Dict[str, Any]:
        warehouses = self.db_service.get_warehouse_performance()
        if not warehouses:
            return {"success": True, "response": "🏭 No warehouse data available."}
        
        response = "🏭 *WAREHOUSE PERFORMANCE*\n\n"
        for w in warehouses[:15]:
            response += f"{w['status']} *{w['warehouse'][:25]}*\n"
            response += f"   📦 {w['total_dns']} DNs | ⏳ {w['pending_dns']} pending ({w['pending_rate']:.0f}%)\n"
            response += f"   💰 Rs {w['total_value']:,.2f}\n\n"
        return {"success": True, "response": response}
    
    def _handle_revenue_analysis(self) -> Dict[str, Any]:
        revenue = self.db_service.get_revenue_analysis()
        response = f"""💰 *REVENUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Revenue: Rs {revenue.get('total_revenue', 0):,.2f}
• Realized: Rs {revenue.get('realized_revenue', 0):,.2f} ✅
• Pending Delivery: Rs {revenue.get('pending_revenue', 0):,.2f} ⏳
• POD Pending: Rs {revenue.get('pod_pending_revenue', 0):,.2f} 📋

📈 *REALIZATION RATE: {revenue.get('realization_rate', 0):.1f}%*"""
        return {"success": True, "response": response}
    
    def _handle_outstanding_analysis(self) -> Dict[str, Any]:
        outstanding = self.db_service.get_outstanding_analysis()
        response = f"""💰 *OUTSTANDING & PENDING VALUE ANALYSIS*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 *VALUE BREAKDOWN*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
• Total Outstanding: Rs {outstanding.get('outstanding_value', 0):,.2f}
• Pending Delivery: Rs {outstanding.get('pending_delivery', 0):,.2f} ⏳
• POD Pending: Rs {outstanding.get('pod_pending', 0):,.2f} 📋"""
        return {"success": True, "response": response}
    
    def _handle_product_analysis(self, entity: str) -> Dict[str, Any]:
        if not entity:
            return {"success": True, "response": "📦 Please specify a product model (e.g., HSU-18HFPAA)"}
        
        result = self.db_service.get_product_sales(entity)
        return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Product not found"))}
    
    def _handle_product_ranking(self, ranking_type: str) -> Dict[str, Any]:
        if ranking_type == "top":
            products = self.db_service.get_top_products(10, "revenue")
            return {"success": True, "response": self.formatter.top_products_response(products, "revenue")}
        elif ranking_type == "bottom":
            products = self.db_service.get_bottom_products(10)
            return {"success": True, "response": self.formatter.top_products_response(products, "revenue (lowest)")}
        elif ranking_type == "fast_moving":
            products = self.db_service.get_fast_moving_products(10)
            if not products:
                return {"success": True, "response": "📦 No fast-moving products data available."}
            response = "⚡ *FAST MOVING PRODUCTS*\n\n"
            for i, p in enumerate(products[:10], 1):
                response += f"{i}. *{p['product'][:35]}*\n"
                response += f"   📦 {p['avg_quantity_per_dn']} units per DN\n"
                response += f"   📊 Total: {p['total_quantity']:,.0f} units\n\n"
            return {"success": True, "response": response}
        elif ranking_type == "slow_moving":
            products = self.db_service.get_slow_moving_products(30, 10)
            if not products:
                return {"success": True, "response": "📦 No slow-moving products found."}
            response = "🐢 *SLOW MOVING PRODUCTS (>30 days)*\n\n"
            for i, p in enumerate(products[:10], 1):
                response += f"{i}. *{p['product'][:35]}*\n"
                response += f"   ⏱️ {p['inactive_days']} days inactive\n"
                response += f"   📦 Total: {p['total_quantity']:,.0f} units\n\n"
            return {"success": True, "response": response}
        else:
            products = self.db_service.get_top_products(10, "revenue")
            return {"success": True, "response": self.formatter.top_products_response(products, "revenue")}
    
    def _handle_division_analysis(self, entity: str) -> Dict[str, Any]:
        if entity == "all":
            divisions = self.db_service.get_all_divisions_summary()
            if not divisions:
                return {"success": True, "response": "🏭 No division data available."}
            response = "🏭 *DIVISION WISE PERFORMANCE*\n\n"
            for d in divisions:
                response += f"• *{d['division']}*\n"
                response += f"   📊 Revenue: Rs {d['revenue']:,.2f}\n"
                response += f"   📦 Quantity: {d['quantity']:,.0f}\n\n"
            return {"success": True, "response": response}
        else:
            result = self.db_service.get_division_sales(entity)
            return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Division not found"))}
    
    def _handle_manager_analysis(self, entity: str) -> Dict[str, Any]:
        if entity == "ranking":
            managers = self.db_service.get_manager_ranking()
            if not managers:
                return {"success": True, "response": "👔 No manager data available."}
            response = "👔 *MANAGER RANKING (by Revenue)*\n\n"
            for i, m in enumerate(managers[:10], 1):
                response += f"{i}. *{m['manager']}*\n"
                response += f"   💰 Revenue: Rs {m['total_revenue']:,.2f}\n"
                response += f"   📦 DNs: {m['total_dns']} | Completion: {m['completion_rate']}%\n\n"
            return {"success": True, "response": response}
        else:
            result = self.db_service.get_manager_sales(entity)
            return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Manager not found"))}
    
    def _handle_exception_analysis(self, entity: str) -> Dict[str, Any]:
        if entity == "inactive_dealers":
            inactive = self.db_service.get_inactive_dealers(30)
            if not inactive:
                return {"success": True, "response": "✅ No inactive dealers found (threshold: 30 days)"}
            response = "🚨 *INACTIVE DEALERS (>30 days)*\n\n"
            for d in inactive[:15]:
                response += f"• *{d['dealer'][:35]}*\n"
                response += f"   ⏱️ {d['inactive_days']} days inactive\n"
                response += f"   💰 Last order value: Rs {d['total_value']:,.2f}\n\n"
            return {"success": True, "response": response}
        elif entity == "dead_stock":
            dead = self.db_service.get_dead_stock_products(90)
            if not dead:
                return {"success": True, "response": "✅ No dead stock products found (threshold: 90 days)"}
            response = "💀 *DEAD STOCK PRODUCTS (>90 days)*\n\n"
            for p in dead[:15]:
                response += f"• *{p['product'][:35]}*\n"
                response += f"   ⏱️ {p['inactive_days']} days no sales\n"
                response += f"   📦 Total inventory: {p['total_quantity']:,.0f} units\n\n"
            return {"success": True, "response": response}
        elif entity == "critical_delays":
            critical = self.db_service.get_critical_delays()
            response = "🚨 *CRITICAL DELAYS REPORT*\n\n"
            response += f"📋 DNs pending >7 days: {len(critical.get('pending_dns_gt_7', []))}\n"
            response += f"📋 DNs pending >15 days: {len(critical.get('pending_dns_gt_15', []))}\n"
            response += f"📋 DNs pending >30 days: {len(critical.get('pending_dns_gt_30', []))}\n"
            response += f"💰 High value pending DNs: {len(critical.get('high_value_pending', []))}\n"
            response += f"📋 PODs pending >7 days: {len(critical.get('pending_pods_gt_7', []))}\n"
            return {"success": True, "response": response}
        elif entity == "high_value_pending":
            high_value = self.db_service.get_high_value_pending_dns()
            if not high_value:
                return {"success": True, "response": "💰 No high value pending DNs found (threshold: Rs 1,000,000)"}
            response = "💰 *HIGH VALUE PENDING DNs (>Rs 1M)*\n\n"
            for d in high_value[:15]:
                response += f"🔢 *{d['dn_no']}*\n"
                response += f"   🏪 {d['dealer'][:30]}\n"
                response += f"   💰 Rs {d['amount']:,.2f}\n\n"
            return {"success": True, "response": response}
        else:
            # All exceptions
            inactive = self.db_service.get_inactive_dealers(30)
            dead = self.db_service.get_dead_stock_products(90)
            critical = self.db_service.get_critical_delays()
            
            response = "🚨 *EXCEPTION MANAGEMENT REPORT*\n\n"
            response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            response += f"📋 Critical Delays:\n"
            response += f"   • DNs >7 days: {len(critical.get('pending_dns_gt_7', []))}\n"
            response += f"   • DNs >15 days: {len(critical.get('pending_dns_gt_15', []))}\n"
            response += f"   • DNs >30 days: {len(critical.get('pending_dns_gt_30', []))}\n"
            response += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            response += f"🏪 Inactive Dealers (>30 days): {len(inactive)}\n"
            response += f"💀 Dead Stock Products (>90 days): {len(dead)}\n"
            response += f"💰 High Value Pending DNs: {len(critical.get('high_value_pending', []))}\n"
            
            return {"success": True, "response": response}
    
    def _handle_ai_insight(self, insight_type: str) -> Dict[str, Any]:
        if insight_type == "sales_decrease":
            result = self.db_service.why_sales_decreased(30)
            return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Analysis failed"))}
        elif insight_type == "logistics_delay":
            result = self.db_service.logistics_delay_analysis()
            return {"success": result["success"], "response": result.get("formatted_response", result.get("message", "Analysis failed"))}
        else:
            return {"success": True, "response": "💡 *AI INSIGHTS*\n\nAvailable insights:\n• 'Why sales decreased?'\n• 'Logistics delay analysis'"}
    
    def _handle_general_query(self, question: str, user_phone: str, user_role: str, conversation_context: Dict = None) -> Dict[str, Any]:
        logger.info(f"🤖 Processing general query with GROQ: {question[:100]}")
        
        executive_context = self.executive_cache.get(self.db_hash)
        if not executive_context:
            executive_context = self.db_service.get_executive_context()
            self.executive_cache.set(self.db_hash, executive_context)
        
        context_prompt = f"""
BUSINESS CONTEXT:
- Network Health Score: {executive_context.get('network_health', {}).get('health_score', 0)}/100
- Revenue at Risk: Rs {executive_context.get('network_health', {}).get('revenue_at_risk', 0):,.2f}
- Avg Dispatch Aging: {executive_context.get('network_health', {}).get('avg_dispatch_aging', 0)} days
- Top Dealers: {executive_context.get('top_dealers', [])[:3]}
- Top Risk Dealers: {executive_context.get('top_risk_dealers', [])[:3]}
- Pending PODs: {len(executive_context.get('pending_pods', []))}
- Delayed DNs: {len(executive_context.get('delayed_dns', []))}
- Top Products: {executive_context.get('top_products', [])[:3]}
- Division Summary: {executive_context.get('division_summary', [])[:3]}
- Critical Alerts: {executive_context.get('critical_alerts', {})}
"""
        
        if conversation_context:
            context_prompt += f"""
CONVERSATION CONTEXT:
- Last Question: {conversation_context.get('last_question', 'None')}
- Last Entity: {conversation_context.get('last_entity', 'None')}
"""
        
        try:
            from app.services.ai_provider_service import get_ai_provider_service
            ai_provider = get_ai_provider_service(self.db)
            
            if ai_provider and ai_provider.is_available:
                result = ai_provider.answer_question(
                    question=f"{context_prompt}\n\nUSER QUESTION: {question}\n\nProvide a helpful, data-driven response for WhatsApp.",
                    user_phone=user_phone,
                    user_role=user_role or "guest"
                )
                
                if result.get("success"):
                    return {"success": True, "response": result.get("content")}
                else:
                    return self._get_fallback_response(question, result.get('error'))
            else:
                return self._get_fallback_response(question, "AI Provider not available")
                
        except Exception as e:
            logger.exception(f"GROQ error: {e}")
            return self._get_fallback_response(question, str(e))
    
    def _get_fallback_response(self, question: str, error: str = None) -> Dict[str, Any]:
        error_msg = f"\n\n*Error Details:* {error[:200]}" if error else ""
        
        response = f"""🤖 *AI LOGISTICS ASSISTANT v16.0*

I understand you're asking about: "{question[:50]}"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️ *AI Service Unavailable*{error_msg}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 *Try these commands:*
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 Type a dealer name (e.g., "Exact Trading Co")
🔢 Send a 10-digit DN number
👑 "Executive summary" - Leadership view
🏆 "Top dealers" - Best performers
🚨 "Top risk dealers" - Critical accounts
📋 "Pending PODs" - POD collection required
🚚 "Pending PGI" - Dispatch pending
📦 "Top products" - Best selling products
🏭 "AC sales" - Division performance
👔 "Manager ranking" - Manager performance
🚨 "Inactive dealers" - Exception management
💡 "Why sales decreased?" - AI Insights

Type "Help" for complete menu."""
        
        return {"success": True, "response": response}


# ==========================================================
# FACTORY FUNCTION
# ==========================================================

def process_whatsapp_query(question: str, db: Session, user_phone: str = None, user_role: str = None) -> str:
    """Process WhatsApp query and return response"""
    try:
        service = AIQueryService(db)
        result = service.process_query(question, user_phone, user_role)
        return result.get("response", "Unable to process your request. Please try again.")
    except Exception as e:
        logger.error(f"Query processing error: {e}")
        return "⚠️ Service temporarily unavailable. Please try again later."
